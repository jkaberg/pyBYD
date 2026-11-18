"""Charging telemetry from issue #20 (ATTO 3 EVO, Sealion 6/7 data points)."""

from __future__ import annotations

import pytest

from pybyd.models.charging import ChargingStatus
from pybyd.models.latest_config import registered_latest_config_function_nos
from pybyd.models.realtime import ChargingState, VehicleRealtimeData


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-999, None),
        (-2, -2.0),
        (-5, -5.0),
        (-11, -11.0),
        (-150, -150.0),
        (3, 3.0),
    ],
)
def test_rate_keeps_fast_charge_values(raw: int, expected: float | None) -> None:
    assert VehicleRealtimeData.model_validate({"rate": raw}).rate == expected


@pytest.mark.parametrize(
    ("charge_state", "expected"),
    [
        (ChargingState.CHARGING, True),
        (ChargingState.CONNECTED, None),
        (ChargingState.NOT_CHARGING, None),
        (ChargingState.UNKNOWN, None),
    ],
)
def test_is_charger_connected_only_trusts_charging(charge_state: ChargingState, expected: bool | None) -> None:
    data = VehicleRealtimeData.model_validate({"chargeState": int(charge_state)})
    assert data.is_charger_connected is expected


def test_charge_power_parsed_on_realtime_and_charging() -> None:
    payload = {"chargePower": "5.1", "chargePowerUnit": "kW"}
    rt = VehicleRealtimeData.model_validate(payload)
    cs = ChargingStatus.model_validate(payload)
    assert (rt.charge_power, rt.charge_power_unit) == (5.1, "kW")
    assert (cs.charge_power, cs.charge_power_unit) == (5.1, "kW")


def test_charge_power_none_when_unplugged() -> None:
    rt = VehicleRealtimeData.model_validate({"chargePower": None, "chargePowerUnit": None})
    assert rt.charge_power is None
    assert rt.charge_power_unit is None


def test_issue_20_function_nos_are_known() -> None:
    known = registered_latest_config_function_nos()
    for fn in ("1018", "1019", "1024", "1025", "1032", "1033", "10020006", "10030012", "10030014", "10030017"):
        assert fn in known
