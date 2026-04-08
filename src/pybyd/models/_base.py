"""Base model and enum for BYD API responses.

Every BYD response model inherits from :class:`BydBaseModel` which
provides:

* ``alias_generator=to_camel`` so camelCase API keys map
  automatically to snake_case fields.
* A ``model_validator(mode="before")`` that strips BYD sentinel
  values (``""``, ``"--"``, NaN) so the field default is used.
* A ``raw`` dict that captures the original payload.

State enums inherit from :class:`BydEnum` which adds an ``UNKNOWN``
member at ``-1`` and a ``_missing_`` hook that returns ``UNKNOWN``
for any value without a mapped member.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# Sentinel strings the BYD API uses for "not available".
_SENTINELS = frozenset({"", "--", "NaN", "nan"})

# ---------------------------------------------------------------------------
# Shared sentinel predicates
# ---------------------------------------------------------------------------


def is_negative(value: int | float) -> bool:
    """Return ``True`` when *value* is negative (e.g. ``-1`` sentinel)."""
    return value < 0


def is_temp_sentinel(value: int | float) -> bool:
    """Return ``True`` when *value* is the BYD temperature sentinel ``-129``."""
    return value in (-129.0, -129)


# ---------------------------------------------------------------------------
# Common key aliases shared across multiple BYD response models.
# BYD API sends "stearing" (typo) instead of "steering".
# ---------------------------------------------------------------------------
COMMON_KEY_ALIASES: dict[str, str] = {
    "stearingWheelHeatState": "steeringWheelHeatState",
}

# Threshold to distinguish seconds from milliseconds.
_MS_THRESHOLD = 1_000_000_000_000

# China (and some overseas) vehicle payloads use Java-style date strings, e.g.
# ``Sat Nov 22 00:00:00 CST 2025`` for ``autoBoughtTime``. Parse without relying
# on system locale for ``%b``.
_EN_MONTH: dict[str, int] = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}

# Offset hours for the timezone token in those strings (CN uses CST = UTC+8).
_JAVA_DATE_TZ_OFFSET_HOURS: dict[str, int] = {
    "CST": 8,
    "GMT": 0,
    "UTC": 0,
}


def _parse_java_style_date_string(s: str) -> datetime | None:
    """Parse ``Wed Nov 22 00:00:00 CST 2025`` into UTC (fixed-offset TZ only)."""
    parts = s.split()
    if len(parts) != 6:
        return None
    _wk, mon, day_s, hms, tz_token, year_s = parts
    mon_num = _EN_MONTH.get(mon)
    if mon_num is None:
        return None
    offset_h = _JAVA_DATE_TZ_OFFSET_HOURS.get(tz_token)
    if offset_h is None:
        return None
    try:
        day = int(day_s)
        year = int(year_s)
        h, m, sec = (int(x) for x in hms.split(":"))
    except ValueError:
        return None

    naive = datetime(year, mon_num, day, h, m, sec)
    src_tz = timezone(timedelta(hours=offset_h))
    return naive.replace(tzinfo=src_tz).astimezone(UTC)


def parse_byd_timestamp(value: Any) -> datetime | None:
    """Convert a BYD time value to a UTC datetime.

    Accepts:

    * epoch seconds or milliseconds (int/float or numeric string),
    * ISO-8601 strings (with optional ``Z``),
    * Java-style locale strings with English month abbreviations and a known
      ``CST``/``GMT``/``UTC`` token (common on CN vehicle list payloads).

    Returns ``None`` when the value is ``None``, empty, or not recognised.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped in _SENTINELS:
            return None
        parsed_java = _parse_java_style_date_string(stripped)
        if parsed_java is not None:
            return parsed_java
        iso = stripped.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            pass
        else:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        try:
            ts = int(stripped)
        except ValueError:
            return None
    else:
        try:
            ts = int(value)
        except (TypeError, ValueError):
            try:
                ts = int(float(value))
            except (TypeError, ValueError):
                return None
    if ts >= _MS_THRESHOLD:
        ts = ts // 1000
    return datetime.fromtimestamp(ts, tz=UTC)


BydTimestamp = Annotated[datetime | None, BeforeValidator(parse_byd_timestamp)]
"""Annotated type that coerces BYD epoch ints (seconds or ms) to UTC datetimes."""


class BydEnum(enum.IntEnum):
    """Base for BYD API state enums.

    Every subclass **must** define ``UNKNOWN = -1``.
    Values the API sends that have no mapped member automatically
    resolve to ``UNKNOWN`` instead of raising ``ValueError``.
    """

    @classmethod
    def _missing_(cls, value: object) -> BydEnum:
        # noinspection PyUnresolvedReferences
        # pylint: disable=no-member
        if hasattr(cls, "UNKNOWN"):
            unknown: BydEnum = cls.UNKNOWN
            return unknown
        # Fallback: return first member
        return next(iter(cls))


class BydBaseModel(BaseModel):
    """Base for BYD API response models.

    Handles:
    * camelCase → snake_case via ``alias_generator=to_camel``
    * BYD sentinel values (``""``, ``"--"``, NaN) → dropped so
      the field default is used instead
    * Stashes the original API dict in ``raw``
    * Post-construction sentinel normalisation via ``_SENTINEL_RULES``
    """

    _SENTINEL_RULES: ClassVar[dict[str, Callable[..., bool]]] = {}
    """Per-field sentinel predicates.

    Subclasses override this to declare ``{"field_name": predicate}``
    pairs.  After model construction the base ``_normalise_sentinels``
    validator sets the field to ``None`` when *predicate(value)* is
    ``True``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
        alias_generator=to_camel,
    )

    raw: dict[str, Any] = Field(default_factory=dict)
    """Original API response dict."""

    @staticmethod
    def _clean_dict(values: dict[str, Any], aliases: dict[str, str] | None = None) -> dict[str, Any]:
        """Strip BYD sentinel values and apply key aliases on *values*.

        This is the core cleaning logic, extracted so subclass validators
        (e.g. ``HvacStatus._unwrap_status_now``) can re-clean unwrapped
        inner dicts that weren't visible to the first ``_clean_byd_values``
        pass.
        """
        working = dict(values)
        if aliases:
            for old_key, new_key in aliases.items():
                if old_key in working and new_key not in working:
                    working[new_key] = working.pop(old_key)

        cleaned: dict[str, Any] = {}
        for key, value in working.items():
            if value is None:
                continue
            if isinstance(value, str) and value.strip() in _SENTINELS:
                continue
            if isinstance(value, float) and math.isnan(value):
                continue
            cleaned[key] = value
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def _clean_byd_values(cls, values: Any) -> Any:
        """Strip BYD sentinel values, apply key aliases, and stash the raw payload."""
        if not isinstance(values, dict):
            return values
        original = dict(values)

        aliases: dict[str, str] = getattr(cls, "_KEY_ALIASES", {})
        cleaned = BydBaseModel._clean_dict(original, aliases)

        # Only auto-stash raw when not explicitly provided (i.e. model_validate
        # from an API dict).  When constructing with kwargs that include raw=,
        # keep the caller's value.
        if "raw" not in values:
            cleaned["raw"] = original
        return cleaned

    @model_validator(mode="after")
    def _normalise_sentinels(self) -> BydBaseModel:
        """Replace per-field sentinel values with ``None``.

        Uses the ``_SENTINEL_RULES`` class-variable which maps field
        names to predicate callables.  If a field's current value is
        not ``None`` and the predicate returns ``True``, the field is
        set to ``None``.
        """
        sentinel_rules: dict[str, Callable[..., bool]] = getattr(type(self), "_SENTINEL_RULES", {})
        for field_name, predicate in sentinel_rules.items():
            val = getattr(self, field_name, None)
            if val is not None and predicate(val):
                object.__setattr__(self, field_name, None)
        return self
