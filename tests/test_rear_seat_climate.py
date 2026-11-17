"""Rear seat heat/ventilation: chairType 3 (rear left) and 4 (rear right)."""

from __future__ import annotations

from typing import Any

import pytest

from pybyd._capabilities.seat import SeatCapability, SeatLevel, SeatPosition
from pybyd.exceptions import BydEndpointNotSupportedError
from pybyd.models.command_gating import evaluate_command_gate
from pybyd.models.control import RemoteCommand, SeatClimateParams
from pybyd.models.latest_config import LatestConfigFunction, VehicleCapabilities, VehicleLatestConfig

_VIN = "LGXCE6CB0P0000001"

# Rear seat functionNos reported by a BYD Han (#80).
_HAN_REAR_SEATS = ["10030006", "10030007", "10030008", "10030009"]


def _caps_for(function_nos: list[str]) -> VehicleCapabilities:
    items = [LatestConfigFunction.model_validate({"functionNo": fn}) for fn in function_nos]
    latest = VehicleLatestConfig.model_validate({"cfFixedList": items})
    return VehicleCapabilities.from_latest_config(_VIN, latest)


def test_han_rear_seat_function_nos_map_to_capabilities() -> None:
    caps = _caps_for(_HAN_REAR_SEATS)
    assert caps.rear_left_seat_heat is True
    assert caps.rear_left_seat_ventilation is True
    assert caps.rear_right_seat_heat is True
    assert caps.rear_right_seat_ventilation is True
    assert caps.unknown_function_nos == []


@pytest.mark.parametrize(
    ("param_key", "chair_type"),
    [
        ("lr_seat_heat_state", "3"),
        ("lr_seat_ventilation_state", "3"),
        ("rr_seat_heat_state", "4"),
        ("rr_seat_ventilation_state", "4"),
    ],
)
def test_with_change_sets_rear_chair_type(param_key: str, chair_type: str) -> None:
    params = SeatClimateParams().with_change(param_key, 1)
    assert params.chair_type == chair_type
    assert getattr(params, param_key) == 1


@pytest.mark.parametrize(
    ("chair_type", "function_no", "gate_id"),
    [("3", "10030007", "seat_rear_left"), ("4", "10030008", "seat_rear_right")],
)
def test_gate_accepts_rear_chair_types(chair_type: str, function_no: str, gate_id: str) -> None:
    verdict = evaluate_command_gate(
        RemoteCommand.SEAT_CLIMATE,
        _caps_for([function_no]),
        control_params={"chairType": chair_type},
    )
    assert verdict.supported is True
    assert verdict.gate_id == gate_id


def test_gate_rejects_rear_chair_type_without_rear_seats() -> None:
    verdict = evaluate_command_gate(
        RemoteCommand.SEAT_CLIMATE,
        _caps_for(["10030001", "10030002", "10300003"]),
        control_params={"chairType": "3"},
    )
    assert verdict.supported is False


class _Snapshot:
    hvac = None
    realtime = None


def _seat_capability(sent: list[SeatClimateParams], **available: bool | None) -> SeatCapability:
    async def _set_seat_climate(vin: str, *, params: SeatClimateParams) -> None:
        sent.append(params)

    async def _execute(cmd: Any, specs: Any) -> None:
        await cmd()

    return SeatCapability(
        set_seat_climate_fn=_set_seat_climate,
        vin=_VIN,
        get_state=lambda: _Snapshot(),  # type: ignore[arg-type,return-value]
        execute_command=_execute,
        **available,
    )


@pytest.mark.asyncio
async def test_seat_capability_sends_rear_left_heat() -> None:
    sent: list[SeatClimateParams] = []
    seat = _seat_capability(sent, rear_left_heat_available=True)

    await seat.heat(SeatPosition.REAR_LEFT, SeatLevel.HIGH)

    assert len(sent) == 1
    assert sent[0].chair_type == "3"
    assert sent[0].lr_seat_heat_state == 1


@pytest.mark.asyncio
async def test_seat_capability_rejects_rear_seat_when_unavailable() -> None:
    sent: list[SeatClimateParams] = []
    seat = _seat_capability(sent)

    with pytest.raises(BydEndpointNotSupportedError):
        await seat.ventilation(SeatPosition.REAR_RIGHT, SeatLevel.LOW)
    assert sent == []
