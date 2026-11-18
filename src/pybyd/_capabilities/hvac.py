"""HVAC (climate control) capability."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pybyd._constants import DEFAULT_TEMP_OFFSET_C, minutes_to_time_span
from pybyd._state_engine import ProjectionSpec
from pybyd.exceptions import BydEndpointNotSupportedError, BydError
from pybyd.models.control import ClimateScheduleParams, ClimateStartParams
from pybyd.models.hvac import HvacOverallStatus, HvacWindPosition
from pybyd.models.realtime import AirCirculationMode, SeatHeatVentState, StearingWheelHeat

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pybyd._state_engine import VehicleSnapshot


class HvacCapability:
    """HVAC start/stop/schedule commands with projection support."""

    def __init__(
        self,
        *,
        start_fn: Callable[..., Awaitable[Any]],
        stop_fn: Callable[..., Awaitable[Any]],
        schedule_fn: Callable[..., Awaitable[Any]],
        vin: str,
        execute_command: Callable[..., Awaitable[None]],
        get_state: Callable[[], VehicleSnapshot] | None = None,
        refresh_fn: Callable[[], Awaitable[Any]] | None = None,
        available: bool | None = True,
    ) -> None:
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._schedule_fn = schedule_fn
        self._vin = vin
        self._execute = execute_command
        self._get_state = get_state
        self._refresh_fn = refresh_fn
        self._available = available

    @property
    def available(self) -> bool:
        return bool(self._available)

    @property
    def temp_offset(self) -> float:
        """°C offset of the 1-17 scale, from the last HVAC status if known."""
        hvac = self._get_state().hvac if self._get_state is not None else None
        return hvac.temp_offset if hvac is not None else DEFAULT_TEMP_OFFSET_C

    @property
    def temp_range(self) -> tuple[float, float]:
        """Settable temperature range in °C."""
        offset = self.temp_offset
        return offset + 1, offset + 17

    def _ensure_available(self) -> None:
        if not self.available:
            raise BydEndpointNotSupportedError(
                f"HVAC capability not supported for VIN {self._vin}",
                code="capability_unsupported",
                endpoint="hvac",
            )

    async def start(
        self,
        temperature: float = 21.0,
        duration: int = 20,
        cycle_mode: int | None = None,
        fan_level: int | None = None,
    ) -> None:
        """Start climate control, or change the settings of a running session.

        Parameters
        ----------
        temperature
            Target temperature in °C (15-31, or the vehicle's reported
            range when it differs, eg 17-33 on the HAN).
        duration
            Run duration in minutes (10/15/20/25/30).
        cycle_mode
            Air source: ``1`` = fresh (outside) air, ``2`` = recirculate.
            ``None`` keeps the current setting while climate is running,
            otherwise recirculate (the BYD app's default).
        fan_level
            Fan speed ``1``-``7``.  ``None`` keeps the current speed while
            climate is running, otherwise lets the car pick.

        While climate is running and only the temperature changes, the
        command is sent as a setpoint adjustment, so the running session
        and its auto-off timer are kept and ``duration`` is not applied.
        """
        self._ensure_available()
        if fan_level is not None and not 1 <= fan_level <= 7:
            raise ValueError(f"fan_level must be 1-7, got {fan_level}")
        time_span = minutes_to_time_span(duration)

        hvac = self._get_state().hvac if self._get_state is not None else None
        if hvac is None and self._refresh_fn is not None:
            # The temperature scale depends on the car's reported range
            # (15-31 or 17-33); without it a 17-33 car gets 2 °C too much.
            try:
                hvac = await self._refresh_fn()
            except BydError:
                hvac = None
        temp_offset = hvac.temp_offset if hvac is not None else DEFAULT_TEMP_OFFSET_C

        # OPENAIR resends every setting, so a setpoint change on a running
        # session would otherwise reset recirculation and fan speed (#64).
        air_set: str | None = None
        if hvac is not None and hvac.status == HvacOverallStatus.ON:
            current_cycle = (
                int(hvac.cycle_choice)
                if hvac.cycle_choice in (AirCirculationMode.EXTERNAL, AirCirculationMode.INTERNAL)
                else None
            )
            current_fan = (
                int(hvac.wind_position)
                if hvac.wind_position is not None and 1 <= int(hvac.wind_position) <= 7
                else None
            )
            # airSet "14" adjusts only the setpoint; null restarts the
            # session and its auto-off timer, as the app does (#20).
            if cycle_mode in (None, current_cycle) and fan_level in (None, current_fan):
                air_set = "14"
            if cycle_mode is None:
                cycle_mode = current_cycle
            if fan_level is None:
                fan_level = current_fan
        if cycle_mode is None:
            cycle_mode = 2

        params = ClimateStartParams(
            temperature=temperature,
            time_span=time_span,
            temp_offset=temp_offset,
            cycle_mode=cycle_mode,
            wind_level=fan_level,
            air_set=air_set,
        )
        specs = [
            ProjectionSpec("hvac", "status", HvacOverallStatus.ON),
            ProjectionSpec("hvac", "main_setting_temp_new", temperature),
        ]
        if fan_level is not None:
            specs.append(ProjectionSpec("hvac", "wind_position", HvacWindPosition(fan_level)))

        async def _cmd() -> Any:
            return await self._start_fn(self._vin, params=params)

        await self._execute(_cmd, specs)

    async def stop(self) -> None:
        """Stop climate control (including seat/steering heat)."""
        self._ensure_available()
        specs = [
            ProjectionSpec("hvac", "status", HvacOverallStatus.OFF),
            ProjectionSpec("hvac", "main_seat_heat_state", SeatHeatVentState.OFF),
            ProjectionSpec("hvac", "copilot_seat_heat_state", SeatHeatVentState.OFF),
            ProjectionSpec("hvac", "main_seat_ventilation_state", SeatHeatVentState.OFF),
            ProjectionSpec("hvac", "copilot_seat_ventilation_state", SeatHeatVentState.OFF),
            ProjectionSpec("hvac", "steering_wheel_heat_state", StearingWheelHeat.OFF),
            ProjectionSpec("realtime", "main_seat_heat_state", SeatHeatVentState.OFF),
            ProjectionSpec("realtime", "copilot_seat_heat_state", SeatHeatVentState.OFF),
            ProjectionSpec("realtime", "main_seat_ventilation_state", SeatHeatVentState.OFF),
            ProjectionSpec("realtime", "copilot_seat_ventilation_state", SeatHeatVentState.OFF),
            ProjectionSpec("realtime", "steering_wheel_heat_state", StearingWheelHeat.OFF),
        ]

        async def _cmd() -> Any:
            return await self._stop_fn(self._vin)

        await self._execute(_cmd, specs)

    async def schedule(self, params: ClimateScheduleParams) -> None:
        """Schedule climate control.

        Parameters
        ----------
        params
            Pre-built schedule parameters (create/modify/remove).
        """
        self._ensure_available()

        async def _cmd() -> Any:
            return await self._schedule_fn(self._vin, params=params)

        # Scheduling doesn't change observable state immediately
        await self._execute(_cmd, [])
