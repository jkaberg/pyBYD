"""Preconditioning plan models (``/control/standby-vehicles/*``).

App 3.6.0 schedules climate through these plans instead of
``BOOKINGAIR``.  A plan holds one or more remote commands (``OPENAIR``
with its ``controlParamsMap``) and an optional weekly schedule; the
cloud works out the next run and returns it in ``executeTime`` (#91).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from pybyd.models._base import BydBaseModel, BydTimestamp


def _json_or_raw(value: Any) -> Any:
    # controlParams and controlParamsMap come JSON-encoded as strings
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


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
    start_time: str = ""
    """Scheduled start as ``"HH:MM"``, empty when not scheduled."""
    weekly_days: list[int] = Field(default_factory=list)
    """Days the plan runs, ``0`` = Monday … ``6`` = Sunday."""
    execute_time: BydTimestamp = None
    """Next run as computed by the cloud, ``None`` when not scheduled."""
    rec_execute_time: BydTimestamp = None
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

    @field_validator("weekly_days", mode="before")
    @classmethod
    def _parse_days(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return sorted({int(day) for day in value.split(",") if day.strip().isdigit()})
        return value

    @property
    def scheduled(self) -> bool:
        """Whether the plan has a time and at least one day to run on."""
        return bool(self.start_time and self.weekly_days)


class StandbyPlanList(BydBaseModel):
    """Preconditioning plans for a vehicle (``list`` response)."""

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "data": "plans",
    }

    vin: str = ""
    plans: list[StandbyPlan] = Field(default_factory=list)


class StandbyPlanRunResult(BydBaseModel):
    """Result of running a plan now (``send`` → ``send-result``)."""

    vin: str = ""
    request_serial: str | None = None
    res: int | None = None
    """``1`` = in progress, ``2`` = done, other values = failure."""
    message: str | None = None
