"""Tests for granular guest permissions (rangeDetailList & empowerDetailByVin)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import (
    BydCar,
    BydClient,
    BydConfig,
    BydDataUnavailableError,
    BydEndpointNotSupportedError,
    EmpowerDetail,
)
from pybyd._api.gps import fetch_gps_endpoint
from pybyd._api.vehicle import fetch_empower_detail_by_vin
from pybyd._crypto.aes import aes_encrypt_hex
from pybyd.models.command_gating import evaluate_command_gate
from pybyd.models.control import RemoteCommand
from pybyd.models.latest_config import VehicleCapabilities, VehicleLatestConfig
from pybyd.models.vehicle import Vehicle
from pybyd.session import Session

_RANGE_TREE = [
    {
        "code": "2",
        "name": "Keys & Control",
        "childList": [{"code": "21", "name": "Basic control", "childList": []}],
    },
    {
        "code": "4",
        "name": "More control",
        "childList": [{"code": "41", "name": "Scheduled charging", "childList": []}],
    },
]


def _caps_all() -> VehicleCapabilities:
    latest = VehicleLatestConfig.model_validate(
        {
            "cfFixedList": [
                {"functionNo": "1001", "code": "AC"},
                {"functionNo": "1005", "code": "LOCK"},
                {"functionNo": "1026", "code": "WINDOW"},
                {"functionNo": "10020004", "code": "UPWINDOWSREMOTELY"},
                {"functionNo": "10020001", "code": "LOCKDOOR"},
                {"functionNo": "10100001", "code": "BATHEAT"},
                {"functionNo": "1012", "code": "RESERVATIONCHARGING"},
            ]
        }
    )
    return VehicleCapabilities.from_latest_config("TESTVIN", latest)


def test_vehicle_has_permission_owner_vs_guest() -> None:
    owner = Vehicle.model_validate({"vin": "TESTVIN", "empowerType": 2})
    guest_empty = Vehicle.model_validate({"vin": "TESTVIN", "empowerType": -1})
    guest = Vehicle.model_validate({"vin": "TESTVIN", "empowerType": -1, "rangeDetailList": _RANGE_TREE})

    assert owner.is_shared is False
    assert owner.has_permission("43") is True
    assert owner.has_permission("31") is True

    assert guest_empty.is_shared is True
    assert guest_empty.has_permission("21") is True
    assert guest_empty.has_permission("31") is True

    assert guest.is_shared is True
    assert guest.granted_permission_codes == frozenset({"2", "21", "4", "41"})
    assert guest.has_permission("21") is True
    assert guest.has_permission("41") is True
    assert guest.has_permission("43") is False
    assert guest.has_permission("31") is False


def test_command_gating_respects_guest_level2_permissions() -> None:
    caps = _caps_all()
    guest_perms = frozenset({"2", "21", "4", "41"})

    climate = evaluate_command_gate(
        RemoteCommand.START_CLIMATE,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert climate.supported is True

    charge = evaluate_command_gate(
        RemoteCommand.START_CHARGE,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert charge.supported is True

    bat_heat = evaluate_command_gate(
        RemoteCommand.BATTERY_HEAT,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert bat_heat.supported is False
    assert bat_heat.reason == "guest_permission_denied"


def test_command_gating_level34_allowlist_blocks_window_close_unless_granted() -> None:
    caps = _caps_all()
    # Level 3/4 allowlist grants climate ("1001") and lock ("1005"), omits window ("1026").
    guest_perms = frozenset({"2", "21", "1001", "1005"})

    lock_verdict = evaluate_command_gate(
        RemoteCommand.LOCK,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert lock_verdict.supported is True

    win_verdict = evaluate_command_gate(
        RemoteCommand.CLOSE_WINDOWS,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert win_verdict.supported is False
    assert win_verdict.reason == "function_no_missing"


@pytest.mark.asyncio
async def test_fetch_empower_detail_by_vin_parses_response() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = MagicMock(spec=Session)
    transport = MagicMock()
    payload = {
        "empowerControlPwdStatus": False,
        "data": {
            "empowerId": 114149,
            "empowerStatus": 0,
            "ownerEmail": "owner@example.com",
            "email": "guest@example.com",
            "authPeriod": 4,
            "expireDate": 1799999999000,
            "detailList": _RANGE_TREE,
        },
    }

    with patch("pybyd._api.vehicle.post_token_json", new=AsyncMock(return_value=payload)) as mock_post:
        detail = await fetch_empower_detail_by_vin(cfg, session, transport, "TESTVIN", empower_id=114149)

    assert isinstance(detail, EmpowerDetail)
    assert detail.empower_id == 114149
    assert detail.empower_control_pwd_status is False
    assert detail.granted_codes == frozenset({"2", "21", "4", "41"})
    assert mock_post.await_args.kwargs["inner"]["empowerId"] == "114149"


@pytest.mark.asyncio
async def test_client_gps_blocked_when_guest_lacks_31_and_empower_detail_requires_shared() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    client._vehicle_metadata_by_vin["TESTVIN"] = Vehicle.model_validate(
        {"vin": "TESTVIN", "empowerType": -1, "rangeDetailList": _RANGE_TREE}
    )

    with pytest.raises(BydEndpointNotSupportedError, match="guest permission '31'"):
        await client.get_gps_info("TESTVIN")

    client._vehicle_metadata_by_vin["OWNERVIN"] = Vehicle.model_validate({"vin": "OWNERVIN", "empowerType": 2})
    with pytest.raises(BydEndpointNotSupportedError, match="not a shared guest grant"):
        await client.get_empower_detail("OWNERVIN")


def test_byd_car_masks_capabilities_for_shared_guest() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    guest_vehicle = Vehicle.model_validate({"vin": "TESTVIN", "empowerType": -1, "rangeDetailList": _RANGE_TREE})
    car = BydCar(client, "TESTVIN", guest_vehicle, capabilities=_caps_all())

    assert car.hvac.available is True
    assert car.lock.available is True
    assert car.capabilities.start_charge is True
    # Missing "43" (battery heat) and "31" (GPS) in _RANGE_TREE
    assert car.battery.available is False
    assert car.capabilities.battery_heat is False
    assert car.capabilities.location is False


def test_command_gating_ignores_deep_codes_that_are_not_function_nos() -> None:
    caps = _caps_all()
    # a level-3 tree code that is not a command functionNo must not act as an allow-list
    guest_perms = frozenset({"2", "21", "2101"})

    lock_verdict = evaluate_command_gate(
        RemoteCommand.LOCK,
        caps,
        is_shared=True,
        guest_permissions=guest_perms,
    )
    assert lock_verdict.supported is True


class _FakeGpsCloud:
    """Minimal BYD cloud double for GPS endpoint permission and userType tests."""

    def __init__(
        self,
        session: Session,
        *,
        guest: bool,
        gps_allowed: bool = True,
        answer: str = "0",
        message: str = "permission denied",
    ) -> None:
        self._session = session
        self.guest = guest
        self.gps_allowed = gps_allowed
        self.answer = answer
        self.message = message
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post_secure(self, endpoint: str, outer: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, outer))
        declared = outer.get("userType") == "1"
        if declared and not self.guest:
            return {"code": "8003", "message": "Vehicle authorization invalid"}
        if not declared and self.guest:
            return {"code": "1001", "message": "service exception"}
        if self.guest and not self.gps_allowed:
            return {"code": "8002", "message": self.message}
        if self.answer != "0":
            return {"code": self.answer, "message": self.message}
        body = json.dumps({"requestSerial": "ser-1", "data": {"latitude": 52.0, "longitude": 4.0}})
        return {"code": "0", "respondData": aes_encrypt_hex(body, self._session.content_key())}


@pytest.mark.parametrize("endpoint", ["/control/getGpsInfo", "/control/getGpsInfoResult"])
async def test_fetch_gps_endpoint_maps_8002_to_endpoint_not_supported(endpoint: str) -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = Session(user_id="42", sign_token="sign", encry_token="encry")
    session.guest_vins["TESTVIN0000000001"] = True
    cloud = _FakeGpsCloud(session, guest=True, gps_allowed=False, message="no location permission")

    with pytest.raises(BydEndpointNotSupportedError, match="no location permission") as exc_info:
        await fetch_gps_endpoint(endpoint, cfg, session, cloud, "TESTVIN0000000001")  # type: ignore[arg-type]

    assert exc_info.value.code == "8002"
    assert exc_info.value.endpoint == endpoint
    assert [outer.get("userType") for _, outer in cloud.calls] == ["1"]


async def test_fetch_gps_endpoint_unlisted_guest_retries_user_type_then_maps_8002() -> None:
    """Restored session without guest_vins retries 1001 -> userType=1 -> 8002."""
    cfg = BydConfig(username="u@example.com", password="secret")
    session = Session(user_id="42", sign_token="sign", encry_token="encry")
    vin = "TESTVIN0000000001"
    cloud = _FakeGpsCloud(session, guest=True, gps_allowed=False)

    with pytest.raises(BydEndpointNotSupportedError) as exc_info:
        await fetch_gps_endpoint("/control/getGpsInfo", cfg, session, cloud, vin)  # type: ignore[arg-type]

    assert exc_info.value.code == "8002"
    assert exc_info.value.endpoint == "/control/getGpsInfo"
    assert [outer.get("userType") for _, outer in cloud.calls] == [None, "1"]
    assert session.guest_vins[vin] is True
    assert (vin, "/control/getGpsInfo") not in session.user_type_settled

    # Subsequent call uses the learned guest flag directly without retrying.
    cloud.calls.clear()
    with pytest.raises(BydEndpointNotSupportedError) as exc_info2:
        await fetch_gps_endpoint("/control/getGpsInfo", cfg, session, cloud, vin)  # type: ignore[arg-type]

    assert exc_info2.value.code == "8002"
    assert [outer.get("userType") for _, outer in cloud.calls] == ["1"]


async def test_fetch_gps_endpoint_owner_wrong_user_type_retry_still_works() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = Session(user_id="42", sign_token="sign", encry_token="encry")
    vin = "TESTVIN0000000001"
    session.guest_vins[vin] = True
    cloud = _FakeGpsCloud(session, guest=False)

    decoded, serial = await fetch_gps_endpoint("/control/getGpsInfo", cfg, session, cloud, vin)  # type: ignore[arg-type]

    assert serial == "ser-1"
    assert decoded["data"]["latitude"] == 52.0
    assert [outer.get("userType") for _, outer in cloud.calls] == ["1", None]
    assert session.guest_vins[vin] is False


async def test_fetch_gps_endpoint_6051_still_maps_to_data_unavailable() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = Session(user_id="42", sign_token="sign", encry_token="encry")
    cloud = _FakeGpsCloud(session, guest=False, answer="6051", message="no fix")

    with pytest.raises(BydDataUnavailableError) as exc_info:
        await fetch_gps_endpoint("/control/getGpsInfo", cfg, session, cloud, "TESTVIN0000000001")  # type: ignore[arg-type]

    assert exc_info.value.code == "6051"


@pytest.mark.parametrize(
    "cached_vehicle",
    [
        None,
        Vehicle.model_validate({"vin": "TESTVIN0000000001", "empowerType": -1}),
    ],
)
async def test_client_get_gps_info_fail_open_surfaces_8002_as_not_supported(
    cached_vehicle: Vehicle | None,
) -> None:
    """When preflight gating fails open (uncached vehicle or empty rangeDetailList), 8002 raises not supported."""
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    vin = "TESTVIN0000000001"
    session = Session(user_id="42", sign_token="sign", encry_token="encry")
    if cached_vehicle is not None:
        client._vehicle_metadata_by_vin[vin] = cached_vehicle
        session.guest_vins[vin] = True
    cloud = _FakeGpsCloud(session, guest=True, gps_allowed=False)
    client._session = session
    client._transport = cloud  # type: ignore[assignment]

    with pytest.raises(BydEndpointNotSupportedError) as exc_info:
        await client.get_gps_info(vin)

    assert exc_info.value.code == "8002"
    assert exc_info.value.endpoint == "/control/getGpsInfo"
