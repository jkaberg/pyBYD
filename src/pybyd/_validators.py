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
)

# HVAC sensor-population fields the cloud uses together to signal a
# placeholder payload: when every reading is the "no data" value the
# payload carries no real sensor data, only state and setpoints. Used by
# :func:`_is_hvac_placeholder` to detect the bad payload as a whole
# rather than guarding each field individually — a real 0 °C reading or
# real 0 µg/m³ PM measurement must round-trip cleanly.
_HVAC_SENSOR_FIELDS: tuple[str, ...] = (
    "temp_in_car",
    "temp_out_car",
    "pm",
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


def apply_realtime_filters(
    previous: VehicleRealtimeData | None,
    incoming: VehicleRealtimeData,
) -> VehicleRealtimeData:
    """Apply all realtime filtering rules in one place.

    Rules currently include:
    - selected realtime zero-drop gating (doors/locks, SOC/range/tire pressure/odometer).

    The returned model is either:
    - the original incoming payload when no override is needed, or
    - a copy with field overrides from registered filters.
    """
    baseline = previous if previous is not None else VehicleRealtimeData.model_validate({})
    updates = _apply_model_field_filters(baseline, incoming, _REALTIME_FIELD_FILTERS)
    if updates:
        return incoming.model_copy(update=updates)
    return incoming


def _is_hvac_placeholder(data: HvacStatus) -> bool:
    """Return True when *data* carries no real sensor readings.

    The HTTP /control/getStatusNow endpoint occasionally returns a
    payload where every sensor-populated field (cabin temperature,
    exterior temperature, PM2.5) is zero or absent, while state and
    setpoint fields keep their last values. Treat such payloads as
    placeholders and skip the sensor-field merge so the previous MQTT
    readings survive until the next push.

    Any one sensor reporting a real value flips the verdict — a payload
    with ``pm: 0, temp_in_car: 22, temp_out_car: 0`` is accepted as a
    legitimate cold-start reading where exterior happens to read 0 °C.
    """
    return all(
        getattr(data, f, None) in (None, 0, 0.0) for f in _HVAC_SENSOR_FIELDS
    )


def apply_hvac_filters(
    previous: HvacStatus | None,
    incoming: HvacStatus,
) -> HvacStatus:
    """Apply HVAC filtering rules.

    Drops the sensor-population fields (``temp_in_car``, ``temp_out_car``,
    ``pm``) when *incoming* matches the placeholder signature — every
    sensor field reads zero or is absent. Setpoints, state enums, and
    other fields on the payload still flow through unchanged; only the
    sensor readings are pinned to the previous values.

    Temperature fields additionally use BYD's documented ``-129``
    sentinel via :func:`is_temp_sentinel` on the model itself, so a real
    0 °C reading on a non-placeholder payload round-trips unchanged.
    """
    if previous is None:
        return incoming
    if not _is_hvac_placeholder(incoming):
        return incoming
    updates: dict[str, Any] = {}
    for field_name in _HVAC_SENSOR_FIELDS:
        prev_value = getattr(previous, field_name, None)
        if prev_value is not None:
            updates[field_name] = prev_value
    if updates:
        return incoming.model_copy(update=updates)
    return incoming


def apply_gps_filters(
    previous: GpsInfo | None,
    incoming: GpsInfo | None,
) -> GpsInfo | None:
    """Apply all GPS filtering rules in one place."""
    return guard_gps_coordinates(previous, incoming)
