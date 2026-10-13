"""Internal constants shared across the library."""

USER_AGENT = "okhttp/4.12.0"
SESSION_EXPIRED_CODES: frozenset[str] = frozenset({"1002", "1005", "1010"})

# ------------------------------------------------------------------
# BYD climate temperature scale  (°C → scale 1-17)
# ------------------------------------------------------------------

_SCALE_MIN = 1
_SCALE_MAX = 17
_OFFSET_C = 14.0
_TEMP_MIN_C = _OFFSET_C + _SCALE_MIN  # 15.0
_TEMP_MAX_C = _OFFSET_C + _SCALE_MAX  # 31.0


DEFAULT_TEMP_OFFSET_C = _OFFSET_C


def temp_offset_from_range(range_min: float | None, range_max: float | None) -> float:
    """Derive the scale offset from a vehicle's reported °C range.

    Most models use 15-31 °C, but eg the HAN reports 17-33 °C
    (``vdeTempRangeMin`` / ``vdeTempRangeMax``) on the same 1-17 scale.
    Falls back to the 15-31 °C offset when the range is missing or doesn't
    fit the scale.
    """
    if range_min is None or range_max is None:
        return _OFFSET_C
    if not 0 < range_max - range_min <= _SCALE_MAX - _SCALE_MIN:
        return _OFFSET_C
    return float(range_min) - _SCALE_MIN


def celsius_to_scale(temp_c: float, *, offset: float = _OFFSET_C) -> int:
    """Convert a °C temperature to BYD's climate scale (1-17).

    The default *offset* covers 15-31 °C. Pass the result of
    :func:`temp_offset_from_range` for vehicles with a different range.

    Raises :class:`ValueError` if *temp_c* is outside the supported range.
    """
    value = float(temp_c)
    temp_min, temp_max = offset + _SCALE_MIN, offset + _SCALE_MAX
    if not temp_min <= value <= temp_max:
        raise ValueError(f"temperature must be between {temp_min} and {temp_max} °C, got {value}")
    return max(_SCALE_MIN, min(_SCALE_MAX, int(round(value - offset))))


# ------------------------------------------------------------------
# BYD climate duration codes  (minutes → time_span 1-5)
# ------------------------------------------------------------------

_DURATION_TO_CODE: dict[int, int] = {10: 1, 15: 2, 20: 3, 25: 4, 30: 5}
VALID_CLIMATE_DURATIONS: tuple[int, ...] = (10, 15, 20, 25, 30)


def minutes_to_time_span(minutes: int) -> int:
    """Convert a climate duration in minutes to a BYD ``time_span`` code.

    Supported values: 10, 15, 20, 25, 30 → codes 1-5.

    Raises :class:`ValueError` for unsupported durations.
    """
    code = _DURATION_TO_CODE.get(minutes)
    if code is None:
        raise ValueError(f"duration must be one of {VALID_CLIMATE_DURATIONS} minutes, got {minutes}")
    return code
