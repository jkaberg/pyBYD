"""Seat heating and ventilation capability."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

from pybyd._state_engine import ProjectionSpec, VehicleSnapshot
from pybyd.exceptions import BydEndpointNotSupportedError
from pybyd.models.control import SeatClimateParams
from pybyd.models.hvac import HvacOverallStatus
from pybyd.models.realtime import SeatHeatVentState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class SeatPosition(enum.StrEnum):
    """Seat position for heat/ventilation commands."""

    DRIVER = "driver"
    COPILOT = "copilot"
    REAR_LEFT = "rear_left"
    REAR_RIGHT = "rear_right"


class SeatLevel(enum.IntEnum):
    """Seat heat/ventilation intensity level."""

    OFF = 0
    LOW = 1
    HIGH = 2

    def to_command_value(self) -> int:
        """Convert to BYD command scale (inverted: HIGH→1, LOW→2, OFF→3)."""
        return _LEVEL_TO_COMMAND[self.value]

    def to_status_value(self) -> SeatHeatVentState:
        """Convert to the corresponding status enum value."""
        return _LEVEL_TO_STATUS[self.value]


# Command scale: HIGH=1, LOW=2, OFF=3
_LEVEL_TO_COMMAND: dict[int, int] = {0: 3, 1: 2, 2: 1}

# Status mapping
_LEVEL_TO_STATUS: dict[int, SeatHeatVentState] = {
    0: SeatHeatVentState.OFF,
    1: SeatHeatVentState.LOW,
    2: SeatHeatVentState.HIGH,
}

# Mapping from (position, "heat"/"vent") → SeatClimateParams field name
_PARAM_KEY_MAP: dict[tuple[SeatPosition, str], str] = {
    (SeatPosition.DRIVER, "heat"): "main_heat",
    (SeatPosition.DRIVER, "vent"): "main_ventilation",
    (SeatPosition.COPILOT, "heat"): "copilot_heat",
    (SeatPosition.COPILOT, "vent"): "copilot_ventilation",
    (SeatPosition.REAR_LEFT, "heat"): "lr_seat_heat_state",
    (SeatPosition.REAR_LEFT, "vent"): "lr_seat_ventilation_state",
    (SeatPosition.REAR_RIGHT, "heat"): "rr_seat_heat_state",
    (SeatPosition.REAR_RIGHT, "vent"): "rr_seat_ventilation_state",
}

# Mapping from (position, "heat"/"vent") → status model field name (for projections)
_STATUS_FIELD_MAP: dict[tuple[SeatPosition, str], str] = {
    (SeatPosition.DRIVER, "heat"): "main_seat_heat_state",
    (SeatPosition.DRIVER, "vent"): "main_seat_ventilation_state",
    (SeatPosition.COPILOT, "heat"): "copilot_seat_heat_state",
    (SeatPosition.COPILOT, "vent"): "copilot_seat_ventilation_state",
    (SeatPosition.REAR_LEFT, "heat"): "lr_seat_heat_state",
    (SeatPosition.REAR_LEFT, "vent"): "lr_seat_ventilation_state",
    (SeatPosition.REAR_RIGHT, "heat"): "rr_seat_heat_state",
    (SeatPosition.REAR_RIGHT, "vent"): "rr_seat_ventilation_state",
}


class SeatCapability:
    """Seat heating and ventilation commands with projection support.

    The BYD API requires all seat values in every ``VENTILATIONHEATING``
    command.  This capability encapsulates that constraint — callers only
    specify the position and level for the seat they want to change.
    """

    def __init__(
        self,
        *,
        set_seat_climate_fn: Callable[..., Awaitable[Any]],
        vin: str,
        get_state: Callable[[], VehicleSnapshot],
        execute_command: Callable[..., Awaitable[None]],
        driver_heat_available: bool | None = True,
        driver_ventilation_available: bool | None = True,
        passenger_heat_available: bool | None = True,
        passenger_ventilation_available: bool | None = True,
        rear_left_heat_available: bool | None = None,
        rear_left_ventilation_available: bool | None = None,
        rear_right_heat_available: bool | None = None,
        rear_right_ventilation_available: bool | None = None,
    ) -> None:
        self._set_seat_climate_fn = set_seat_climate_fn
        self._vin = vin
        self._get_state = get_state
        self._execute = execute_command
        self._available: dict[tuple[SeatPosition, str], bool | None] = {
            (SeatPosition.DRIVER, "heat"): driver_heat_available,
            (SeatPosition.DRIVER, "vent"): driver_ventilation_available,
            (SeatPosition.COPILOT, "heat"): passenger_heat_available,
            (SeatPosition.COPILOT, "vent"): passenger_ventilation_available,
            (SeatPosition.REAR_LEFT, "heat"): rear_left_heat_available,
            (SeatPosition.REAR_LEFT, "vent"): rear_left_ventilation_available,
            (SeatPosition.REAR_RIGHT, "heat"): rear_right_heat_available,
            (SeatPosition.REAR_RIGHT, "vent"): rear_right_ventilation_available,
        }

    def _is_mode_available(self, position: SeatPosition, mode: str) -> bool:
        return bool(self._available.get((position, mode)))

    async def heat(self, position: SeatPosition, level: SeatLevel) -> None:
        """Set seat heating level.

        Parameters
        ----------
        position
            Which seat to change.
        level
            Heating intensity (OFF/LOW/HIGH).
        """
        await self._set_seat(position, "heat", level)

    async def ventilation(self, position: SeatPosition, level: SeatLevel) -> None:
        """Set seat ventilation level.

        Parameters
        ----------
        position
            Which seat to change.
        level
            Ventilation intensity (OFF/LOW/HIGH).
        """
        await self._set_seat(position, "vent", level)

    async def _set_seat(self, position: SeatPosition, mode: str, level: SeatLevel) -> None:
        """Internal: build params from current state and execute command."""
        if not self._is_mode_available(position, mode):
            raise BydEndpointNotSupportedError(
                f"Seat {position.value} {mode} capability not supported for VIN {self._vin}",
                code="capability_unsupported",
                endpoint=f"seat.{mode}",
            )

        param_key = _PARAM_KEY_MAP[(position, mode)]
        status_field = _STATUS_FIELD_MAP[(position, mode)]
        status_value = level.to_status_value()
        command_value = level.to_command_value()

        specs = [
            ProjectionSpec("hvac", status_field, status_value),
            ProjectionSpec("realtime", status_field, status_value),
        ]

        # Activating any seat feature physically turns on the car's A/C.
        # Project HVAC status ON so climate/AC entities update immediately.
        # Turning a feature OFF does NOT turn off the A/C, so we skip that direction.
        if level != SeatLevel.OFF:
            specs.append(ProjectionSpec("hvac", "status", HvacOverallStatus.ON))

        async def _cmd() -> Any:
            # Read state inside the command lock to avoid races
            state = self._get_state()
            params = SeatClimateParams.from_current_state(
                hvac=state.hvac,
                realtime=state.realtime,
            ).with_change(param_key, command_value)
            return await self._set_seat_climate_fn(self._vin, params=params)

        await self._execute(_cmd, specs)
