"""Preconditioning plan models (``/control/standby-vehicles/*``).

App 3.6.0 schedules climate through these plans instead of
``BOOKINGAIR``.  A plan holds one or more remote commands (``OPENAIR``,
``VENTILATIONHEATING``, ``STEERINGWHEELHEATING`` with their
``controlParamsMap``) and an optional schedule: once, every day or
weekly.  The next run comes back in ``executeTime`` (#91).

Every plan runs the A/C; seats and steering wheel heat are extras on
top of it, and the app has no way to save a plan without ``OPENAIR``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pybyd._constants import DEFAULT_TEMP_OFFSET_C, minutes_to_time_span, time_span_to_minutes
from pybyd.models._base import BydBaseModel, BydTimestamp
from pybyd.models.control import ClimateStartParams, SeatClimateParams


def _json_or_raw(value: Any) -> Any:
    # controlParams and controlParamsMap come JSON-encoded as strings
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


COMMAND_CLIMATE = "OPENAIR"
COMMAND_SEATS = "VENTILATIONHEATING"
COMMAND_STEERING_WHEEL = "STEERINGWHEELHEATING"

# plans only do on/off: 1 = on, 3 = off, 0 = seat feature not present
_SEAT_ON = 1
_SEAT_OFF = 3
_COMMAND_TO_SEAT: dict[int, bool] = {1: True, 2: True, 3: False}
# PlanSeats field -> SeatClimateParams field
_SEAT_PARAMS: dict[str, str] = {
    "driver_heat": "main_heat",
    "driver_ventilation": "main_ventilation",
    "passenger_heat": "copilot_heat",
    "passenger_ventilation": "copilot_ventilation",
}
# plans carry the seat levels only, chairType 8 instead of a single seat
_SEAT_PLAN_EXCLUDE = {"chair_type", "remote_mode", "steering_wheel_heat_state"}


class PlanClimate(BaseModel):
    """A/C settings of a plan."""

    model_config = ConfigDict(frozen=True)

    temperature: float
    """Setpoint in °C."""
    duration: int
    """Run length in minutes (10-30)."""
    recirculation: bool = False
    """Internal air circulation (``cycleMode`` 2) instead of external."""


class PlanSeats(BaseModel):
    """Front seat settings of a plan, ``None`` where the car lacks the feature."""

    model_config = ConfigDict(frozen=True)

    driver_heat: bool | None = None
    driver_ventilation: bool | None = None
    passenger_heat: bool | None = None
    passenger_ventilation: bool | None = None

    @property
    def active(self) -> bool:
        """Whether any seat is set to heat or ventilate."""
        return any(self.model_dump().values())


class StandbyPlanCommand(BydBaseModel):
    """One remote command stored in a plan, eg ``OPENAIR``."""

    command_type: str = ""
    command_id: str | None = None
    control_params_map: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command_id", mode="before")
    @classmethod
    def _id_to_str(cls, value: Any) -> Any:
        return str(value) if value is not None else None

    @field_validator("control_params_map", mode="before")
    @classmethod
    def _decode_map(cls, value: Any) -> Any:
        decoded = _json_or_raw(value)
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def climate(
        cls,
        temperature: float,
        duration: int = 10,
        *,
        recirculation: bool = False,
        temp_offset: float = DEFAULT_TEMP_OFFSET_C,
    ) -> StandbyPlanCommand:
        """A/C at ``temperature`` °C for ``duration`` minutes, as the app saves it."""
        params = ClimateStartParams(
            temperature=temperature,
            temp_offset=temp_offset,
            time_span=minutes_to_time_span(duration),
            cycle_mode=2 if recirculation else 1,
        ).to_control_params_map()
        # airSet belongs to the direct start, plans don't send it
        params.pop("airSet", None)
        return cls(command_type=COMMAND_CLIMATE, control_params_map=params)

    @classmethod
    def seats(cls, settings: PlanSeats) -> StandbyPlanCommand:
        """Front seat heat and ventilation, on or off.  ``None`` sends ``0`` (no such feature)."""
        values = settings.model_dump()
        seat_params = SeatClimateParams.model_validate(
            {
                param: 0 if values[field] is None else _SEAT_ON if values[field] else _SEAT_OFF
                for field, param in _SEAT_PARAMS.items()
            }
        )
        params: dict[str, Any] = seat_params.model_dump(by_alias=True, exclude=_SEAT_PLAN_EXCLUDE)
        # chairType 8 only shows up in plans, likely "all seats"
        params["chairType"] = 8
        return cls(command_type=COMMAND_SEATS, control_params_map=params)

    @classmethod
    def steering_wheel(cls, on: bool = True) -> StandbyPlanCommand:
        """Steering wheel heat; plans use their own command type for it."""
        return cls(
            command_type=COMMAND_STEERING_WHEEL,
            control_params_map={"steeringWheelHeatState": _SEAT_ON if on else _SEAT_OFF, "chairType": 5},
        )


EXECUTE_ONCE = "0"
EXECUTE_DAILY = "1"
EXECUTE_WEEKLY = "2"

PLAN_TYPE_PRESET = 1
PLAN_TYPE_USER = 2

_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class StandbySchedule(BaseModel):
    """When a plan runs, as sent to ``save``.

    Build one with :meth:`off`, :meth:`once`, :meth:`daily` or
    :meth:`weekly`.  ``once`` and ``daily`` carry the run as an aware
    datetime since the app sends those as ``executeTime``; ``weekly``
    sends ``startTime`` and ``weeklyDays`` and the cloud works out the
    next run.
    """

    model_config = ConfigDict(frozen=True)

    repeat: Literal["off", "once", "daily", "weekly"]
    at: dt.datetime | None = None
    """Run time for ``once`` and ``daily`` (timezone aware)."""
    start_time: str = ""
    """``"HH:MM"`` for ``weekly``."""
    weekly_days: tuple[int, ...] = ()
    """``0`` = Monday … ``6`` = Sunday, for ``weekly``."""

    @model_validator(mode="after")
    def _check(self) -> StandbySchedule:
        if self.repeat in ("once", "daily") and (self.at is None or self.at.tzinfo is None):
            raise ValueError(f"{self.repeat} needs a timezone aware datetime")
        if self.repeat == "weekly":
            if not _HHMM.fullmatch(self.start_time):
                raise ValueError(f"start_time must be HH:MM, got {self.start_time!r}")
            if not self.weekly_days or any(not 0 <= day <= 6 for day in self.weekly_days):
                raise ValueError(f"weekly_days must be 0-6, got {list(self.weekly_days)!r}")
        return self

    @classmethod
    def off(cls) -> StandbySchedule:
        """Not scheduled."""
        return cls(repeat="off")

    @classmethod
    def once(cls, at: dt.datetime) -> StandbySchedule:
        """Run once at ``at``."""
        return cls(repeat="once", at=at)

    @classmethod
    def daily(cls, at: dt.datetime) -> StandbySchedule:
        """Run every day at ``at``'s time, starting at ``at``.

        The app sends the next occurrence of the time, so ``at`` should
        be in the future.
        """
        return cls(repeat="daily", at=at)

    @classmethod
    def weekly(cls, start_time: str, weekly_days: Any) -> StandbySchedule:
        """Run on ``weekly_days`` at ``start_time`` (``"HH:MM"``)."""
        return cls(repeat="weekly", start_time=start_time, weekly_days=tuple(sorted(set(weekly_days))))


class StandbyPlan(BydBaseModel):
    """A preconditioning plan (one ``list`` entry)."""

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "controlParamsList": "commands",
    }

    id: str = ""
    """Plan id, kept as a string since it is a 64-bit number."""
    plan_name: str = ""
    plan_type: int | None = None
    default_plan: int | None = None
    execute_type: str | None = None
    """``"0"`` = once, ``"1"`` = every day, ``"2"`` = weekly."""
    start_time: str = ""
    """Weekly start as ``"HH:MM"``, empty when not scheduled weekly."""
    weekly_days: list[int] = Field(default_factory=list)
    """Days the plan runs, ``0`` = Monday … ``6`` = Sunday."""
    execute_time: BydTimestamp = None
    """Next run, ``None`` when not scheduled."""
    rec_execute_time: BydTimestamp = None
    """The slot that ran last or is running now.  ``execute_time`` has
    moved on to the next run by then: ``None`` for a once plan, a day
    later for a daily one (#91)."""
    time_zone: str | None = None
    commands: list[StandbyPlanCommand] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _commands_from_control_params(cls, values: Any) -> Any:
        # fall back to the controlParams string when controlParamsList is absent
        if isinstance(values, dict) and not values.get("controlParamsList"):
            decoded = _json_or_raw(values.get("controlParams"))
            if isinstance(decoded, list):
                values = {**values, "controlParamsList": decoded}
        return values

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value: Any) -> Any:
        return str(value) if value is not None else ""

    @field_validator("start_time", "plan_name", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        # a plan that was never scheduled has startTime null
        return "" if value is None else value

    @field_validator("execute_type", mode="before")
    @classmethod
    def _type_to_str(cls, value: Any) -> Any:
        return str(value) if value is not None else None

    @field_validator("weekly_days", mode="before")
    @classmethod
    def _parse_days(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return sorted({int(day) for day in value.split(",") if day.strip().isdigit()})
        return value

    def command(self, command_type: str) -> StandbyPlanCommand | None:
        """The plan's command of ``command_type``, if it has one."""
        return next((command for command in self.commands if command.command_type == command_type), None)

    def climate(self, *, temp_offset: float = DEFAULT_TEMP_OFFSET_C) -> PlanClimate | None:
        """The plan's A/C settings, ``None`` when it doesn't run the A/C."""
        command = self.command(COMMAND_CLIMATE)
        if command is None:
            return None
        params = command.control_params_map
        scale = params.get("mainSettingTemp")
        span = params.get("timeSpan")
        return PlanClimate(
            temperature=float(scale) + temp_offset if isinstance(scale, int) else temp_offset + 1,
            duration=(time_span_to_minutes(span) if isinstance(span, int) else None) or 10,
            recirculation=params.get("cycleMode") == 2,
        )

    @property
    def seats(self) -> PlanSeats | None:
        """The plan's front seat settings, ``None`` when it has none."""
        command = self.command(COMMAND_SEATS)
        if command is None:
            return None
        params = command.control_params_map
        keys = SeatClimateParams.model_fields
        return PlanSeats(
            **{
                field: _COMMAND_TO_SEAT.get(params.get(keys[param].alias or param, 0))
                for field, param in _SEAT_PARAMS.items()
            }
        )

    @property
    def steering_wheel_heat(self) -> bool:
        """Whether the plan heats the steering wheel."""
        command = self.command(COMMAND_STEERING_WHEEL)
        return command is not None and command.control_params_map.get("steeringWheelHeatState") == _SEAT_ON

    @property
    def preset(self) -> bool:
        """The built-in plan, which can't be deleted."""
        return self.plan_type == PLAN_TYPE_PRESET

    @property
    def scheduled(self) -> bool:
        """Whether the plan is set to run.

        Weekly needs both a time and days; days without a time (or the
        other way round) is saved but never runs.
        """
        if self.execute_type == EXECUTE_WEEKLY:
            return bool(self.start_time and self.weekly_days)
        return self.execute_time is not None

    @property
    def schedule(self) -> StandbySchedule:
        """The plan's current schedule, for saving it back unchanged."""
        if not self.scheduled:
            return StandbySchedule.off()
        if self.execute_type == EXECUTE_WEEKLY:
            return StandbySchedule.weekly(self.start_time, self.weekly_days)
        assert self.execute_time is not None
        if self.execute_type == EXECUTE_DAILY:
            return StandbySchedule.daily(self.execute_time)
        return StandbySchedule.once(self.execute_time)


class StandbyPlanList(BydBaseModel):
    """Preconditioning plans for a vehicle (``list`` response)."""

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "data": "plans",
    }

    vin: str = ""
    plans: list[StandbyPlan] = Field(default_factory=list)
    message: str | None = None
    """The cloud's reply to the save that returned this list, if any.

    Worth showing: a daily or weekly run less than 20 min ahead is saved
    but skips today, and this is the only place that says so (#91).
    """


class StandbyPlanRunResult(BydBaseModel):
    """Result of running a plan now (``send`` → ``send-result``)."""

    vin: str = ""
    request_serial: str | None = None
    res: int | None = None
    """``1`` = in progress, ``2`` = done, other values = failure."""
    message: str | None = None
