"""Tests for telemetry, latest_config, session codes, and battery-heat fixes."""

from __future__ import annotations

import pytest

from pybyd._api._common import _raise_for_code
from pybyd._constants import SESSION_EXPIRED_CODES
from pybyd.exceptions import BydApiError, BydSessionExpiredError
from pybyd.models.charging import ChargingStatus
from pybyd.models.control import ControlState, RemoteControlResult
from pybyd.models.latest_config import VehicleCapabilities, VehicleLatestConfig
from pybyd.models.realtime import DoorOpenState, OnlineState, PowerGear, VehicleRealtimeData


def _caps_with(function_nos: list[str]) -> VehicleCapabilities:
    latest = VehicleLatestConfig.model_validate({"cfFixedList": [{"functionNo": fn} for fn in function_nos]})
    return VehicleCapabilities.from_latest_config("VIN123", latest)


def test_online_state_zero_is_offline_and_nonzero_is_online() -> None:
    offline = VehicleRealtimeData.model_validate({"onlineState": 0})
    assert offline.online_state == OnlineState.OFFLINE
    assert offline.is_online is False
    assert VehicleRealtimeData.is_ready_raw({"onlineState": 0, "time": 123}) is False

    online = VehicleRealtimeData.model_validate({"onlineState": 1})
    assert online.online_state == OnlineState.ONLINE
    assert online.is_online is True

    online_two = VehicleRealtimeData.model_validate({"onlineState": 2})
    assert online_two.is_online is True


def test_power_gear_includes_starting_level_2() -> None:
    off = VehicleRealtimeData.model_validate({"powerGear": 1})
    starting = VehicleRealtimeData.model_validate({"powerGear": 2})
    on = VehicleRealtimeData.model_validate({"powerGear": 3})

    assert off.is_vehicle_on is False
    assert starting.power_gear == PowerGear.STARTING
    assert starting.is_vehicle_on is True
    assert on.power_gear == PowerGear.ON
    assert on.is_vehicle_on is True


def test_charging_status_is_connected_ignores_negative_one_sentinel() -> None:
    assert ChargingStatus.model_validate({"connectState": -1}).is_connected is False
    assert ChargingStatus.model_validate({"connectState": 0}).is_connected is False
    assert ChargingStatus.model_validate({"connectState": 1}).is_connected is True


def test_realtime_trunk_lid_lower_and_repair_mode_switch() -> None:
    rt = VehicleRealtimeData.model_validate({"trunkLidLower": 1, "repairModeSwitch": 0})
    assert rt.trunk_lid_lower == DoorOpenState.OPEN
    assert rt.is_any_door_open is True
    assert rt.repair_mode_switch == 0

    rt_str = VehicleRealtimeData.model_validate({"repairModeSwitch": "1"})
    assert rt_str.repair_mode_switch == "1"


def test_latest_config_maps_tpms_and_missing_dtc_codes() -> None:
    caps = _caps_with(["1004", "10040001", "10230006", "10230008", "10230009"])
    assert caps.tpms is True
    assert caps.direct_tpms is True
    assert caps.health_oil_pressure is True
    assert caps.health_engine is True
    assert caps.health_coolant is True
    assert caps.unknown_function_nos == []


def test_session_expired_codes_updated() -> None:
    expected = frozenset({"1002", "1011", "3001", "3003"})
    assert expected == SESSION_EXPIRED_CODES

    with pytest.raises(BydSessionExpiredError):
        _raise_for_code(endpoint="/test", code="1011", message="kick")

    with pytest.raises(BydApiError) as exc_info:
        _raise_for_code(endpoint="/test", code="1005", message="data error")
    assert not isinstance(exc_info.value, BydSessionExpiredError)


def test_battery_heat_result_rejection_overrides_res_2() -> None:
    res = RemoteControlResult.model_validate(
        {
            "res": 2,
            "batteryHeatResult": 6,
            "batteryHeatResultMessage": "Conditions not met",
            "batteryTemperature": 24,
        }
    )
    assert res.control_state == ControlState.FAILURE
    assert res.success is False
    assert res.battery_heat_result == 6
    assert res.battery_temperature == 24
