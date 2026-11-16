"""HAN temperature scale offset and per-field projection replacement (#74)."""

from __future__ import annotations

import pytest

from pybyd._capabilities.hvac import HvacCapability
from pybyd._constants import celsius_to_scale, temp_offset_from_range
from pybyd._state_engine import ProjectionSpec, VehicleStateEngine
from pybyd.models.control import ClimateStartParams
from pybyd.models.hvac import HvacOverallStatus, HvacStatus
from pybyd.models.vehicle import Vehicle


@pytest.mark.parametrize(
    ("range_min", "range_max", "expected"),
    [(17, 33, 16.0), (15, 31, 14.0), (None, None, 14.0), (17, None, 14.0), (30, 20, 14.0), (10, 40, 14.0)],
)
def test_temp_offset_from_range(range_min: float | None, range_max: float | None, expected: float) -> None:
    assert temp_offset_from_range(range_min, range_max) == expected


def test_han_scale() -> None:
    hvac = HvacStatus.model_validate({"statusNow": {"vdeTempRangeMin": 17, "vdeTempRangeMax": 33}})
    assert hvac.temp_offset == 16.0
    params = ClimateStartParams(temperature=29, time_span=1, temp_offset=hvac.temp_offset)
    data = params.to_control_params_map()
    assert data["mainSettingTemp"] == 13
    assert data["copilotSettingTemp"] == 13
    assert "tempOffset" not in data and "temp_offset" not in data


def test_default_scale_unchanged() -> None:
    assert celsius_to_scale(29) == 15
    assert ClimateStartParams(temperature=29).to_control_params_map()["mainSettingTemp"] == 15
    with pytest.raises(ValueError):
        ClimateStartParams(temperature=33)
    with pytest.raises(ValueError):
        ClimateStartParams(temperature=15, temp_offset=16.0)


async def test_capability_uses_reported_range() -> None:
    engine = VehicleStateEngine("VIN", Vehicle(vin="VIN"))
    sent: list[ClimateStartParams] = []

    async def start_fn(vin: str, *, params: ClimateStartParams) -> None:
        sent.append(params)

    async def execute(cmd, specs):  # type: ignore[no-untyped-def]
        await cmd()

    cap = HvacCapability(
        start_fn=start_fn,
        stop_fn=start_fn,
        schedule_fn=start_fn,
        vin="VIN",
        execute_command=execute,
        get_state=lambda: engine.snapshot,
    )
    assert cap.temp_range == (15.0, 31.0)
    await engine.update_hvac(HvacStatus.model_validate({"vdeTempRangeMin": 17, "vdeTempRangeMax": 33, "pm": 5}))
    assert cap.temp_range == (17.0, 33.0)
    await cap.start(temperature=33, duration=10)
    assert sent[-1].to_control_params_map()["mainSettingTemp"] == 17
    assert sent[-1].cycle_mode == 2
    await cap.start(temperature=21, duration=10, cycle_mode=1)
    assert sent[-1].cycle_mode == 1


async def test_newer_projection_replaces_older() -> None:
    engine = VehicleStateEngine("VIN", Vehicle(vin="VIN"))
    await engine.update_hvac(HvacStatus.model_validate({"status": 2, "pm": 5}))
    async with engine.lock:
        engine.register_projections([ProjectionSpec("hvac", "status", HvacOverallStatus.ON)])
        engine.register_projections([ProjectionSpec("hvac", "status", HvacOverallStatus.OFF)])
    assert len(engine.active_projections) == 1
    await engine.update_hvac(HvacStatus.model_validate({"status": 2, "pm": 5}))
    assert engine.active_projections == []
    assert engine.snapshot.hvac is not None
    assert engine.snapshot.hvac.status == HvacOverallStatus.OFF


def _capture_cap(engine: VehicleStateEngine, sent: list[ClimateStartParams]) -> HvacCapability:
    async def start_fn(vin: str, *, params: ClimateStartParams) -> None:
        sent.append(params)

    async def execute(cmd, specs):  # type: ignore[no-untyped-def]
        await cmd()

    return HvacCapability(
        start_fn=start_fn,
        stop_fn=start_fn,
        schedule_fn=start_fn,
        vin="VIN",
        execute_command=execute,
        get_state=lambda: engine.snapshot,
    )


async def test_start_sends_fan_level() -> None:
    engine = VehicleStateEngine("VIN", Vehicle(vin="VIN"))
    sent: list[ClimateStartParams] = []
    cap = _capture_cap(engine, sent)
    await cap.start(temperature=21, duration=10)
    assert "windLevel" not in sent[-1].to_control_params_map()
    await cap.start(temperature=21, duration=10, fan_level=4)
    assert sent[-1].to_control_params_map()["windLevel"] == 4
    with pytest.raises(ValueError):
        await cap.start(temperature=21, duration=10, fan_level=8)


async def test_start_keeps_running_settings() -> None:
    # issue #64: a setpoint change on a running session reset recirculation
    engine = VehicleStateEngine("VIN", Vehicle(vin="VIN"))
    sent: list[ClimateStartParams] = []
    cap = _capture_cap(engine, sent)
    await engine.update_hvac(HvacStatus.model_validate({"status": 1, "cycleChoice": 1, "windPosition": 3, "pm": 5}))
    await cap.start(temperature=24, duration=10)
    data = sent[-1].to_control_params_map()
    assert data["cycleMode"] == 1
    assert data["windLevel"] == 3
    await cap.start(temperature=24, duration=10, cycle_mode=2, fan_level=6)
    data = sent[-1].to_control_params_map()
    assert data["cycleMode"] == 2
    assert data["windLevel"] == 6


async def test_start_when_off_uses_defaults() -> None:
    engine = VehicleStateEngine("VIN", Vehicle(vin="VIN"))
    sent: list[ClimateStartParams] = []
    cap = _capture_cap(engine, sent)
    await engine.update_hvac(HvacStatus.model_validate({"status": 2, "cycleChoice": 1, "windPosition": 6, "pm": 5}))
    await cap.start(temperature=24, duration=10)
    data = sent[-1].to_control_params_map()
    assert data["cycleMode"] == 2
    assert "windLevel" not in data


@pytest.mark.parametrize(("raw", "expected"), [(1, False), (2, True), (None, None), (99, None)])
def test_compressor_state(raw: int | None, expected: bool | None) -> None:
    # issue #64: Dolphin Surf dumps show acSwitch 1=off, 2=on
    hvac = HvacStatus.model_validate({} if raw is None else {"acSwitch": raw})
    assert hvac.is_compressor_on is expected
