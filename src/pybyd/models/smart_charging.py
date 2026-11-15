"""Smart charging schedule model.

Populated from ``/control/smartCharge/homePage``.  The endpoint returns
two nested DTOs that describe the user's configured schedules:

* ``smartChargeDto`` — the simple "charge between X and Y" schedule
* ``smartJourneyDto`` — the departure-time / off-peak-window schedule

Both DTOs use ``HH:MM`` time-of-day strings on the wire (no seconds).
``endChargeTime`` may also be the sentinel ``"full"`` meaning *charge
until the battery is full within the start-time window*.

The ``chargeWay`` field selects how often the schedule repeats:

* ``"s"`` — single one-shot charge
* ``"e"`` — every day
* ``"0,1,2,3,4"`` — comma-separated weekday indices (``0`` = Monday)

See ``captures/logs_decrypted/timetable_set_*`` for reference payloads.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any, ClassVar

from pydantic import BeforeValidator, Field, model_validator

from pybyd.models._base import BydBaseModel, BydTimestamp


def _parse_hhmm(value: Any) -> time | None:
    """Parse a BYD ``HH:MM`` time-of-day string to :class:`datetime.time`.

    Returns ``None`` for sentinel-like inputs (the base validator already
    drops ``""`` / ``"--"``, but ``"full"`` is endpoint-specific and
    handled separately on :pyattr:`SmartChargeDto.charge_until_full`).
    """
    if value is None or isinstance(value, time):
        return value if isinstance(value, time) else None
    text = str(value).strip()
    if not text or text.lower() == "full":
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) >= 3 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return time(hour=hour, minute=minute, second=second)


def _parse_status_bool(value: Any) -> bool | None:
    """Parse the BYD status string (``"1"``/``"0"``) to ``bool``."""
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return None
    return text == "1"


def _parse_charge_way(value: Any) -> str | None:
    """Pass through ``chargeWay`` as-is — interpretation lives on the model."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_HhmmTime = Annotated[time | None, BeforeValidator(_parse_hhmm)]
_StatusBool = Annotated[bool | None, BeforeValidator(_parse_status_bool)]
_ChargeWay = Annotated[str | None, BeforeValidator(_parse_charge_way)]


class SmartChargeDto(BydBaseModel):
    """The simple "charge within a window" schedule (``smartChargeDto``).

    ``end_time`` is ``None`` when the wire value is the ``"full"`` sentinel —
    use :pyattr:`charge_until_full` to distinguish that from "no schedule".
    """

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "startChargeTime": "start_time",
        "endChargeTime": "end_time",
    }

    start_time: _HhmmTime = None
    end_time: _HhmmTime = None
    charge_until_full: bool = False
    """``True`` when ``endChargeTime`` was the wire sentinel ``"full"``."""
    charge_way: _ChargeWay = None
    status: _StatusBool = None
    update_time: BydTimestamp = None
    create_time: BydTimestamp = None
    exe_time: BydTimestamp = None

    @model_validator(mode="before")
    @classmethod
    def _capture_charge_until_full(cls, values: Any) -> Any:
        """Set ``chargeUntilFull=True`` when wire ``endChargeTime`` is ``"full"``.

        Runs alongside :meth:`BydBaseModel._clean_byd_values` (pydantic
        chains inherited ``mode="before"`` validators) — keeping this as
        a separate validator avoids overriding the parent and the
        descriptor-proxy mypy complaint that comes with calling
        ``super()._clean_byd_values(...)``.  The ``"full"`` token isn't
        in the base sentinel set, so it survives the cleaning pass and
        is still visible here.
        """
        if isinstance(values, dict):
            raw_end = values.get("endChargeTime")
            if isinstance(raw_end, str) and raw_end.strip().lower() == "full":
                values = dict(values)
                values["chargeUntilFull"] = True
        return values

    @property
    def is_enabled(self) -> bool:
        """Whether the schedule is currently active."""
        return self.status is True

    @property
    def repeat_days(self) -> list[int] | None:
        """Parsed weekday indices for ``chargeWay``, when applicable.

        Returns ``None`` for the named modes (``"s"`` single, ``"e"`` every
        day) and for empty/unknown values.  Returns a list of ``int`` for
        explicit weekday selections like ``"0,1,2,3,4"`` (``0`` = Monday).
        """
        cw = self.charge_way
        if not cw or cw in ("s", "e"):
            return None
        try:
            return [int(p.strip()) for p in cw.split(",") if p.strip()]
        except ValueError:
            return None


class SmartJourneyDto(BydBaseModel):
    """Departure-time / off-peak-window schedule (``smartJourneyDto``).

    ``use_vehicle_time`` is when the vehicle should be ready;
    ``start_discount_price`` / ``end_discount_price`` define the off-peak
    tariff window the charger will prefer.
    """

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "useVehicleTime": "use_vehicle_time",
        "startDiscountPrice": "discount_start",
        "endDiscountPrice": "discount_end",
    }

    use_vehicle_time: _HhmmTime = None
    discount_start: _HhmmTime = None
    discount_end: _HhmmTime = None
    charge_way: _ChargeWay = None
    status: _StatusBool = None
    update_time: BydTimestamp = None
    create_time: BydTimestamp = None

    @property
    def is_enabled(self) -> bool:
        """Whether the journey schedule is currently active."""
        return self.status is True


class SmartChargingSchedule(BydBaseModel):
    """Top-level schedule snapshot from ``/control/smartCharge/homePage``.

    Composes the two nested DTOs plus the response-level metadata
    (``vehicleTimeZone``, ``soc``, ``type``).
    """

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "smartChargeDto": "charge",
        "smartJourneyDto": "journey",
        "vehicleTimeZone": "time_zone",
    }

    vin: str = ""
    soc: int | None = None
    type: str | None = None
    time_zone: str | None = None
    charge: SmartChargeDto | None = None
    journey: SmartJourneyDto | None = None
    update_time: BydTimestamp = None

    @property
    def is_enabled(self) -> bool:
        """Whether *any* schedule is currently active."""
        return (self.charge is not None and self.charge.is_enabled) or (
            self.journey is not None and self.journey.is_enabled
        )


def _parse_limit_list(value: Any) -> list[str]:
    """Coerce ``currentLimitList`` items to non-empty strings."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _parse_limit_str(value: Any) -> str | None:
    """Coerce ``currentEvCurrentLimit`` to a non-empty string or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_LimitList = Annotated[list[str], BeforeValidator(_parse_limit_list)]
_LimitStr = Annotated[str | None, BeforeValidator(_parse_limit_str)]


class AcCurrentLimit(BydBaseModel):
    """Supported and active AC charging current limits from ``/control/smartCharge/getAcCurrentLimit``."""

    vin: str = ""
    current_ev_current_limit: _LimitStr = None
    current_limit_list: _LimitList = Field(default_factory=list)
