"""HVAC / climate control status model.

Mapped from ``/control/getStatusNow`` response documented in API_MAPPING.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import model_validator

from pybyd._constants import celsius_to_scale
from pybyd.models._base import COMMON_KEY_ALIASES, BydBaseModel, BydEnum, is_negative, is_temp_sentinel
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
    """AcSwitch on/off state.

    Observed 0=off, 1=on in live API data, but the exact semantics
    are not fully confirmed.  ``is_ac_on`` prefers this field when
    present and falls back to :class:`HvacOverallStatus` (``status``)
    which is more reliably observed.
    """

    UNKNOWN = -1
    OFF = 0
    ON = 1


class HvacOverallStatus(BydEnum):
    """Overall HVAC status.

    Observed from live API data: ``2`` while A/C active.
    """

    UNKNOWN = -1
    OFF = 1
    ON = 2


class AirConditioningMode(BydEnum):
    """A/C control mode code."""

    # couldnt get reliable results here.
    UNKNOWN = -1
    AUTO = 1
    MANUAL = 2


class HvacWindMode(BydEnum):
    """Fan (wind) mode — airflow direction."""

    # couldnt get reliable results here. not all are confirmed.
    UNKNOWN = -1
    FACE = 1
    FACE_FOOT = 2
    FOOT = 3
    FOOT_DEFROST = 4
    DEFROST = 5  # aka warm front windshield


class HvacWindPosition(BydEnum):
    """Airflow direction code (wind position)."""

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
        "refrigerator_temp": is_negative,
    }

    # --- A/C state ---
    ac_switch: AcSwitch | int | None = None
    """0=off, 1=on (confirmed)."""
    status: HvacOverallStatus | int | None = None
    """Overall HVAC status; ``2`` observed while A/C active (confirmed)."""
    air_conditioning_mode: AirConditioningMode | int | None = None
    """Mode code; ``1`` observed (confirmed)."""
    wind_mode: HvacWindMode | int | None = None
    """Fan mode code; ``3`` observed (confirmed)."""
    wind_position: HvacWindPosition | int | None = None
    """Airflow direction (unconfirmed)."""
    cycle_choice: AirCirculationMode | int | None = None
    """``2`` observed in live capture (confirmed); exact mapping still unconfirmed."""

    # --- Temperature ---
    main_setting_temp: float | None = None
    """Set temp integer on BYD scale (1-17) (confirmed)."""
    main_setting_temp_new: float | None = None
    """Set temp (°C, precise) (confirmed)."""
    copilot_setting_temp: float | None = None
    """Passenger set temp on BYD scale (1-17) (confirmed)."""
    copilot_setting_temp_new: float | None = None
    """Passenger set temp (°C, precise) (confirmed)."""
    temp_in_car: float | None = None
    """Interior °C; ``-129`` means unavailable (confirmed)."""
    temp_out_car: float | None = None
    """Exterior °C (confirmed)."""
    whether_support_adjust_temp: int | None = None
    """1=supported (confirmed)."""

    # --- Defrost ---
    front_defrost_status: int | None = None
    """Front defrost status.  0=off, 1=on (confirmed).
    BYD SDK ``getAcDefrostState(FRONT)`` (section 6.6.10)."""
    electric_defrost_status: int | None = None
    """Rear (electric) defrost status.  0=off (confirmed).
    BYD SDK ``getAcDefrostState(REAR)`` (section 6.6.10)."""
    wiper_heat_status: int | None = None
    """Wiper heater status.  0=off (confirmed)."""

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

    # --- Third-row seat heating / ventilation (3-row vehicles only) ---
    lr_third_heat_state: SeatHeatVentState | None = None
    """Third-row left seat heating level (unconfirmed)."""
    lr_third_ventilation_state: SeatHeatVentState | None = None
    """Third-row left seat ventilation level (unconfirmed)."""
    rr_third_heat_state: SeatHeatVentState | None = None
    """Third-row right seat heating level (unconfirmed)."""
    rr_third_ventilation_state: SeatHeatVentState | None = None
    """Third-row right seat ventilation level (unconfirmed)."""

    # --- Refrigerator ---
    refrigerator_state: int | None = None
    refrigerator_door_state: int | None = None
    refrigerator_temp: float | None = None
    """Refrigerator temperature (°C).  Negative values (e.g. ``-1``)
    are stripped to ``None`` by sentinel rules (unsupported/unavailable)."""

    # --- Extended climate fields ---
    air_temp_level: int | None = None
    """Air temperature level code (unconfirmed)."""
    air_condition_temp_range: int | None = None
    """A/C temperature range setting (unconfirmed)."""

    # --- Air quality ---
    pm: float | None = None
    """PM2.5 value; 0 observed (confirmed).
    BYD SDK ``getPM25Value()`` (section 6.7.5)."""
    pm25_state_out_car: float | None = None
    """Outside PM2.5 state; 0 observed (confirmed).
    BYD SDK ``getPM25Level(OUT)`` (section 6.7.4)."""

    @property
    def is_ac_on(self) -> bool:
        """Whether the A/C is currently on.

        Prefers ``ac_switch`` when present; falls back to ``status``.
        An explicit switch-off wins over a stale/lagging overall status.
        """
        if self.ac_switch is not None:
            try:
                return int(self.ac_switch) == int(AcSwitch.ON)
            except (TypeError, ValueError):
                pass
        if self.status is None:
            return False
        try:
            return int(self.status) == int(HvacOverallStatus.ON)
        except (TypeError, ValueError):
            return False

    @property
    def is_climate_active(self) -> bool:
        """Whether the HVAC system appears active.

        This is a more permissive signal than :pyattr:`is_ac_on` and is
        intended for consumers that want a best-effort "climate running"
        indicator even when the explicit switch field is missing or
        temporarily inconsistent.  Returns ``True`` when *either*
        ``ac_switch`` is on **or** ``status`` indicates activity.
        """
        try:
            if self.status is not None and int(self.status) == int(HvacOverallStatus.ON):
                return True
        except (TypeError, ValueError):
            pass
        try:
            if self.ac_switch is not None and int(self.ac_switch) == int(AcSwitch.ON):
                return True
        except (TypeError, ValueError):
            pass
        return False

    @property
    def interior_temp_available(self) -> bool:
        """Whether interior temperature reading is valid.

        After sentinel normalisation ``temp_in_car`` is ``None`` when
        the BYD API returned ``-129``, so a simple ``is not None`` suffices.
        """
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
            # The parent _clean_byd_values ran on the outer dict before
            # this validator.  Re-clean the inner dict so sentinel values
            # (e.g. "--", "") inside statusNow are properly stripped.
            aliases: dict[str, str] = getattr(cls, "_KEY_ALIASES", {})
            return BydBaseModel._clean_dict(status_now, aliases)
        return values
