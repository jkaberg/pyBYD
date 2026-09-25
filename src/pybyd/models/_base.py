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
from datetime import UTC, datetime
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

# ---------------------------------------------------------------------------
# Unit strings shared by the realtime and energy models.
# The cloud spells one unit several ways: ``kW·h/100km``, ``kWh/100km``,
# and ``度/百公里`` in the Chinese-locale ``totalConsumption``.
# ---------------------------------------------------------------------------
_UNIT_QUANTITIES: dict[str, str] = {"kwh": "kWh", "度": "kWh", "l": "L", "升": "L"}
_UNIT_DISTANCES: dict[str, str] = {
    "100km": "100km",
    "百公里": "100km",
    "100mi": "100miles",
    "100mile": "100miles",
    "100miles": "100miles",
    "百英里": "100miles",
}


def normalize_unit(text: str | None) -> str | None:
    """Return the canonical form of a BYD energy or consumption unit.

    The quantity and the per-distance part are folded separately, ignoring
    case, whitespace, the ``·`` / ``.`` inside ``kW·h`` and the Chinese
    spellings: ``kW·h/100km``, ``KWH/100 KM`` and ``度/百公里`` all become
    ``kWh/100km``, ``升/百公里`` becomes ``L/100km``, and a per-100-miles
    distance becomes ``100miles`` (``kWh/100miles``). A bare quantity folds
    the same way (``kW·h`` → ``kWh``). A part it doesn't recognise is kept
    as sent, minus the ``·``.
    """
    if text is None:
        return None
    quantity, sep, distance = text.replace("·", "").partition("/")
    quantity = _UNIT_QUANTITIES.get("".join(quantity.split()).replace(".", "").casefold(), quantity)
    distance = _UNIT_DISTANCES.get("".join(distance.split()).casefold(), distance)
    return f"{quantity}{sep}{distance}"


# Threshold to distinguish seconds from milliseconds.
_MS_THRESHOLD = 1_000_000_000_000


def parse_byd_timestamp(value: Any) -> datetime | None:
    """Convert a BYD timestamp to a UTC datetime.

    Accepts:

    * ``int`` / numeric string — epoch seconds *or* milliseconds (auto-detected)
    * ISO-8601 string — e.g. ``"2026-05-07T05:56:45.000+00:00"`` from the
      ``smartChargeDto`` schedule blocks

    Returns ``None`` when the value is ``None`` or unparseable.
    """
    if value is None:
        return value
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.lstrip("-").isdigit():
            try:
                # ``Z`` suffix isn't accepted by fromisoformat until 3.11.
                normalised = text.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalised)
            except ValueError:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        value = text
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    # 0 and negative epochs are sentinels meaning "no value", not literal
    # epoch-zero (1970-01-01).  The GPS endpoint returns `gpsTimeStamp=0`
    # while the satellite fix is stale; without this guard the timestamp
    # surfaces as 1970-01-01 in HA which is worse than `unknown`.
    if ts <= 0:
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
