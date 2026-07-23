"""Central filtering/validation layer for incoming vehicle telemetry models.

This module is the single place where pyBYD applies *quality filtering* to
incoming payloads before values are persisted by the state engine.

Design goals
------------
1. Keep filtering rules in one place.
2. Support both section-level and per-field filtering patterns.
3. Make extension safe and explicit, so new rules are easy to add.

Two filtering styles are used:

- **Section-level filters** (example: GPS):
    operate on the whole model because validity depends on multiple fields
    together (for example latitude + longitude).
- **Per-field filters** (example: realtime zero-dropped fields):
    operate on one field at a time and can preserve previous values when
    incoming values are missing or non-authoritative.

Execution model
---------------
- State engine calls ``apply_realtime_filters(previous, incoming)`` for
    realtime payloads and ``apply_gps_filters(previous, incoming)`` for GPS.
- A filter may keep the incoming value unchanged, or request a replacement
    (usually the last known value).
- Realtime per-field filters use a sentinel contract:
    return ``_MISSING`` to indicate "no change".

How to extend with more filters
-------------------------------
1. Add a new filter function with signature ``RealtimeFieldFilter`` when
     handling a single realtime field.
2. Register it in ``_REALTIME_FIELD_FILTERS`` under the target field name.
     Multiple filters can be chained per field; first non-``_MISSING`` wins.
3. For model-wide logic (cross-field dependencies), add/extend an
     ``apply_<section>_filters`` function that processes the full model.
4. Add regression tests for:
     - preserve previous value case,
     - accept incoming authoritative value case,
     - first payload / no-previous-value case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import VehicleRealtimeData

_GPS_NULL_ISLAND_THRESHOLD: float = 0.1
_MISSING: object = object()

_ZERO_DROP_FIELD_NAMES: tuple[str, ...] = (
    "left_front_door_lock",
    "right_front_door_lock",
    "left_rear_door_lock",
    "right_rear_door_lock",
    "sliding_door_lock",
    "elec_percent",
    "left_front_tire_pressure",
    "right_front_tire_pressure",
    "left_rear_tire_pressure",
    "right_rear_tire_pressure",
    "endurance_mileage",
    "ev_endurance",
    "endurance_mileage_v2",
    "total_mileage",
    "total_mileage_v2",
    "oil_endurance",
    "oil_percent",
)

# Energy consumption fields where the HTTP /getEnergyConsumption endpoint
# returns the sentinel string "--" (stripped to None) while MQTT vehicleInfo
# events carry real values. Preserving the last known good value stops
# HTTP polls from clobbering the MQTT-derived value.
_PRESERVE_WHEN_NONE_FIELD_NAMES: tuple[str, ...] = (
    "recent_50km_energy",
    "total_energy",
    "total_consumption",
    "total_consumption_en",
)

RealtimeFieldFilter = Callable[[str, Any, Any, bool], Any | object]
"""Per-field realtime filter callback.

Args:
    field_name: Canonical model field name being filtered.
    previous_value: Stored/last-known field value.
    incoming_value: Incoming field value (or ``_MISSING`` when absent).
    incoming_present: Whether field exists in incoming model payload.

Returns:
    - replacement value to override incoming field value, or
    - ``_MISSING`` to leave incoming field value unchanged.
"""


def _has_valid_coordinates(gps: GpsInfo) -> bool:
    """Return True when *gps* carries a usable latitude/longitude pair.

    A coordinate pair is considered **invalid** (returns False) when:
    - either latitude or longitude is ``None`` (partial fix is useless), or
    - both values fall within the Null Island threshold (API artefact).
    """
    lat, lon = gps.latitude, gps.longitude
    if lat is None or lon is None:
        return False
    return not (abs(lat) < _GPS_NULL_ISLAND_THRESHOLD and abs(lon) < _GPS_NULL_ISLAND_THRESHOLD)


def guard_gps_coordinates(
    previous: GpsInfo | None,
    incoming: GpsInfo | None,
) -> GpsInfo | None:
    """Return the best available GpsInfo, preferring *incoming*.

    Falls back to *previous* when *incoming* has incomplete coordinates
    (either value is ``None``), suspiciously near-zero ``(0, 0)``
    "Null Island" coordinates, or is ``None`` itself.

    On first startup (``previous is None``) the same validity checks
    apply; ``None`` is returned when no usable coordinates exist yet.
    """
    if incoming is None:
        return previous
    if not _has_valid_coordinates(incoming):
        return previous
    return incoming


def _drop_zero_value_filter(
    _: str,
    previous_value: Any,
    incoming_value: Any,
    incoming_present: bool,
) -> Any | object:
    """Drop zero-valued incoming telemetry by preserving previous value."""
    if not incoming_present:
        return _MISSING
    if incoming_value == 0:
        return previous_value
    return _MISSING


def _preserve_when_none_filter(
    _: str,
    previous_value: Any,
    incoming_value: Any,
    incoming_present: bool,
) -> Any | object:
    """Preserve previous value when incoming is absent or ``None``."""
    if previous_value is None:
        return _MISSING
    if not incoming_present or incoming_value is None:
        return previous_value
    return _MISSING


_REALTIME_FIELD_FILTERS: dict[str, tuple[RealtimeFieldFilter, ...]] = {
    **{field_name: (_drop_zero_value_filter,) for field_name in _ZERO_DROP_FIELD_NAMES},
    **{field_name: (_preserve_when_none_filter,) for field_name in _PRESERVE_WHEN_NONE_FIELD_NAMES},
}


def _apply_model_field_filters(
    previous: object,
    incoming: object,
    field_filters: dict[str, tuple[RealtimeFieldFilter, ...]],
) -> dict[str, Any]:
    """Apply per-field filters and return update overrides.

    Filtering rules are applied per field in registration order.
    For each field, the first filter returning a non-``_MISSING`` value wins.
    """
    updates: dict[str, Any] = {}
    incoming_fields: set[str] = set(getattr(incoming, "model_fields_set", set()))

    for field_name, filters in field_filters.items():
        previous_value = getattr(previous, field_name, _MISSING)
        if previous_value is _MISSING:
            continue

        incoming_present = field_name in incoming_fields
        incoming_value = getattr(incoming, field_name, _MISSING) if incoming_present else _MISSING

        for filter_func in filters:
            filtered_value = filter_func(
                field_name,
                previous_value,
                incoming_value,
                incoming_present,
            )
            if filtered_value is _MISSING:
                continue
            updates[field_name] = filtered_value
            break

    return updates


_TIRE_PRESSURE_FIELDS: tuple[str, ...] = (
    "left_front_tire_pressure",
    "right_front_tire_pressure",
    "left_rear_tire_pressure",
    "right_rear_tire_pressure",
)


def _guard_tire_press_unit(
    previous: VehicleRealtimeData,
    incoming: VehicleRealtimeData,
) -> Any | object:
    """Drop incoming ``tire_press_unit`` when all four pressures are zero.

    The HTTP /vehicleRealTimeResult endpoint returns the realtime block
    with ``tirePressUnit: 3`` (kPa, the default) and every tire pressure
    set to ``0`` even when the vehicle is configured for PSI. The tire
    pressure zero-drop filters already preserve the previous numeric
    values, but the unit field has no such guard — so the unit flips
    PSI → kPa while the numeric values stay PSI-scale, and any HA
    consumer doing unit conversion ends up displaying ``pressure /
    6.89476`` until the next MQTT push restores the real unit.

    Returns the previous unit when every pressure on *incoming* reads
    ``0`` / ``None``, or :data:`_MISSING` to leave the incoming value
    unchanged.
    """
    pressures = [getattr(incoming, f, None) for f in _TIRE_PRESSURE_FIELDS]
    if all(p is None or p == 0 for p in pressures):
        return getattr(previous, "tire_press_unit", _MISSING)
    return _MISSING


def apply_realtime_filters(
    previous: VehicleRealtimeData | None,
    incoming: VehicleRealtimeData,
) -> VehicleRealtimeData:
    """Apply all realtime filtering rules in one place.

    Rules currently include:
    - selected realtime zero-drop gating (doors/locks, SOC/range/tire pressure/odometer).
    - cross-field guard preserving ``tire_press_unit`` when all four
      tire pressures on the incoming payload read zero.

    The returned model is either:
    - the original incoming payload when no override is needed, or
    - a copy with field overrides from registered filters.
    """
    baseline = previous if previous is not None else VehicleRealtimeData.model_validate({})
    updates = _apply_model_field_filters(baseline, incoming, _REALTIME_FIELD_FILTERS)
    tire_unit_override = _guard_tire_press_unit(baseline, incoming)
    if tire_unit_override is not _MISSING:
        updates["tire_press_unit"] = tire_unit_override
    if updates:
        return incoming.model_copy(update=updates)
    return incoming


def apply_gps_filters(
    previous: GpsInfo | None,
    incoming: GpsInfo | None,
) -> GpsInfo | None:
    """Apply all GPS filtering rules in one place."""
    return guard_gps_coordinates(previous, incoming)


_HVAC_SENSOR_PLACEHOLDER_FIELDS: tuple[str, ...] = (
    "temp_in_car",
    "temp_out_car",
    "pm",
)


def apply_hvac_filters(
    previous: HvacStatus | None,
    incoming: HvacStatus,
) -> HvacStatus:
    """Drop the all-zero-or-missing placeholder HVAC payload.

    BYD's HVAC HTTP endpoint has been observed returning a payload where
    every sensor-population field (``temp_in_car``, ``temp_out_car``,
    ``pm``) is 0 or missing simultaneously while the vehicle's HVAC
    block is asleep — a placeholder, not real data.  When detected, each
    of those three fields is pinned to its previous value when one
    exists, and cleared to ``None`` otherwise, so the placeholder's
    literal 0 does not surface as a reading.  Setpoints / state enums on
    the same payload still update.

    On first poll (``previous is None``) the placeholder is rejected
    wholesale and the sensor fields resolve to ``None``, matching the
    realtime zero-drop family's first-poll behaviour.  This matters in
    practice: a parked car's HVAC block sleeps within minutes, so a
    first poll after a consumer restart commonly lands on the
    placeholder — accepting it surfaced a false ``0`` reading, which
    then self-perpetuated as the pinned "previous value".

    A genuine 0 °C reading on one of the sensors round-trips intact as
    long as the same payload carries any other real sensor value (true
    of every real capture observed so far), and a genuine 0 already
    pinned from such a payload keeps being pinned.  The trade-off: a
    payload whose whole triad is genuinely 0-or-missing at once is
    indistinguishable from the placeholder and is suppressed too.
    """
    all_zero_or_missing = all(
        getattr(incoming, field, None) in (None, 0, 0.0) for field in _HVAC_SENSOR_PLACEHOLDER_FIELDS
    )
    if not all_zero_or_missing:
        return incoming
    updates: dict[str, Any] = {}
    for field in _HVAC_SENSOR_PLACEHOLDER_FIELDS:
        prev_value = getattr(previous, field, None) if previous is not None else None
        if getattr(incoming, field, None) != prev_value:
            updates[field] = prev_value
    if not updates:
        return incoming
    return incoming.model_copy(update=updates)
