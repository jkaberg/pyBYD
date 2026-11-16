"""Tests for battery heating parameters, capability gating, and endpoint routing."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pybyd._api import control as control_api
from pybyd.config import BydConfig
from pybyd.exceptions import BydRemoteControlError
from pybyd.models.command_gating import evaluate_command_gate
from pybyd.models.control import BatteryHeatParams, ControlState, RemoteCommand
from pybyd.models.latest_config import LatestConfigFunction, VehicleCapabilities, VehicleLatestConfig

_VIN = "LGXCE6CB0P0000001"


def _caps_for(function_nos: list[str]) -> VehicleCapabilities:
    items = [LatestConfigFunction.model_validate({"functionNo": fn}) for fn in function_nos]
    latest = VehicleLatestConfig.model_validate({"cfFixedList": items})
    return VehicleCapabilities.from_latest_config(_VIN, latest)


def test_battery_heat_params_wire_serialization() -> None:
    on_params = BatteryHeatParams(on=True)
    assert on_params.to_control_params_map() == {"batteryHeatSwitch": 1}
    assert on_params.to_dedicated_control_params_map() == {"batteryHeat": 1, "heatType": 2}

    off_params = BatteryHeatParams(on=False, heat_type=1)
    assert off_params.to_control_params_map() == {"batteryHeatSwitch": 0}
    assert off_params.to_dedicated_control_params_map() == {"batteryHeat": 0, "heatType": 1}


@pytest.mark.parametrize("fn", ["10300002", "10100001"])
def test_battery_heat_capability_and_gate_accept_supported_function_nos(fn: str) -> None:
    caps = _caps_for([fn])
    assert caps.battery_heat is True
    assert caps.unknown_function_nos == []

    verdict = evaluate_command_gate(
        RemoteCommand.BATTERY_HEAT,
        caps,
        control_params=BatteryHeatParams(on=True).to_control_params_map(),
    )
    assert verdict.supported is True


@pytest.mark.asyncio
async def test_battery_heat_legacy_route_preserved_for_battery_heat_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    responses = [
        {"requestSerial": "SERIAL_LEGACY"},
        {"res": 1, "controlState": 1},
    ]

    async def _fake_post_vehicle_json(**kwargs: Any) -> dict[str, Any]:
        calls.append((kwargs["endpoint"], kwargs["inner"]))
        return responses.pop(0)

    monkeypatch.setattr(control_api, "post_vehicle_json", _fake_post_vehicle_json)

    cfg = BydConfig(username="user@example.com", password="secret")
    res = await control_api.poll_remote_control(
        cfg,
        session=AsyncMock(),
        transport=AsyncMock(),
        vin=_VIN,
        command=RemoteCommand.BATTERY_HEAT,
        control_params=BatteryHeatParams(on=True).to_control_params_map(),
        command_pwd="deadbeef",
        poll_interval=0.0,
        command_retries=1,
    )

    assert res.success is True
    assert [ep for ep, _ in calls] == [
        "/control/remoteControl",
        "/control/remoteControlResult",
    ]
    assert json.loads(calls[0][1]["controlParamsMap"]) == {"batteryHeatSwitch": 1}
    assert "controlParamsMap" not in calls[1][1]
    assert calls[1][1]["commandPwd"] == ""


@pytest.mark.asyncio
async def test_battery_heat_dispatches_dedicated_endpoints_and_resolves_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    responses = [
        {"requestSerial": "SERIAL123"},
        {"res": 1, "message": "succcess"},
        {"res": 2, "message": "succcess"},
    ]

    async def _fake_post_vehicle_json(**kwargs: Any) -> dict[str, Any]:
        calls.append((kwargs["endpoint"], kwargs["inner"]))
        return responses.pop(0)

    monkeypatch.setattr(control_api, "post_vehicle_json", _fake_post_vehicle_json)

    cfg = BydConfig(username="user@example.com", password="secret")
    res = await control_api.poll_remote_control(
        cfg,
        session=AsyncMock(),
        transport=AsyncMock(),
        vin=_VIN,
        command=RemoteCommand.BATTERY_HEAT,
        control_params=BatteryHeatParams(on=True).to_dedicated_control_params_map(),
        command_pwd="deadbeef",
        poll_interval=0.0,
        command_retries=1,
    )

    assert res.success is True
    assert res.control_state is ControlState.SUCCESS
    assert [ep for ep, _ in calls] == [
        "/control/remoteControlBatteryHeat",
        "/control/remoteControlBatteryHeatResult",
        "/control/remoteControlBatteryHeatResult",
    ]
    for _, inner in calls:
        assert inner["commandType"] == "BATTERYHEAT"
        assert json.loads(inner["controlParamsMap"]) == {"batteryHeat": 1, "heatType": 2}


@pytest.mark.asyncio
async def test_battery_heat_surfaces_vehicle_rejection_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {"requestSerial": "SERIAL456"},
        {"res": 3, "message": "Las condiciones no se cumplen. No se puede activar el precalentamiento de conducción"},
    ]

    async def _fake_post_vehicle_json(**kwargs: Any) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(control_api, "post_vehicle_json", _fake_post_vehicle_json)

    cfg = BydConfig(username="user@example.com", password="secret")
    with pytest.raises(BydRemoteControlError) as exc_info:
        await control_api.poll_remote_control(
            cfg,
            session=AsyncMock(),
            transport=AsyncMock(),
            vin=_VIN,
            command=RemoteCommand.BATTERY_HEAT,
            control_params=BatteryHeatParams(on=True).to_dedicated_control_params_map(),
            command_pwd="deadbeef",
            poll_interval=0.0,
            command_retries=1,
        )

    assert exc_info.value.endpoint == "/control/remoteControlBatteryHeatResult"
    assert "Las condiciones no se cumplen" in str(exc_info.value)
