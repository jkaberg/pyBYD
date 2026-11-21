"""Tests for granular guest permissions (rangeDetailList & empowerDetailByVin)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import BydCar, BydClient, BydConfig, BydEndpointNotSupportedError, EmpowerDetail
from pybyd._api.vehicle import fetch_empower_detail_by_vin
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
    guest = Vehicle.model_validate({"vin": "TESTVIN", "empowerType": -1, "rangeDetailList": _RANGE_TREE})

    assert owner.is_shared is False
    assert owner.has_permission("43") is True
    assert owner.has_permission("31") is True

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
