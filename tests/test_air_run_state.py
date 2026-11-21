"""``airRunState`` follows the A/C, not circulation (#20)."""

from __future__ import annotations

import pytest

from pybyd.models import AirRunState
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import VehicleRealtimeData


@pytest.mark.parametrize(
    ("raw", "state", "running"),
    [
        # plan running: A/C on, internal circulation
        ({"airRunState": 1, "cycleChoice": 2}, AirRunState.ON, True),
        # parked and after the plan: A/C off, either circulation
        ({"airRunState": 2, "cycleChoice": 2}, AirRunState.OFF, False),
        ({"airRunState": 2, "cycleChoice": 1}, AirRunState.OFF, False),
        ({}, None, None),
        ({"airRunState": 7}, AirRunState.UNKNOWN, None),
    ],
)
def test_realtime_air_run_state(raw: dict[str, int], state: AirRunState | None, running: bool | None) -> None:
    data = VehicleRealtimeData.model_validate(raw)
    assert data.air_run_state == state
    assert data.is_ac_running is running


def test_hvac_air_run_state_matches_status() -> None:
    hvac = HvacStatus.model_validate({"airRunState": 1, "status": 1, "acSwitch": 2})
    assert hvac.air_run_state == AirRunState.ON
    assert hvac.is_ac_on
