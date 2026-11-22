"""Tests for MEA code=1008 dead-session detection and recovery (issue #104).

On BYD's MEA cluster, single-session enforcement causes vehicle endpoints to
return ``code=1008`` (``BydServiceBusyError``) for invalidated sessions while
account endpoints (``/app/account/getAllListByUserId``) still return a session
expiry code (e.g. ``1011`` / ``1002``).

On read-only vehicle calls, ``BydClient._call_with_reauth`` probes
``fetch_vehicle_list`` (rate-limited) to distinguish a dead session from
genuine backend busyness. Write/command endpoints and ``get_vehicles()`` must
never probe or replay.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import (
    BydApiError,
    BydAuthenticationError,
    BydClient,
    BydConfig,
    BydServerTimeError,
    BydServiceBusyError,
    BydSessionExpiredError,
    BydTokenRefreshError,
    BydTransportError,
)
from pybyd._constants import SESSION_EXPIRED_CODES
from pybyd.models.charging import ChargingStatus
from pybyd.models.hvac import HvacStatus
from pybyd.models.latest_config import VehicleCapabilities, VehicleLatestConfig
from pybyd.models.smart_charging import SmartChargingSchedule
from pybyd.models.vehicle import EnergyType, Vehicle
from pybyd.session import Session

_VIN = "TESTVIN0000000001"


def _make_client() -> BydClient:
    cfg = BydConfig(
        username="user@example.com",
        password="secret",
        control_pin="123456",
        mqtt_enabled=False,
    )
    client = BydClient(cfg)
    client._session = Session(
        user_id="u1",
        sign_token="a" * 32,
        encry_token="b" * 32,
        refresh_token="rt1",
    )
    client._transport = MagicMock()
    return client


def _busy(endpoint: str = "/control/getStatusNow", code: str = "1008") -> BydServiceBusyError:
    return BydServiceBusyError(f"{endpoint} failed: code={code} message=Service error", code=code, endpoint=endpoint)


@pytest.mark.asyncio
async def test_busy_1008_probe_succeeds_surfaces_original_busy_error() -> None:
    """When the account probe succeeds, the session is valid — re-raise original 1008."""
    client = _make_client()
    orig_session = client._session
    original_exc = _busy()
    calls = 0

    async def read_call() -> str:
        nonlocal calls
        calls += 1
        raise original_exc

    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(return_value=[])) as mock_probe,
        patch.object(client, "ensure_session", new=AsyncMock()) as mock_ensure,
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(read_call, probe_busy_session=True)

    assert exc_info.value is original_exc
    assert calls == 1
    mock_probe.assert_awaited_once()
    mock_ensure.assert_not_awaited()
    assert client._session is orig_session


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_code", sorted(SESSION_EXPIRED_CODES))
async def test_busy_1008_probe_session_expired_relogins_and_retries_once(expired_code: str) -> None:
    """Dead-session 1008 (probe raises BydSessionExpiredError) re-authenticates and retries once."""
    client = _make_client()
    fresh_session = Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
    calls = 0

    async def read_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _busy()
        return "recovered"

    async def fake_ensure() -> Session:
        client._session = fresh_session
        return fresh_session

    probe_err = BydSessionExpiredError("expired", code=expired_code, endpoint="/app/account/getAllListByUserId")
    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)) as mock_probe,
        patch.object(client, "ensure_session", new=AsyncMock(side_effect=fake_ensure)) as mock_ensure,
    ):
        result = await client._call_with_reauth(read_call, probe_busy_session=True)

    assert result == "recovered"
    assert calls == 2
    mock_probe.assert_awaited_once()
    mock_ensure.assert_awaited_once()
    assert client._session is fresh_session


@pytest.mark.asyncio
async def test_busy_1008_probe_token_refresh_refreshes_and_retries_once() -> None:
    """When the probe returns 1030 (BydTokenRefreshError), refresh tokens and retry once."""
    client = _make_client()
    calls = 0

    async def read_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _busy()
        return "refreshed-ok"

    probe_err = BydTokenRefreshError("1030", code="1030", endpoint="/app/account/getAllListByUserId")
    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)) as mock_probe,
        patch.object(client, "_refresh_or_relogin", new=AsyncMock()) as mock_refresh,
    ):
        result = await client._call_with_reauth(read_call, probe_busy_session=True)

    assert result == "refreshed-ok"
    assert calls == 2
    mock_probe.assert_awaited_once()
    mock_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_1008_reauth_cooldown_throttles_repeat_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second dead-session 1008 inside _BUSY_REAUTH_COOLDOWN_S does not probe or re-login."""
    client = _make_client()
    now = 1000.0
    monkeypatch.setattr("pybyd.client.time.monotonic", lambda: now)

    probe_err = BydSessionExpiredError("kicked", code="1011", endpoint="/app/account/getAllListByUserId")
    mock_probe = AsyncMock(side_effect=probe_err)
    mock_ensure = AsyncMock(
        side_effect=lambda: setattr(
            client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
        )
        or client._session
    )

    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_probe),
        patch.object(client, "ensure_session", new=mock_ensure),
    ):
        # First 1008 at t=1000 -> probes, re-authenticates, retries and succeeds.
        attempt = 0

        async def first_call() -> str:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise _busy()
            return "ok-1"

        assert await client._call_with_reauth(first_call, probe_busy_session=True) == "ok-1"
        assert mock_probe.await_count == 1
        assert mock_ensure.await_count == 1

        # Second 1008 at t=1120 (past 60s probe cooldown, inside 600s reauth cooldown) -> throttled!
        now = 1000.0 + client._BUSY_PROBE_COOLDOWN_S + 60.0
        second_exc = _busy()
        with pytest.raises(BydServiceBusyError) as exc_info:
            await client._call_with_reauth(AsyncMock(side_effect=second_exc), probe_busy_session=True)
        assert exc_info.value is second_exc
        assert mock_probe.await_count == 1
        assert mock_ensure.await_count == 1

        # Third 1008 after _BUSY_REAUTH_COOLDOWN_S elapses -> probes and recovers again.
        now = 1000.0 + client._BUSY_REAUTH_COOLDOWN_S + 1.0
        attempt = 0

        async def third_call() -> str:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise _busy()
            return "ok-3"

        assert await client._call_with_reauth(third_call, probe_busy_session=True) == "ok-3"
        assert mock_probe.await_count == 2
        assert mock_ensure.await_count == 2


@pytest.mark.asyncio
async def test_busy_1008_probe_cooldown_throttles_busy_bursts(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the session is healthy, repeated 1008s inside _BUSY_PROBE_COOLDOWN_S only probe once."""
    client = _make_client()
    now = 500.0
    monkeypatch.setattr("pybyd.client.time.monotonic", lambda: now)

    mock_probe = AsyncMock(return_value=[])
    with patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_probe):
        # First busy 1008 -> probes once and sees healthy session.
        with pytest.raises(BydServiceBusyError):
            await client._call_with_reauth(AsyncMock(side_effect=_busy()), probe_busy_session=True)
        assert mock_probe.await_count == 1

        # Second busy 1008 10s later -> probe cooldown suppresses redundant probe.
        now += 10.0
        with pytest.raises(BydServiceBusyError):
            await client._call_with_reauth(AsyncMock(side_effect=_busy()), probe_busy_session=True)
        assert mock_probe.await_count == 1

        # After probe cooldown expires, a new 1008 probes again.
        now += client._BUSY_PROBE_COOLDOWN_S
        with pytest.raises(BydServiceBusyError):
            await client._call_with_reauth(AsyncMock(side_effect=_busy()), probe_busy_session=True)
        assert mock_probe.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_error",
    [
        BydServiceBusyError("busy", code="1008", endpoint="/app/account/getAllListByUserId"),
        BydTransportError("timeout", status_code=503, endpoint="/app/account/getAllListByUserId"),
        BydApiError("data error", code="1005", endpoint="/app/account/getAllListByUserId"),
        BydServerTimeError("clock", code="1003", endpoint="/app/account/getAllListByUserId"),
    ],
)
async def test_busy_1008_probe_non_session_failure_surfaces_original_1008(probe_error: Exception) -> None:
    """Non-session errors from the probe fall back to the original BydServiceBusyError."""
    client = _make_client()
    original_exc = _busy()

    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_error)) as mock_probe,
        patch.object(client, "ensure_session", new=AsyncMock()) as mock_ensure,
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(AsyncMock(side_effect=original_exc), probe_busy_session=True)

    assert exc_info.value is original_exc
    mock_probe.assert_awaited_once()
    mock_ensure.assert_not_awaited()
    # Non-session probe failure does not trip the re-auth cooldown.
    assert client._busy_reauth_last_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_exc", "recovery_method", "recovery_err"),
    [
        (
            BydSessionExpiredError("kicked", code="1011", endpoint="/app/account/getAllListByUserId"),
            "ensure_session",
            BydAuthenticationError("login failed", code="2001", endpoint="/app/account/login"),
        ),
        (
            BydTokenRefreshError("1030", code="1030", endpoint="/app/account/getAllListByUserId"),
            "_refresh_or_relogin",
            BydTransportError("network down", endpoint="/app/token/refresh"),
        ),
    ],
)
async def test_busy_1008_reauth_failure_surfaces_original_1008(
    probe_exc: Exception,
    recovery_method: str,
    recovery_err: Exception,
) -> None:
    """If re-login / refresh fails after a dead-session probe, re-raise the original 1008."""
    client = _make_client()
    original_exc = _busy()
    calls = 0

    async def read_call() -> str:
        nonlocal calls
        calls += 1
        raise original_exc

    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_exc)),
        patch.object(client, recovery_method, new=AsyncMock(side_effect=recovery_err)),
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(read_call, probe_busy_session=True)

    assert exc_info.value is original_exc
    assert calls == 1
    assert client._busy_reauth_last_at is not None


@pytest.mark.asyncio
async def test_busy_1008_retry_failure_propagates_without_looping() -> None:
    """If the single retry after re-authentication also raises 1008, propagate it directly."""
    client = _make_client()
    first_exc = _busy("/control/getStatusNow")
    second_exc = _busy("/control/getStatusNow")
    calls = 0

    async def always_busy() -> str:
        nonlocal calls
        calls += 1
        raise first_exc if calls == 1 else second_exc

    probe_err = BydSessionExpiredError("kicked", code="1011", endpoint="/app/account/getAllListByUserId")
    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)) as mock_probe,
        patch.object(client, "ensure_session", new=AsyncMock()) as mock_ensure,
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(always_busy, probe_busy_session=True)

    assert exc_info.value is second_exc
    assert calls == 2
    mock_probe.assert_awaited_once()
    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_vehicles_1008_never_probes_or_recurses() -> None:
    """get_vehicles() itself must never trigger the 1008 probe (prevents recursion)."""
    client = _make_client()
    busy_exc = _busy("/app/account/getAllListByUserId")
    mock_fetch = AsyncMock(side_effect=busy_exc)

    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_fetch),
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client.get_vehicles()

    assert exc_info.value is busy_exc
    mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_and_command_paths_never_probe_or_replay_on_1008() -> None:
    """Write/command endpoints must never probe or replay when receiving 1008."""
    client = _make_client()
    client._commands_enabled = True
    latest = VehicleLatestConfig.model_validate({"cfFixedList": [{"functionNo": "1005"}, {"functionNo": "1012"}]})
    client._vehicle_capabilities[_VIN] = VehicleCapabilities.from_latest_config(_VIN, latest)
    client._vehicle_metadata_by_vin[_VIN] = Vehicle.model_validate({"vin": _VIN, "energyType": 1})

    mock_probe = AsyncMock(side_effect=BydSessionExpiredError("kicked", code="1011"))
    with patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_probe):
        # 1. Default _call_with_reauth does not probe.
        calls = 0

        async def default_call() -> None:
            nonlocal calls
            calls += 1
            raise _busy()

        with pytest.raises(BydServiceBusyError):
            await client._call_with_reauth(default_call)
        assert calls == 1

        # 2. Remote control (lock).
        mock_remote = AsyncMock(side_effect=_busy("/control/remoteControl"))
        with (
            patch("pybyd.client._control_api.poll_remote_control", new=mock_remote),
            pytest.raises(BydServiceBusyError),
        ):
            await client.lock(_VIN)
        mock_remote.assert_awaited_once()

        # 3. Simple write endpoints (toggle_smart_charging, rename_vehicle, set_push_state, verify_command_access).
        mock_toggle = AsyncMock(side_effect=_busy("/control/smartCharge/changeChargeStatue"))
        with (
            patch("pybyd.client._smart_api.toggle_smart_charging", new=mock_toggle),
            pytest.raises(BydServiceBusyError),
        ):
            await client.toggle_smart_charging(_VIN, enable=True)
        mock_toggle.assert_awaited_once()

        mock_rename = AsyncMock(side_effect=_busy("/vehicle/rename"))
        with (
            patch("pybyd.client._settings_api.rename_vehicle", new=mock_rename),
            pytest.raises(BydServiceBusyError),
        ):
            await client.rename_vehicle(_VIN, name="Seal")
        mock_rename.assert_awaited_once()

        mock_push = AsyncMock(side_effect=_busy("/app/push/set"))
        with (
            patch("pybyd.client._push_api.set_push_state", new=mock_push),
            pytest.raises(BydServiceBusyError),
        ):
            await client.set_push_state(_VIN, enable=True)
        mock_push.assert_awaited_once()

        mock_verify = AsyncMock(side_effect=_busy("/vehicle/verifyControlPassword"))
        with (
            patch("pybyd.client._control_api.verify_control_password", new=mock_verify),
            pytest.raises(BydServiceBusyError),
        ):
            await client.verify_command_access(_VIN)
        mock_verify.assert_awaited_once()

        # 4. Trigger-and-poll write (start_charging).
        mock_trigger = AsyncMock(side_effect=_busy("/control/smartCharge/changeChargeStatue"))
        with (
            patch.object(client, "_trigger_and_poll", new=mock_trigger),
            pytest.raises(BydServiceBusyError),
        ):
            await client.start_charging(_VIN)
        mock_trigger.assert_awaited_once()

        # 5. Standby plan run (write).
        mock_run_trigger = AsyncMock(side_effect=_busy("/control/standby/send"))
        with (
            patch.object(client, "_trigger_and_poll", new=mock_run_trigger),
            pytest.raises(BydServiceBusyError),
        ):
            await client.run_standby_plan(_VIN, "1")
        mock_run_trigger.assert_awaited_once()

    # None of the writes above should ever have called the account probe.
    mock_probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_vehicle_endpoints_recover_from_dead_session_1008() -> None:
    """Read-only vehicle endpoints (realtime, GPS, HVAC, charging) recover via probe + re-login."""
    probe_err = BydSessionExpiredError("kicked", code="1011", endpoint="/app/account/getAllListByUserId")

    # 1. get_hvac_status (/control/getStatusNow)
    client = _make_client()
    hvac_ok = HvacStatus.model_validate({"status": 1})
    mock_hvac = AsyncMock(side_effect=[_busy("/control/getStatusNow"), hvac_ok])
    with (
        patch("pybyd.client._hvac_api.fetch_hvac_status", new=mock_hvac),
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)),
        patch.object(
            client,
            "login",
            new=AsyncMock(
                side_effect=lambda: setattr(
                    client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
                )
            ),
        ),
    ):
        assert await client.get_hvac_status(_VIN) == hvac_ok
    assert mock_hvac.await_count == 2

    # 2. get_charging_status (/control/smartCharge/homePage)
    client = _make_client()
    charging_ok = ChargingStatus.model_validate({"elecPercent": 80})
    mock_charging = AsyncMock(side_effect=[_busy("/control/smartCharge/homePage"), charging_ok])
    with (
        patch("pybyd.client._charging_api.fetch_charging_status", new=mock_charging),
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)),
        patch.object(
            client,
            "login",
            new=AsyncMock(
                side_effect=lambda: setattr(
                    client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
                )
            ),
        ),
    ):
        assert await client.get_charging_status(_VIN) == charging_ok
    assert mock_charging.await_count == 2

    # 3. get_charging_schedule (/control/smartCharge/homePage)
    client = _make_client()
    schedule_ok = SmartChargingSchedule.model_validate({"vin": _VIN})
    mock_sched = AsyncMock(side_effect=[_busy("/control/smartCharge/homePage"), schedule_ok])
    with (
        patch("pybyd.client._smart_api.fetch_charging_schedule", new=mock_sched),
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)),
        patch.object(
            client,
            "login",
            new=AsyncMock(
                side_effect=lambda: setattr(
                    client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
                )
            ),
        ),
    ):
        assert await client.get_charging_schedule(_VIN) == schedule_ok
    assert mock_sched.await_count == 2

    # 4. get_vehicle_realtime (/vehicleInfo/vehicle/vehicleRealTimeRequest)
    client = _make_client()
    client._vehicle_metadata_by_vin[_VIN] = Vehicle.model_validate({"vin": _VIN, "energyType": int(EnergyType.EV)})
    mock_rt = AsyncMock(
        side_effect=[
            _busy("/vehicleInfo/vehicle/vehicleRealTimeRequest"),
            ({"onlineState": 1, "elecPercent": 77, "time": 1_700_000_000}, None),
        ]
    )
    with (
        patch("pybyd.client._realtime_api.fetch_realtime_endpoint", new=mock_rt),
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)),
        patch.object(
            client,
            "login",
            new=AsyncMock(
                side_effect=lambda: setattr(
                    client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
                )
            ),
        ),
    ):
        rt = await client.get_vehicle_realtime(_VIN)
    assert rt.elec_percent == 77
    assert mock_rt.await_count == 2

    # 5. get_gps_info (/control/getGpsInfo)
    client = _make_client()
    mock_gps = AsyncMock(
        side_effect=[
            _busy("/control/getGpsInfo"),
            ({"data": {"latitude": 52.0, "longitude": 4.0}}, None),
        ]
    )
    with (
        patch("pybyd.client._gps_api.fetch_gps_endpoint", new=mock_gps),
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_err)),
        patch.object(
            client,
            "login",
            new=AsyncMock(
                side_effect=lambda: setattr(
                    client, "_session", Session(user_id="u1", sign_token="c" * 32, encry_token="d" * 32)
                )
            ),
        ),
    ):
        gps = await client.get_gps_info(_VIN)
    assert gps.latitude == 52.0
    assert mock_gps.await_count == 2


@pytest.mark.asyncio
async def test_busy_1008_concurrent_session_refresh_skips_duplicate_relogin() -> None:
    """If another task replaced self._session while our probe was in flight, retry without re-logging in."""
    client = _make_client()
    fresh_session = Session(user_id="u1", sign_token="f" * 32, encry_token="f" * 32)
    calls = 0

    async def read_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _busy()
        return "used-concurrent-session"

    async def probe_while_concurrent_refresh(*_args: object, **_kwargs: object) -> list[Vehicle]:
        client._session = fresh_session
        raise BydSessionExpiredError("kicked", code="1011", endpoint="/app/account/getAllListByUserId")

    with (
        patch(
            "pybyd.client._vehicle_api.fetch_vehicle_list", new=AsyncMock(side_effect=probe_while_concurrent_refresh)
        ),
        patch.object(client, "ensure_session", new=AsyncMock()) as mock_ensure,
    ):
        result = await client._call_with_reauth(read_call, probe_busy_session=True)

    assert result == "used-concurrent-session"
    assert calls == 2
    mock_ensure.assert_not_awaited()
    assert client._session is fresh_session
    assert client._busy_reauth_last_at is None


@pytest.mark.asyncio
async def test_busy_1008_non_1008_code_or_missing_session_skips_probe() -> None:
    """Non-1008 BydServiceBusyError or missing session/transport skips probing cleanly."""
    client = _make_client()
    non_1008 = _busy(code="9999")
    mock_probe = AsyncMock()
    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_probe),
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(AsyncMock(side_effect=non_1008), probe_busy_session=True)
    assert exc_info.value is non_1008
    mock_probe.assert_not_awaited()

    client._session = None
    busy_1008 = _busy()
    with (
        patch("pybyd.client._vehicle_api.fetch_vehicle_list", new=mock_probe),
        pytest.raises(BydServiceBusyError) as exc_info,
    ):
        await client._call_with_reauth(AsyncMock(side_effect=busy_1008), probe_busy_session=True)
    assert exc_info.value is busy_1008
    mock_probe.assert_not_awaited()
