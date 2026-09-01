"""Vehicle realtime data model.

Enum values and field meanings are documented in API_MAPPING.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import ValidationInfo, model_validator

from pybyd.models._base import COMMON_KEY_ALIASES, BydBaseModel, BydEnum, BydTimestamp, is_negative, is_temp_sentinel
from pybyd.models.vehicle import EnergyType

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class OnlineState(BydEnum):
    """Vehicle online/offline state."""

    UNKNOWN = -1
    ONLINE = 1
    OFFLINE = 2


class ConnectState(BydEnum):
    """T-Box connection state."""

    UNKNOWN = -1
    DISCONNECTED = 0
    CONNECTED = 1


class VehicleState(BydEnum):
    """Vehicle power state."""

    UNKNOWN = -1
    OFF = 0
    ON = 2  # not confirmed


class ChargingState(BydEnum):
    """Charging state indicator."""

    UNKNOWN = -1
    NOT_CHARGING = 0
    CHARGING = 1
    CONNECTED = 15
    # We previously thought value 15 represented "connected", but this value
    # does not change when the charging gun is disconnected, so we should not
    # rely on it.


class SentryStatus(BydEnum):
    """Sentry mode state.

    Confirmed via live toggle on a BYD Sealion 8 (AU region) using
    scripts/data_diff.py: ON observed as 2, OFF observed as 4. Only two
    states have been observed so far; any other raw value falls back to
    UNKNOWN via BydEnum._missing_.
    """

    UNKNOWN = -1
    ON = 2
    OFF = 4


class TirePressureUnit(BydEnum):
    """Unit used for tire pressure readings."""

    UNKNOWN = -1
    BAR = 1
    PSI = 2
    KPA = 3


class DoorOpenState(BydEnum):
    """Door/trunk open/closed state."""

    UNKNOWN = -1
    CLOSED = 0
    OPEN = 1


class LockState(BydEnum):
    """Door lock state.

    ``UNAVAILABLE`` (0) is returned by the API when state is unknown.
    """

    UNKNOWN = -1
    UNAVAILABLE = 0
    UNLOCKED = 1
    LOCKED = 2


class WindowState(BydEnum):
    """Window open/closed state."""

    UNKNOWN = -1
    CLOSED = 1
    OPEN = 2


class PowerGear(BydEnum):
    """Vehicle power state."""

    UNKNOWN = -1
    OFF = 1
    ON = 3


class StearingWheelHeat(BydEnum):
    """Steering wheel heating state.

    Status values: ``-1`` = on, ``1`` = off.
    Command values use a different scale (see ``to_command_level``).
    """

    ON = -1
    OFF = 1

    def to_command_level(self) -> int:
        """Return the value to send in a seat-climate command.

        Command scale: ``1`` = on, ``3`` = off.
        """
        return 1 if self == StearingWheelHeat.ON else 3


class SeatHeatVentState(BydEnum):
    """Seat heating / ventilation level.

    ``NO_DATA`` (0) means the API has no information for this seat
    in the current response -- it does not indicate hardware absence.
    """

    UNKNOWN = -1
    NO_DATA = 0
    OFF = 1
    LOW = 2
    HIGH = 3

    def to_command_level(self) -> int:
        """Return the value to send in a ``set_seat_climate()`` command.

        Command scale is inverted: HIGH=3 -> 1, LOW=2 -> 2, OFF=1 -> 3.
        ``NO_DATA`` and ``UNKNOWN`` map to ``0`` (no action).
        """
        return _SEAT_STATUS_TO_COMMAND.get(self.value, 0)


_SEAT_STATUS_TO_COMMAND: dict[int, int] = {
    -1: 0,  # UNKNOWN  -> no action
    0: 0,  # NO_DATA  -> no action
    1: 3,  # OFF      -> 3 (off)
    2: 2,  # LOW      -> 2 (low)
    3: 1,  # HIGH     -> 1 (high)
}


class AirCirculationMode(BydEnum):
    """Air circulation mode."""

    UNKNOWN = -1
    UNAVAILABLE = 0
    EXTERNAL = 1
    INTERNAL = 2


# ------------------------------------------------------------------
# Hybrid energy/consumption string splitting
# ------------------------------------------------------------------
#
# Several realtime fields combine EV and ICE data when the per-vehicle
# ``EnergyType`` is :attr:`EnergyType.HYBRID`. The shape varies per field:
#
#     EnergyType.EV                 EnergyType.ICE                    EnergyType.HYBRID (combined)
#     ─────────────────────────     ─────────────────────────         ──────────────────────────
#     "6.1"                         "8.4"                             "6.1+8.4"
#     "6.1kW·h/100km"               "3.5L/100km"                      "6.1kW·h/100km+8.4L/100km"
#     "19.7kW·h/100km"              "3.5L/100km"                      "(19.7kW·h+3.5L)/100km"
#     "10.1" (kW·h/100km)           "--"                              "10.1" (L/100km)   ← nearestEnergyConsumption
#
# Parsing branches by :class:`EnergyType`:
#   - EV:     value is single-leg EV
#   - ICE:    value is single-leg petrol
#   - HYBRID: value is combined; pull both legs out
#
# ``nearestEnergyConsumption`` is special: even at HYBRID the cloud returns
# a single-leg value, with the unit field disambiguating which leg.

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_FUEL_UNIT_RE = re.compile(r"L/100km|升", re.IGNORECASE)
_EV_UNIT_RE = re.compile(r"kW[·.]?h/100km|度", re.IGNORECASE)
_SENTINELS = frozenset({"", "--"})

_LegSplit = tuple[str | None, str | None, str | None, str | None]


def _first_num(text: str | None) -> float | None:
    """Extract the first signed decimal in *text* as a float (or None)."""
    if not text:
        return None
    match = _NUM_RE.search(text)
    return float(match.group()) if match else None


def _normalize_unit(text: str | None) -> str | None:
    """Strip the ``·`` (middle-dot) the BYD cloud injects into unit strings.

    The BYD API sends ``kW·h/100km`` etc.; HA-friendly unit strings drop the
    middot (``kWh/100km``). Applied to every unit string surfaced from
    realtime parsing so consumers see a single, normalized form.
    """
    if text is None:
        return None
    return text.replace("·", "")


def _extract_unit(text: str | None) -> str:
    """Return the substring trailing the leading signed decimal in *text*."""
    if not text:
        return ""
    cleaned = text.strip()
    match = _NUM_RE.match(cleaned)
    tail = cleaned if match is None else cleaned[match.end() :].strip()
    return _normalize_unit(tail) or ""


def _classify_two_legs(
    ev_str: str,
    fuel_str: str,
    default_ev_unit: str,
    default_fuel_unit: str,
) -> _LegSplit:
    """Annotate two known leg strings with their unit substrings."""
    ev_unit = _extract_unit(ev_str) or default_ev_unit
    fuel_unit = _extract_unit(fuel_str) or default_fuel_unit
    return ev_str, ev_unit, fuel_str, fuel_unit


def _split_combined_string(
    raw: Any,
    energy_type: EnergyType,
    *,
    default_ev_unit: str = "",
    default_fuel_unit: str = "",
) -> _LegSplit:
    """Split a combined-form consumption string into per-leg (value, unit) pairs.

    Returns ``(ev_value, ev_unit, fuel_value, fuel_unit)``. Each value is a
    string preserving the original format (units inline where the original
    had them). The unit is the trailing substring after the leading number,
    falling back to the supplied default for unit-less inputs.

    Handles three combined shapes plus single-leg fallback:

      - ``"(EVform+Fuelform)/<shared-unit>"`` (e.g. ``"(19.7kW·h+3.5L)/100km"``)
      - ``"EVform+Fuelform"`` with units inline (e.g. ``"6.1kW·h/100km+8.4L/100km"``)
      - ``"EVform+Fuelform"`` numeric only (e.g. ``"6.1+8.4"``)
      - single-leg, classified by embedded unit or ``energy_type``.
    """
    if raw is None:
        return None, None, None, None
    text = str(raw).strip()
    if text in _SENTINELS:
        return None, None, None, None

    if text.startswith("("):
        close_idx = text.find(")")
        if close_idx != -1:
            inside = text[1:close_idx]
            suffix = text[close_idx + 1 :].lstrip("/").strip()
            if "+" in inside:
                left, _, right = inside.partition("+")
                left, right = left.strip(), right.strip()
                ev_str = f"{left}/{suffix}" if suffix else left
                fuel_str = f"{right}/{suffix}" if suffix else right
                return _classify_two_legs(
                    ev_str,
                    fuel_str,
                    default_ev_unit,
                    default_fuel_unit,
                )

    if "+" in text:
        left, _, right = text.partition("+")
        left, right = left.strip(), right.strip()
        # Detect inverse ordering when both halves carry units.
        if _FUEL_UNIT_RE.search(left) and _EV_UNIT_RE.search(right):
            return _classify_two_legs(right, left, default_ev_unit, default_fuel_unit)
        return _classify_two_legs(left, right, default_ev_unit, default_fuel_unit)

    inline_unit = _extract_unit(text)
    if _FUEL_UNIT_RE.search(text):
        return None, None, text, inline_unit or default_fuel_unit
    if _EV_UNIT_RE.search(text):
        return text, inline_unit or default_ev_unit, None, None
    if energy_type is EnergyType.ICE:
        return None, None, text, inline_unit or default_fuel_unit
    return text, inline_unit or default_ev_unit, None, None


def _legacy_leg_alias(
    ev_value: str | None,
    fuel_value: str | None,
    energy_type: EnergyType,
) -> str | None:
    """Compute the legacy non-suffixed alias for a per-leg field.

    Returns the EV-leg value when present. For :attr:`EnergyType.HYBRID`
    where the response carries only the petrol leg (notably
    ``nearestEnergyConsumption`` at HYBRID), falls back to the fuel value
    so legacy consumers still see *some* data rather than ``None``.
    Pure-ICE vehicles (:attr:`EnergyType.ICE`) deliberately do not fall
    back — their petrol data is exposed exclusively via the ``_fuel``
    fields.
    """
    if ev_value is not None:
        return ev_value
    if energy_type is EnergyType.HYBRID:
        return fuel_value
    return None


def _split_nearest_energy_string(
    value: Any,
    unit: Any,
    energy_type: EnergyType,
) -> _LegSplit:
    """Resolve ``nearestEnergyConsumption`` using the paired unit field.

    The cloud only ever returns a single-leg numeric value here; for
    :attr:`EnergyType.HYBRID` that leg is petrol, with the unit field as
    the authoritative classifier (``"L/100km"`` vs ``"kWh/100km"``).
    """
    if value is None:
        return None, None, None, None
    text = str(value).strip()
    if text in _SENTINELS:
        return None, None, None, None

    unit_text = str(unit or "").strip() if unit is not None else ""
    if unit_text in _SENTINELS:
        unit_text = ""
    unit_text = _normalize_unit(unit_text) or ""

    if _FUEL_UNIT_RE.search(unit_text):
        return None, None, text, unit_text
    if _EV_UNIT_RE.search(unit_text):
        return text, unit_text, None, None
    if energy_type is EnergyType.ICE:
        return None, None, text, unit_text
    return text, unit_text, None, None


# ------------------------------------------------------------------
# Key aliases: BYD API key -> canonical camelCase key
# ------------------------------------------------------------------

_KEY_ALIASES: dict[str, str] = {
    **COMMON_KEY_ALIASES,
    "backCover": "trunkLid",
    "leftFrontTirepressure": "leftFrontTirePressure",
    "rightFrontTirepressure": "rightFrontTirePressure",
    "leftRearTirepressure": "leftRearTirePressure",
    "rightRearTirepressure": "rightRearTirePressure",
    "abs": "absWarning",
    "time": "timestamp",
    "recent50kmEnergy": "recent50KmEnergy",
}


class VehicleRealtimeData(BydBaseModel):
    """Realtime telemetry data for a vehicle.

    Numeric fields are ``None`` when the value is absent or
    unparseable from the API response.  All original data is
    available in the ``raw`` dict.
    """

    _KEY_ALIASES: ClassVar[dict[str, str]] = _KEY_ALIASES

    _SENTINEL_RULES: ClassVar[dict[str, Callable[..., bool]]] = {
        "temp_in_car": is_temp_sentinel,
        "full_hour": is_negative,
        "full_minute": is_negative,
        "remaining_hours": is_negative,
        "remaining_minutes": is_negative,
        # ECT value uses both -129 (temp sentinel) and -1 (generic unavailable).
        "ect_value": lambda v: is_temp_sentinel(v) or is_negative(v),
        # Warning / status indicators: -1 means unavailable.
        "ect": is_negative,
        "abs_warning": is_negative,
        "svs": is_negative,
        "srs": is_negative,
        "eps": is_negative,
        "esp": is_negative,
        "pwr": is_negative,
        "power_system": is_negative,
        "tirepressure_system": is_negative,
        "rapid_tire_leak": is_negative,
        "left_front_tire_status": is_negative,
        "right_front_tire_status": is_negative,
        "left_rear_tire_status": is_negative,
        "right_rear_tire_status": is_negative,
        "upgrade_status": is_negative,
        # Fuel range: -1 means unavailable (BEV or no data).
        "oil_endurance": is_negative,
        # EV range / SoC: -1 sentinel on post-wake or deep-sleep payloads.
        # 0 is intentionally NOT a sentinel — an empty battery is a valid
        # reading.  We only strip negatives.
        "endurance_mileage": is_negative,
        "endurance_mileage_v2": is_negative,
        "elec_percent": is_negative,
        # Charge rate: large negative sentinels when not charging.
        "rate": lambda v: v is not None and v <= -9,
    }

    # --- Connection & state ---
    online_state: OnlineState = OnlineState.UNKNOWN
    connect_state: ConnectState = ConnectState.UNKNOWN
    vehicle_state: VehicleState = VehicleState.UNKNOWN
    request_serial: str | None = None

    # --- Battery & range ---
    elec_percent: float | None = None
    """Battery state of charge (0-100 %)."""
    power_battery: float | None = None
    """Alternative battery percentage field."""
    endurance_mileage: float | None = None
    """Estimated remaining EV range (km)."""
    ev_endurance: float | None = None
    """Alternative EV range field."""
    endurance_mileage_v2: float | None = None
    endurance_mileage_v2_unit: str | None = None
    total_mileage: float | None = None
    """Odometer reading (km)."""
    total_mileage_v2: float | None = None
    total_mileage_v2_unit: str | None = None

    # --- Driving ---
    speed: float | None = None
    """Current speed (km/h)."""
    power_gear: PowerGear | None = None
    """Vehicle power state (off / on)."""

    # --- Climate ---
    temp_in_car: float | None = None
    """Interior temperature (deg C). Sentinel ``-129`` is normalised to ``None``."""
    main_setting_temp: int | None = None
    """Driver-side set temperature on BYD scale (1-17)."""
    main_setting_temp_new: float | None = None
    """Driver-side set temperature (deg C, precise)."""
    air_run_state: AirCirculationMode | None = None
    """Air circulation mode."""

    # --- Seat heating/ventilation ---
    main_seat_heat_state: SeatHeatVentState | None = None
    main_seat_ventilation_state: SeatHeatVentState | None = None
    copilot_seat_heat_state: SeatHeatVentState | None = None
    copilot_seat_ventilation_state: SeatHeatVentState | None = None
    steering_wheel_heat_state: StearingWheelHeat | None = None
    lr_seat_heat_state: SeatHeatVentState | None = None
    lr_seat_ventilation_state: SeatHeatVentState | None = None
    rr_seat_heat_state: SeatHeatVentState | None = None
    rr_seat_ventilation_state: SeatHeatVentState | None = None

    # --- Charging ---
    charging_state: ChargingState = ChargingState.UNKNOWN
    """Charging field from the realtime payload.

    May remain ``UNKNOWN`` while ``charge_state`` carries the
    authoritative status.  Prefer :pyattr:`effective_charging_state`.
    """
    charge_state: ChargingState | None = None
    """Authoritative realtime charging state."""
    wait_status: int | None = None
    """Charge wait status."""
    full_hour: int | None = None
    """Estimated hours to full charge. Sentinel ``-1`` -> ``None``."""
    full_minute: int | None = None
    """Estimated minutes to full charge. Sentinel ``-1`` -> ``None``."""
    remaining_hours: int | None = None
    """Remaining hours component. Sentinel ``-1`` -> ``None``."""
    remaining_minutes: int | None = None
    """Remaining minutes component. Sentinel ``-1`` -> ``None``."""
    booking_charge_state: int | None = None
    """Scheduled charging state (0=off)."""
    booking_charging_hour: int | None = None
    """Scheduled charge start hour."""
    booking_charging_minute: int | None = None
    """Scheduled charge start minute."""

    # --- Doors ---
    left_front_door: DoorOpenState | None = None
    right_front_door: DoorOpenState | None = None
    left_rear_door: DoorOpenState | None = None
    right_rear_door: DoorOpenState | None = None
    trunk_lid: DoorOpenState | None = None
    sliding_door: DoorOpenState | None = None
    forehold: DoorOpenState | None = None
    """Front trunk / frunk."""

    # --- Locks ---
    left_front_door_lock: LockState | None = None
    right_front_door_lock: LockState | None = None
    left_rear_door_lock: LockState | None = None
    right_rear_door_lock: LockState | None = None
    sliding_door_lock: LockState | None = None

    # --- Windows ---
    left_front_window: WindowState | None = None
    right_front_window: WindowState | None = None
    left_rear_window: WindowState | None = None
    right_rear_window: WindowState | None = None
    skylight: WindowState | None = None

    # --- Window opening percentage (0-100) ---
    # Sealion 7 EU reports 0 when closed and 10 after the OPEN_WINDOWS
    # vent command; full drop reports 100.  Other trims may not populate.
    left_front_window_pct: int | None = None
    right_front_window_pct: int | None = None
    left_rear_window_pct: int | None = None
    right_rear_window_pct: int | None = None

    # --- Tire pressure ---
    left_front_tire_pressure: float | None = None
    right_front_tire_pressure: float | None = None
    left_rear_tire_pressure: float | None = None
    right_rear_tire_pressure: float | None = None
    left_front_tire_status: int | None = None
    right_front_tire_status: int | None = None
    left_rear_tire_status: int | None = None
    right_rear_tire_status: int | None = None
    tire_press_unit: TirePressureUnit | None = None
    """Pressure unit: BAR (1), PSI (2), KPA (3)."""
    tirepressure_system: int | None = None
    """TPMS system state. 0=normal, >0=warning. Sentinel ``-1`` -> ``None``."""
    rapid_tire_leak: int | None = None
    """Rapid tire leak indicator. 0=no leak, >0=leak. Sentinel ``-1`` -> ``None``."""

    # --- Energy consumption ---
    total_power: float | None = None
    """Unmapped — semantics unconfirmed. Seen ``0.0`` while parked;
    not yet observed non-zero. Do NOT assume this is a cumulative
    energy counter without live confirmation (none of the fields on
    this endpoint expose absolute lifetime kWh — see the note below)."""
    gl: float | None = None
    """Instantaneous battery power (W)."""
    total_energy: str | None = None
    """Lifetime AVERAGE EV consumption rate, kWh/100km (string; "--"
    when unavailable) — despite the name, this is **not** an absolute
    energy total. Split into :attr:`total_energy_ev`/:attr:`total_energy_fuel`
    by :func:`_split_hybrid_legs`, both in kWh/100km (or L/100km for the
    fuel leg)."""
    nearest_energy_consumption: str | None = None
    """Nearest energy consumption (string; "--" when unavailable)."""
    nearest_energy_consumption_unit: str | None = None
    recent_50km_energy: str | None = None
    """AVERAGE EV consumption rate over the last 50km, kWh/100km
    (string; "--" when unavailable) — **not** the absolute energy used
    over that window. For the actual kWh figure, see
    ``EnergyConsumption.nearest_energy_consumption.ev_consumption``
    from the ``getEnergyConsumption`` endpoint (a rolling last-50km
    window, not aligned to a specific trip)."""

    # --- Fuel (hybrid vehicles) ---
    oil_endurance: float | None = None
    """Fuel-based range (km). Sentinel ``-1`` -> ``None``."""
    oil_percent: float | None = None
    """Fuel percentage."""
    total_oil: float | None = None
    """Total fuel consumption."""

    # --- System indicators ---
    power_system: int | None = None
    """Power system warning. 0=normal, >0=warning."""
    engine_status: int | None = None
    """Engine status."""
    epb: int | None = None
    """Electronic parking brake. 0=released, 1=engaged."""
    eps: int | None = None
    """Electric power steering warning. 0=normal, >0=warning."""
    esp: int | None = None
    """Electronic stability program warning. 0=normal, >0=warning."""
    abs_warning: int | None = None
    """ABS warning. 0=normal, >0=warning."""
    svs: int | None = None
    """Service vehicle soon. 0=normal, >0=warning."""
    srs: int | None = None
    """Supplemental restraint system (airbag) warning. 0=normal, >0=warning."""
    ect: int | None = None
    """Engine coolant temperature warning. 0=normal, >0=warning."""
    ect_value: int | None = None
    """Engine coolant temperature (deg C)."""
    pwr: int | None = None
    """Power warning. 0=normal, >0=warning."""

    # --- Feature states ---
    sentry_status: SentryStatus = SentryStatus.UNKNOWN
    """Sentry/dashcam mode. See :class:`SentryStatus`."""
    battery_heat_state: int | None = None
    """Battery heating state."""
    charge_heat_state: int | None = None
    """Charge heating state."""
    upgrade_status: int | None = None
    """OTA upgrade status. 0=none, >0=active."""

    # --- Third-row seats ---
    lr_third_heat_state: SeatHeatVentState | None = None
    """Third-row left seat heat state."""
    lr_third_ventilation_state: SeatHeatVentState | None = None
    """Third-row left seat ventilation state."""
    rr_third_heat_state: SeatHeatVentState | None = None
    """Third-row right seat heat state."""
    rr_third_ventilation_state: SeatHeatVentState | None = None
    """Third-row right seat ventilation state."""

    # --- Charging (extended) ---
    rate: float | None = None
    """Charge rate. Sentinel values (-999, -9) when not charging."""
    less_one_min: bool | None = None
    """Time-to-full is less than one minute."""

    # --- Energy (extended) ---
    # NOTE: every field in this section is a CONSUMPTION RATE (kWh/100km,
    # or L/100km for the fuel leg) despite names like "energy"/"total" that
    # read as an absolute amount. None of them accumulate — the closest
    # thing to an absolute-kWh figure on this vehicle is
    # ``EnergyConsumption.nearest_energy_consumption.ev_consumption`` from
    # the separate ``getEnergyConsumption`` endpoint (a rolling last-50km
    # window, not a lifetime or per-trip total).
    energy_consumption: str | None = None
    """Recent EV consumption rate, kWh/100km (string; differs from
    ``nearestEnergyConsumption`` in refresh cadence, not in unit)."""
    total_consumption: str | None = None
    """Chinese-locale lifetime average consumption rate, kWh/100km."""
    total_consumption_en: str | None = None
    """English-locale lifetime average consumption rate, kWh/100km
    (e.g. '16.6kW·h/100km')."""

    # --- Hybrid leg breakouts (parsed from combined strings) ---
    # For each combined-form field, ``_split_hybrid_legs`` populates four
    # parallel fields: numeric per-leg value plus the per-leg unit string.
    # The original (non-suffixed) field is also rebound to the EV-portion
    # string of the original payload as a backwards-compat alias — the raw
    # combined string remains accessible via ``raw``.
    #
    # All *_ev fields below are consumption RATES (kWh/100km), not
    # absolute energy, same caveat as their un-suffixed source field above.
    energy_consumption_ev: float | None = None
    energy_consumption_ev_unit: str | None = None
    energy_consumption_fuel: float | None = None
    energy_consumption_fuel_unit: str | None = None

    nearest_energy_consumption_ev: float | None = None
    nearest_energy_consumption_ev_unit: str | None = None
    nearest_energy_consumption_fuel: float | None = None
    nearest_energy_consumption_fuel_unit: str | None = None

    recent_50km_energy_ev: float | None = None
    recent_50km_energy_ev_unit: str | None = None
    recent_50km_energy_fuel: float | None = None
    recent_50km_energy_fuel_unit: str | None = None

    total_energy_ev: float | None = None
    total_energy_ev_unit: str | None = None
    total_energy_fuel: float | None = None
    total_energy_fuel_unit: str | None = None

    total_consumption_ev: float | None = None
    total_consumption_ev_unit: str | None = None
    total_consumption_fuel: float | None = None
    total_consumption_fuel_unit: str | None = None

    total_consumption_en_ev: float | None = None
    total_consumption_en_ev_unit: str | None = None
    total_consumption_en_fuel: float | None = None
    total_consumption_en_fuel_unit: str | None = None

    # ``nearestEnergyConsumption`` (raw): a single-number "recent consumption"
    # summary. The cloud picks whichever metric is most informative for the
    # vehicle type — pure-EV: avg EV (kW·h/100km); pure-ICE: avg petrol
    # (L/100km); hybrid: eq-petrol (L/100km, ≈ avgEqOilConsumption from
    # the getEnergyConsumption endpoint). Preserved here verbatim before the
    # legacy ``nearest_energy_consumption`` field is rebound for backwards
    # compat (see ``_split_hybrid_legs``).
    eq_consumption: float | None = None
    eq_consumption_unit: str | None = None

    # --- Fuel (extended) ---
    oil_pressure_system: int | None = None
    """Oil pressure warning. 0=normal."""

    # --- Warnings (extended) ---
    braking_system: int | None = None
    """Braking system warning. 0=normal."""
    charging_system: int | None = None
    """Charging system warning. 0=normal."""
    steering_system: int | None = None
    """Steering system warning. 0=normal."""
    ok_light: int | None = None
    """OK/ready indicator. 0=off."""

    # --- Features (extended) ---
    repair_mode_switch: str | None = None
    """Repair/service mode. '0'=off."""

    # --- Misc ---
    vehicle_time_zone: str | None = None
    """Vehicle timezone (e.g. 'Europe/Rome')."""
    power_battery_connection: int | None = None
    """Battery connectivity indicator. -1=unknown, 0=disconnected."""
    ins: int | None = None
    """Unknown indicator field."""

    # --- Metadata ---
    timestamp: BydTimestamp = None
    """Data timestamp (parsed to UTC datetime)."""

    # ------------------------------------------------------------------
    # Hybrid leg splitting
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _split_hybrid_legs(cls, values: Any, info: ValidationInfo) -> Any:
        """Populate ``_ev``/``_ev_unit``/``_fuel``/``_fuel_unit`` per-leg fields
        and rebind the original (non-suffixed) field to its EV-leg value.

        The original combined string remains accessible via ``raw``. For
        ``nearestEnergyConsumption``/``nearestEnergyConsumptionUnit`` the
        legacy fields alias to the EV leg, which is ``None`` for hybrids
        whose response only carries the petrol leg (use ``..._fuel`` /
        ``..._fuel_unit`` to read it explicitly).
        """
        if not isinstance(values, dict):
            return values
        # Stash the raw snapshot now so the rebinding below doesn't destroy
        # the original combined strings. Parent ``_clean_byd_values`` runs
        # after and only stashes ``raw`` if absent.
        if "raw" not in values:
            values["raw"] = dict(values)
        ctx = info.context if info is not None else None
        ctx_value = ctx.get("energy_type") if ctx else None
        energy_type = ctx_value if isinstance(ctx_value, EnergyType) else EnergyType.EV

        # (raw_key, ev_key, ev_unit_key, fuel_key, fuel_unit_key,
        #  default_ev_unit, default_fuel_unit)
        splits = (
            (
                "energyConsumption",
                "energy_consumption_ev",
                "energy_consumption_ev_unit",
                "energy_consumption_fuel",
                "energy_consumption_fuel_unit",
                "kWh/100km",
                "L/100km",
            ),
            (
                "recent50kmEnergy",
                "recent_50km_energy_ev",
                "recent_50km_energy_ev_unit",
                "recent_50km_energy_fuel",
                "recent_50km_energy_fuel_unit",
                "kWh/100km",
                "L/100km",
            ),
            (
                "totalEnergy",
                "total_energy_ev",
                "total_energy_ev_unit",
                "total_energy_fuel",
                "total_energy_fuel_unit",
                "kWh/100km",
                "L/100km",
            ),
            (
                "totalConsumption",
                "total_consumption_ev",
                "total_consumption_ev_unit",
                "total_consumption_fuel",
                "total_consumption_fuel_unit",
                "度/百公里",
                "升/百公里",
            ),
            (
                "totalConsumptionEn",
                "total_consumption_en_ev",
                "total_consumption_en_ev_unit",
                "total_consumption_en_fuel",
                "total_consumption_en_fuel_unit",
                "kWh/100km",
                "L/100km",
            ),
        )

        for raw_key, ev_k, ev_u_k, fuel_k, fuel_u_k, def_ev_u, def_fuel_u in splits:
            if raw_key not in values:
                continue
            ev_str, ev_u, fuel_str, fuel_u = _split_combined_string(
                values[raw_key],
                energy_type,
                default_ev_unit=def_ev_u,
                default_fuel_unit=def_fuel_u,
            )
            ev_n = _first_num(ev_str)
            fuel_n = _first_num(fuel_str)
            if ev_n is not None:
                values.setdefault(ev_k, ev_n)
                if ev_u:
                    values.setdefault(ev_u_k, ev_u)
            if fuel_n is not None:
                values.setdefault(fuel_k, fuel_n)
                if fuel_u:
                    values.setdefault(fuel_u_k, fuel_u)
            # Backwards-compat: rebind the original (str) field to the
            # EV-portion. For HYBRID where the cloud returns only the
            # petrol leg, fall back to the fuel value so legacy consumers
            # don't suddenly see ``None``.
            values[raw_key] = _legacy_leg_alias(ev_str, fuel_str, energy_type)

        if "nearestEnergyConsumption" in values:
            # Capture the raw single-number "recent consumption" summary
            # before the rebinding below overwrites the legacy field.
            raw_eq = _first_num(str(values.get("nearestEnergyConsumption") or ""))
            if raw_eq is not None:
                values.setdefault("eq_consumption", raw_eq)
                raw_eq_unit = values.get("nearestEnergyConsumptionUnit")
                if raw_eq_unit:
                    values.setdefault(
                        "eq_consumption_unit",
                        _normalize_unit(str(raw_eq_unit)),
                    )
            ev_str, ev_u, fuel_str, fuel_u = _split_nearest_energy_string(
                values.get("nearestEnergyConsumption"),
                values.get("nearestEnergyConsumptionUnit"),
                energy_type,
            )
            # At HYBRID the cloud repurposes nearestEnergyConsumption to
            # carry the equivalent-petrol consumption (≈ avgEqOilConsumption
            # in getEnergyConsumption) — *not* the per-leg averages. The
            # actual nearest EV/fuel averages are packed into energyConsumption
            # (already split into energy_consumption_ev / _fuel above), so
            # surface those here. The eq-petrol value remains in raw.
            if energy_type is EnergyType.HYBRID:
                ec_ev = values.get("energy_consumption_ev")
                ec_fuel = values.get("energy_consumption_fuel")
                ec_ev_u = values.get("energy_consumption_ev_unit")
                ec_fuel_u = values.get("energy_consumption_fuel_unit")
                if ec_ev is not None:
                    ev_str = str(ec_ev)
                    ev_u = ec_ev_u or "kWh/100km"
                if ec_fuel is not None:
                    fuel_str = str(ec_fuel)
                    fuel_u = ec_fuel_u or "L/100km"
            ev_n = _first_num(ev_str)
            fuel_n = _first_num(fuel_str)
            if ev_n is not None:
                values.setdefault("nearest_energy_consumption_ev", ev_n)
                if ev_u:
                    values.setdefault("nearest_energy_consumption_ev_unit", ev_u)
            if fuel_n is not None:
                values.setdefault("nearest_energy_consumption_fuel", fuel_n)
                if fuel_u:
                    values.setdefault("nearest_energy_consumption_fuel_unit", fuel_u)
            # Backwards-compat: legacy value/unit fields alias the EV leg,
            # falling back to the fuel leg for hybrids that carry only petrol.
            values["nearestEnergyConsumption"] = _legacy_leg_alias(
                ev_str,
                fuel_str,
                energy_type,
            )
            values["nearestEnergyConsumptionUnit"] = _legacy_leg_alias(
                ev_u,
                fuel_u,
                energy_type,
            )

        return values

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_ready_raw(vehicle_info: dict[str, Any]) -> bool:
        """Return True if a raw realtime payload appears to contain meaningful data."""
        if not vehicle_info:
            return False
        if vehicle_info.get("onlineState") == int(OnlineState.OFFLINE):
            return False

        tire_fields = [
            "leftFrontTirepressure",
            "rightFrontTirepressure",
            "leftRearTirepressure",
            "rightRearTirepressure",
        ]
        if any(float(vehicle_info.get(f) or 0) > 0 for f in tire_fields):
            return True
        if int(vehicle_info.get("time") or 0) > 0:
            return True
        return float(vehicle_info.get("enduranceMileage") or 0) > 0

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_online(self) -> bool:
        """Whether the vehicle is reporting as online."""
        return self.online_state == OnlineState.ONLINE

    @property
    def effective_charging_state(self) -> ChargingState:
        """Canonical charging state derived from realtime payload.

        ``charge_state`` is the authoritative source.
        """
        if self.charge_state is None:
            return ChargingState.UNKNOWN
        return self.charge_state

    @property
    def is_charging(self) -> bool:
        """Whether the vehicle is currently charging."""
        return self.effective_charging_state == ChargingState.CHARGING

    @property
    def is_charger_connected(self) -> bool:
        """Whether the charging gun is physically connected."""
        return self.effective_charging_state in (
            ChargingState.CHARGING,
            ChargingState.CONNECTED,
        )

    @property
    def time_to_full_minutes(self) -> int | None:
        """Total estimated minutes until fully charged.

        Returns ``None`` when either component is unavailable.
        """
        if self.full_hour is None or self.full_minute is None:
            return None
        return self.full_hour * 60 + self.full_minute

    @property
    def interior_temp_available(self) -> bool:
        """Whether interior temperature reading is valid."""
        return self.temp_in_car is not None

    @property
    def is_locked(self) -> bool | None:
        """Whether all doors are locked.

        Returns ``None`` when no authoritative lock state is available.
        """
        locks = [
            self.left_front_door_lock,
            self.right_front_door_lock,
            self.left_rear_door_lock,
            self.right_rear_door_lock,
        ]
        _SKIP = {None, LockState.UNKNOWN, LockState.UNAVAILABLE}
        known = [lk for lk in locks if lk not in _SKIP]
        if not known:
            return None
        return all(lk == LockState.LOCKED for lk in known)

    @property
    def is_any_door_open(self) -> bool:
        """Whether any door/trunk/frunk is open."""
        doors = [
            self.left_front_door,
            self.right_front_door,
            self.left_rear_door,
            self.right_rear_door,
            self.trunk_lid,
            self.sliding_door,
            self.forehold,
        ]
        return any(d == DoorOpenState.OPEN for d in doors if d is not None)

    @property
    def is_any_window_open(self) -> bool:
        """Whether any window is open."""
        windows = [
            self.left_front_window,
            self.right_front_window,
            self.left_rear_window,
            self.right_rear_window,
            self.skylight,
        ]
        return any(w == WindowState.OPEN for w in windows if w is not None)

    @property
    def is_vehicle_on(self) -> bool:
        """Whether the vehicle is powered on."""
        return self.power_gear == PowerGear.ON

    @property
    def is_battery_heating(self) -> bool | None:
        """Whether the battery heating system is active."""
        if self.battery_heat_state is None:
            return None
        return bool(self.battery_heat_state)

    @property
    def is_steering_wheel_heating(self) -> bool | None:
        """Whether steering wheel heating is active."""
        if self.steering_wheel_heat_state is None:
            return None
        return self.steering_wheel_heat_state == StearingWheelHeat.ON
