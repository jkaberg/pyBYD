"""HVAC / climate control status model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import model_validator

from pybyd._constants import celsius_to_scale
from pybyd.models._base import COMMON_KEY_ALIASES, BydBaseModel, BydEnum, is_temp_sentinel
from pybyd.models.realtime import AirCirculationMode, SeatHeatVentState, StearingWheelHeat

__all__ = [
    "AcSwitch",
    "AirConditioningMode",
    "HvacOverallStatus",
    "HvacStatus",
    "HvacWindMode",
    "HvacWindPosition",
    "celsius_to_scale",
]


class AcSwitch(BydEnum):
    """A/C switch state. Relationship to ``HvacOverallStatus`` is unclear."""

    UNKNOWN = -1


class HvacOverallStatus(BydEnum):
    """Overall HVAC on/off status."""

    UNKNOWN = -1
    ON = 1
    OFF = 2


class AirConditioningMode(BydEnum):
    """A/C control mode."""

    UNKNOWN = -1
    OFF = 0
    AUTO = 1
    MANUAL = 2


class HvacWindMode(BydEnum):
    """Fan mode / airflow direction."""

    UNKNOWN = -1
    OFF = 0
    FACE = 1
    FACE_FOOT = 2
    FOOT = 3
    FOOT_DEFROST = 4
    DEFROST = 5


class HvacWindPosition(BydEnum):
    """Fan speed / airflow position."""

    UNKNOWN = -1
    OFF = 0
    POSITION_1 = 1
    POSITION_2 = 2
    POSITION_3 = 3
    POSITION_4 = 4
    POSITION_5 = 5
    POSITION_6 = 6
    POSITION_7 = 7


class HvacStatus(BydBaseModel):
    """Current HVAC / climate control state."""

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        **COMMON_KEY_ALIASES,
    }

    _SENTINEL_RULES: ClassVar[dict[str, Callable[..., bool]]] = {
        "temp_in_car": is_temp_sentinel,
        "temp_out_car": is_temp_sentinel,
    }

    # --- A/C state ---
    ac_switch: AcSwitch | int | None = None
    """A/C switch state."""
    status: HvacOverallStatus | int | None = None
    """Overall HVAC status."""
    air_conditioning_mode: AirConditioningMode | int | None = None
    """A/C mode (auto / manual)."""
    wind_mode: HvacWindMode | int | None = None
    """Fan mode / airflow direction."""
    wind_position: HvacWindPosition | int | None = None
    """Fan speed / airflow position."""
    cycle_choice: AirCirculationMode | int | None = None
    """Air circulation mode (external / internal)."""

    # --- Temperature ---
    main_setting_temp: float | None = None
    """Driver-side set temperature on BYD scale (1-17)."""
    main_setting_temp_new: float | None = None
    """Driver-side set temperature (deg C, precise)."""
    copilot_setting_temp: float | None = None
    """Passenger-side set temperature on BYD scale (1-17)."""
    copilot_setting_temp_new: float | None = None
    """Passenger-side set temperature (deg C, precise)."""
    temp_in_car: float | None = None
    """Interior temperature (deg C). Sentinel ``-129`` -> ``None``."""
    temp_out_car: float | None = None
    """Exterior temperature (deg C). Sentinel ``-129`` -> ``None``."""
    whether_support_adjust_temp: int | None = None
    """1 = dual-zone temperature adjustment supported."""

    # --- Defrost ---
    front_defrost_status: int | None = None
    """Front defrost status (0=off, 1=on)."""
    electric_defrost_status: int | None = None
    """Rear (electric) defrost status (0=off, 1=on)."""
    wiper_heat_status: int | None = None
    """Wiper heater status (0=off)."""

    # --- Seat heating / ventilation ---
    main_seat_heat_state: SeatHeatVentState | None = None
    main_seat_ventilation_state: SeatHeatVentState | None = None
    copilot_seat_heat_state: SeatHeatVentState | None = None
    copilot_seat_ventilation_state: SeatHeatVentState | None = None
    steering_wheel_heat_state: StearingWheelHeat | None = None
    lr_seat_heat_state: SeatHeatVentState | None = None
    lr_seat_ventilation_state: SeatHeatVentState | None = None
    rr_seat_heat_state: SeatHeatVentState | None = None
    rr_seat_ventilation_state: SeatHeatVentState | None = None

    # --- Rapid temperature changes ---
    rapid_increase_temp_state: int | None = None
    rapid_decrease_temp_state: int | None = None

    # --- Refrigerator ---
    refrigerator_state: int | None = None
    refrigerator_door_state: int | None = None

    # --- Air quality ---
    pm: float | None = None
    """PM2.5 reading."""
    pm25_state_out_car: float | None = None
    """Exterior PM2.5 level."""

    @property
    def is_ac_on(self) -> bool:
        """Whether the A/C is currently on."""
        if self.status is None:
            return False
        try:
            return int(self.status) == int(HvacOverallStatus.ON)
        except (TypeError, ValueError):
            return False

    @property
    def interior_temp_available(self) -> bool:
        """Whether interior temperature reading is valid."""
        return self.temp_in_car is not None

    @property
    def is_steering_wheel_heating(self) -> bool | None:
        """Whether steering wheel heating is active.

        Returns ``None`` when the state is unknown.
        """
        if self.steering_wheel_heat_state is None:
            return None
        return self.steering_wheel_heat_state == StearingWheelHeat.ON

    @model_validator(mode="before")
    @classmethod
    def _unwrap_status_now(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        status_now = values.get("statusNow")
        if isinstance(status_now, dict):
            aliases: dict[str, str] = getattr(cls, "_KEY_ALIASES", {})
            return BydBaseModel._clean_dict(status_now, aliases)
        return values
