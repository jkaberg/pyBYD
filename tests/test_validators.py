"""Tests for pybyd._validators – GPS coordinate guard & realtime filters."""

from __future__ import annotations

import pytest

from pybyd._validators import (
    _has_valid_coordinates,
    apply_gps_filters,
    apply_hvac_filters,
    apply_realtime_filters,
    guard_gps_coordinates,
)
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import LockState, TirePressureUnit, VehicleRealtimeData
from pybyd.models.vehicle import EnergyType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gps(
    lat: float | None = None,
    lon: float | None = None,
    speed: float | None = None,
) -> GpsInfo:
    return GpsInfo(latitude=lat, longitude=lon, speed=speed)


VALID = _gps(lat=48.8566, lon=2.3522, speed=60.0)  # Paris
VALID_B = _gps(lat=31.2304, lon=121.4737, speed=30.0)  # Shanghai
NONE_NONE = _gps()  # lat=None, lon=None
NULL_ISLAND = _gps(lat=0.001, lon=0.002)
PARTIAL_LAT = _gps(lat=48.8566, lon=None)
PARTIAL_LON = _gps(lat=None, lon=2.3522)


# ---------------------------------------------------------------------------
# _has_valid_coordinates
# ---------------------------------------------------------------------------


class TestHasValidCoordinates:
    """Unit tests for the _has_valid_coordinates helper."""

    def test_valid_coordinates(self) -> None:
        assert _has_valid_coordinates(VALID) is True

    def test_both_none(self) -> None:
        assert _has_valid_coordinates(NONE_NONE) is False

    def test_null_island(self) -> None:
        assert _has_valid_coordinates(NULL_ISLAND) is False

    def test_partial_lat_only(self) -> None:
        assert _has_valid_coordinates(PARTIAL_LAT) is False

    def test_partial_lon_only(self) -> None:
        assert _has_valid_coordinates(PARTIAL_LON) is False

    def test_exactly_at_threshold_boundary(self) -> None:
        """Coordinates at exactly the threshold edge are valid."""
        at_edge = _gps(lat=0.1, lon=0.1)
        assert _has_valid_coordinates(at_edge) is True

    def test_negative_valid(self) -> None:
        """Negative coordinates (Southern/Western hemispheres) are valid."""
        neg = _gps(lat=-33.8688, lon=151.2093)  # Sydney
        assert _has_valid_coordinates(neg) is True

    def test_one_coord_zero_other_large(self) -> None:
        """lat≈0 with large lon is fine (e.g. equatorial Africa)."""
        equatorial = _gps(lat=0.05, lon=32.5)
        assert _has_valid_coordinates(equatorial) is True

    def test_zero_zero_exact(self) -> None:
        """Exact (0, 0) is Null Island – invalid."""
        assert _has_valid_coordinates(_gps(lat=0.0, lon=0.0)) is False


# ---------------------------------------------------------------------------
# guard_gps_coordinates – first poll (previous=None)
# ---------------------------------------------------------------------------


class TestGuardGpsFirstPoll:
    """First poll: previous is None."""

    def test_valid_incoming_accepted(self) -> None:
        result = guard_gps_coordinates(None, VALID)
        assert result is VALID

    def test_none_incoming_returns_none(self) -> None:
        result = guard_gps_coordinates(None, None)
        assert result is None

    def test_both_none_coords_returns_none(self) -> None:
        result = guard_gps_coordinates(None, NONE_NONE)
        assert result is None

    def test_null_island_returns_none(self) -> None:
        result = guard_gps_coordinates(None, NULL_ISLAND)
        assert result is None

    def test_partial_lat_returns_none(self) -> None:
        result = guard_gps_coordinates(None, PARTIAL_LAT)
        assert result is None

    def test_partial_lon_returns_none(self) -> None:
        result = guard_gps_coordinates(None, PARTIAL_LON)
        assert result is None


# ---------------------------------------------------------------------------
# guard_gps_coordinates – subsequent polls (previous is set)
# ---------------------------------------------------------------------------


class TestGuardGpsSubsequentPoll:
    """Subsequent polls: previous has valid data."""

    def test_valid_incoming_replaces_previous(self) -> None:
        result = guard_gps_coordinates(VALID, VALID_B)
        assert result is VALID_B

    def test_none_incoming_keeps_previous(self) -> None:
        result = guard_gps_coordinates(VALID, None)
        assert result is VALID

    def test_both_none_coords_keeps_previous(self) -> None:
        result = guard_gps_coordinates(VALID, NONE_NONE)
        assert result is VALID

    def test_null_island_keeps_previous(self) -> None:
        result = guard_gps_coordinates(VALID, NULL_ISLAND)
        assert result is VALID

    def test_partial_lat_keeps_previous(self) -> None:
        result = guard_gps_coordinates(VALID, PARTIAL_LAT)
        assert result is VALID

    def test_partial_lon_keeps_previous(self) -> None:
        result = guard_gps_coordinates(VALID, PARTIAL_LON)
        assert result is VALID


# ---------------------------------------------------------------------------
# apply_gps_filters (thin wrapper – ensure delegation)
# ---------------------------------------------------------------------------


class TestApplyGpsFilters:
    """apply_gps_filters delegates to guard_gps_coordinates."""

    def test_delegates_valid(self) -> None:
        assert apply_gps_filters(None, VALID) is VALID

    def test_delegates_invalid_first_poll(self) -> None:
        assert apply_gps_filters(None, NONE_NONE) is None

    def test_delegates_preserves_previous(self) -> None:
        assert apply_gps_filters(VALID, NONE_NONE) is VALID


# ---------------------------------------------------------------------------
# apply_realtime_filters – zero-drop gating for selected numeric fields
# ---------------------------------------------------------------------------


class TestApplyRealtimeZeroDropGating:
    """Zero-value drop gating for selected realtime telemetry fields."""

    @pytest.mark.parametrize(
        "field_name,previous_value",
        [
            ("elec_percent", 73.0),
            ("left_front_tire_pressure", 2.4),
            ("right_front_tire_pressure", 2.5),
            ("left_rear_tire_pressure", 2.6),
            ("right_rear_tire_pressure", 2.7),
            ("endurance_mileage", 320.0),
            ("ev_endurance", 310.0),
            ("endurance_mileage_v2", 305.0),
            ("total_mileage", 18234.0),
            ("total_mileage_v2", 18234.0),
            ("oil_endurance", 420.0),
            ("oil_percent", 74.0),
        ],
    )
    def test_zero_incoming_with_previous_keeps_previous(self, field_name: str, previous_value: float) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: previous_value})
        incoming = VehicleRealtimeData.model_validate({field_name: 0})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == previous_value

    @pytest.mark.parametrize(
        "field_name",
        [
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
        ],
    )
    def test_zero_incoming_without_previous_dropped(self, field_name: str) -> None:
        incoming = VehicleRealtimeData.model_validate({field_name: 0})

        filtered = apply_realtime_filters(None, incoming)

        assert getattr(filtered, field_name) is None

    @pytest.mark.parametrize(
        "field_name,previous_value,incoming_value",
        [
            ("elec_percent", 73.0, 72.0),
            ("left_front_tire_pressure", 2.4, 2.3),
            ("right_front_tire_pressure", 2.5, 2.4),
            ("left_rear_tire_pressure", 2.6, 2.5),
            ("right_rear_tire_pressure", 2.7, 2.6),
            ("endurance_mileage", 320.0, 315.0),
            ("ev_endurance", 310.0, 300.0),
            ("endurance_mileage_v2", 305.0, 299.0),
            ("total_mileage", 18234.0, 18240.0),
            ("total_mileage_v2", 18234.0, 18240.0),
            ("oil_endurance", 420.0, 410.0),
            ("oil_percent", 74.0, 72.0),
        ],
    )
    def test_nonzero_incoming_replaces_previous(
        self,
        field_name: str,
        previous_value: float,
        incoming_value: float,
    ) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: previous_value})
        incoming = VehicleRealtimeData.model_validate({field_name: incoming_value})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == incoming_value


class TestApplyRealtimeLockZeroDrop:
    """Door lock fields now follow the same zero-drop policy as numeric fields."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "left_front_door_lock",
            "right_front_door_lock",
            "left_rear_door_lock",
            "right_rear_door_lock",
            "sliding_door_lock",
        ],
    )
    def test_missing_incoming_lock_is_not_preserved(self, field_name: str) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: LockState.LOCKED})
        incoming = VehicleRealtimeData.model_validate({})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) is None

    @pytest.mark.parametrize(
        "field_name",
        [
            "left_front_door_lock",
            "right_front_door_lock",
            "left_rear_door_lock",
            "right_rear_door_lock",
            "sliding_door_lock",
        ],
    )
    def test_unavailable_incoming_lock_keeps_previous(self, field_name: str) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: LockState.LOCKED})
        incoming = VehicleRealtimeData.model_validate({field_name: LockState.UNAVAILABLE})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == LockState.LOCKED

    @pytest.mark.parametrize(
        "field_name",
        [
            "left_front_door_lock",
            "right_front_door_lock",
            "left_rear_door_lock",
            "right_rear_door_lock",
            "sliding_door_lock",
        ],
    )
    def test_unavailable_incoming_lock_without_previous_dropped(self, field_name: str) -> None:
        incoming = VehicleRealtimeData.model_validate({field_name: LockState.UNAVAILABLE})

        filtered = apply_realtime_filters(None, incoming)

        assert getattr(filtered, field_name) is None

    @pytest.mark.parametrize(
        "field_name",
        [
            "left_front_door_lock",
            "right_front_door_lock",
            "left_rear_door_lock",
            "right_rear_door_lock",
            "sliding_door_lock",
        ],
    )
    def test_authoritative_incoming_lock_replaces_previous(self, field_name: str) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: LockState.LOCKED})
        incoming = VehicleRealtimeData.model_validate({field_name: LockState.UNLOCKED})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == LockState.UNLOCKED


class TestApplyRealtimePreserveWhenNone:
    """Preserve previous value when HTTP sentinel strips incoming to None."""

    @pytest.mark.parametrize(
        "field_name,previous_value",
        [
            ("recent_50km_energy", "14.7kW·h/100km"),
            ("total_energy", "11.1kW·h/100km"),
            ("total_consumption", "11.1kW·h/100km"),
            ("total_consumption_en", "11.1kW·h/100km"),
        ],
    )
    def test_sentinel_incoming_with_previous_keeps_previous(
        self,
        field_name: str,
        previous_value: str,
    ) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: previous_value})
        incoming = VehicleRealtimeData.model_validate({field_name: "--"})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == previous_value

    @pytest.mark.parametrize(
        "field_name",
        [
            "recent_50km_energy",
            "total_energy",
            "total_consumption",
            "total_consumption_en",
        ],
    )
    def test_sentinel_incoming_without_previous_stays_none(self, field_name: str) -> None:
        incoming = VehicleRealtimeData.model_validate({field_name: "--"})

        filtered = apply_realtime_filters(None, incoming)

        assert getattr(filtered, field_name) is None

    @pytest.mark.parametrize(
        "field_name,previous_value,incoming_value",
        [
            ("recent_50km_energy", "14.7kW·h/100km", "14.9kW·h/100km"),
            ("total_energy", "11.1kW·h/100km", "11.2kW·h/100km"),
            ("total_consumption", "11.1kW·h/100km", "11.3kW·h/100km"),
            ("total_consumption_en", "11.1kW·h/100km", "11.3kW·h/100km"),
        ],
    )
    def test_authoritative_incoming_replaces_previous(
        self,
        field_name: str,
        previous_value: str,
        incoming_value: str,
    ) -> None:
        previous = VehicleRealtimeData.model_validate({field_name: previous_value})
        incoming = VehicleRealtimeData.model_validate({field_name: incoming_value})

        filtered = apply_realtime_filters(previous, incoming)

        assert getattr(filtered, field_name) == incoming_value


class TestApplyRealtimeTirePressUnitGuard:
    """``tire_press_unit`` is preserved when all four pressures read zero.

    The HTTP /vehicleRealTimeResult endpoint returns ``tirePressUnit: 3``
    (kPa default) with all pressures set to ``0`` even on vehicles
    configured for PSI. Without this guard the unit flips PSI → kPa
    while the numeric pressures stay PSI-scale, so HA's unit conversion
    divides every reading by ~6.89 until the next MQTT push restores
    the real unit.
    """

    def test_zero_pressures_preserve_previous_unit(self) -> None:
        previous = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.PSI),
                "leftFrontTirepressure": 38.4,
                "rightFrontTirepressure": 37.2,
                "leftRearTirepressure": 37.9,
                "rightRearTirepressure": 37.7,
            }
        )
        incoming = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.KPA),
                "leftFrontTirepressure": 0,
                "rightFrontTirepressure": 0,
                "leftRearTirepressure": 0,
                "rightRearTirepressure": 0,
            }
        )

        filtered = apply_realtime_filters(previous, incoming)

        assert filtered.tire_press_unit == TirePressureUnit.PSI

    def test_nonzero_pressures_accept_incoming_unit(self) -> None:
        previous = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.PSI),
                "leftFrontTirepressure": 38.4,
                "rightFrontTirepressure": 37.2,
                "leftRearTirepressure": 37.9,
                "rightRearTirepressure": 37.7,
            }
        )
        incoming = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.KPA),
                "leftFrontTirepressure": 250.0,
                "rightFrontTirepressure": 245.0,
                "leftRearTirepressure": 252.0,
                "rightRearTirepressure": 248.0,
            }
        )

        filtered = apply_realtime_filters(previous, incoming)

        assert filtered.tire_press_unit == TirePressureUnit.KPA

    def test_zero_pressures_first_poll_drops_unit(self) -> None:
        # Without a previous value the guard falls back to None (matches
        # the rest of the zero-drop family on first poll: the all-zero
        # HTTP placeholder is rejected wholesale, including its default
        # unit).
        incoming = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.KPA),
                "leftFrontTirepressure": 0,
                "rightFrontTirepressure": 0,
                "leftRearTirepressure": 0,
                "rightRearTirepressure": 0,
            }
        )

        filtered = apply_realtime_filters(None, incoming)

        assert filtered.tire_press_unit is None

    def test_partial_pressures_accept_incoming_unit(self) -> None:
        # If even one tire pressure carries a real reading the incoming
        # unit is considered trustworthy and replaces the previous.
        previous = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.PSI),
                "leftFrontTirepressure": 38.4,
            }
        )
        incoming = VehicleRealtimeData.model_validate(
            {
                "tirePressUnit": int(TirePressureUnit.KPA),
                "leftFrontTirepressure": 260.0,
                "rightFrontTirepressure": 0,
                "leftRearTirepressure": 0,
                "rightRearTirepressure": 0,
            }
        )

        filtered = apply_realtime_filters(previous, incoming)

        assert filtered.tire_press_unit == TirePressureUnit.KPA


# ---------------------------------------------------------------------------
# Hybrid leg splitting (energy_type-aware parsing)
# ---------------------------------------------------------------------------


class TestEnergyTypeLegSplit:
    """VehicleRealtimeData parses combined consumption strings into _ev/_fuel
    sub-fields, branching on the energy_type validation context."""

    def test_et0_ev_only(self) -> None:
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "6.1",
                "nearestEnergyConsumption": "6.1",
                "nearestEnergyConsumptionUnit": "kW·h/100km",
                "recent50kmEnergy": "6.1kW·h/100km",
                "totalEnergy": "19.7kW·h/100km",
                "totalConsumptionEn": "19.7kW·h/100km",
            },
            context={"energy_type": EnergyType.EV},
        )
        assert m.energy_consumption_ev == 6.1
        assert m.energy_consumption_fuel is None
        assert m.nearest_energy_consumption_ev == 6.1
        assert m.nearest_energy_consumption_fuel is None
        assert m.recent_50km_energy_ev == 6.1
        assert m.recent_50km_energy_fuel is None
        assert m.total_energy_ev == 19.7
        assert m.total_energy_fuel is None
        assert m.total_consumption_en_ev == 19.7
        assert m.total_consumption_en_fuel is None

    def test_et1_fuel_only_routes_bare_numbers_to_fuel(self) -> None:
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "8.4",
                "totalConsumptionEn": "3.5L/100km",
            },
            context={"energy_type": EnergyType.ICE},
        )
        assert m.energy_consumption_ev is None
        assert m.energy_consumption_fuel == 8.4
        assert m.total_consumption_en_ev is None
        assert m.total_consumption_en_fuel == 3.5

    def test_et2_combined_splits_both_legs(self) -> None:
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "6.1+8.4",
                "recent50kmEnergy": "6.1kW·h/100km+8.4L/100km",
                "totalEnergy": "19.7kW·h/100km+3.5L/100km",
                "totalConsumptionEn": "(19.7kW·h+3.5L)/100km",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        assert (m.energy_consumption_ev, m.energy_consumption_fuel) == (6.1, 8.4)
        assert (m.recent_50km_energy_ev, m.recent_50km_energy_fuel) == (6.1, 8.4)
        assert (m.total_energy_ev, m.total_energy_fuel) == (19.7, 3.5)
        assert (m.total_consumption_en_ev, m.total_consumption_en_fuel) == (19.7, 3.5)

    def test_et2_nearest_uses_unit_field_to_classify(self) -> None:
        """At energy_type=2 the cloud returns a single-leg petrol value here;
        the unit field 'L/100km' is the authoritative classifier."""
        m = VehicleRealtimeData.model_validate(
            {
                "nearestEnergyConsumption": "10.1",
                "nearestEnergyConsumptionUnit": "L/100km",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        assert m.nearest_energy_consumption_ev is None
        assert m.nearest_energy_consumption_fuel == 10.1

    def test_no_context_defaults_to_ev(self) -> None:
        """Bare numeric without context falls back to EV (preserves
        backwards-compatible behaviour for callers not passing context)."""
        m = VehicleRealtimeData.model_validate({"energyConsumption": "6.1"})
        assert m.energy_consumption_ev == 6.1
        assert m.energy_consumption_fuel is None

    def test_sentinel_value_yields_no_legs(self) -> None:
        m = VehicleRealtimeData.model_validate(
            {"energyConsumption": "--"},
            context={"energy_type": EnergyType.HYBRID},
        )
        assert m.energy_consumption_ev is None
        assert m.energy_consumption_fuel is None

    def test_unit_companion_strings_populated(self) -> None:
        """Each per-leg float has a parallel ``_unit`` string."""
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "6.1+8.4",
                "totalEnergy": "19.7kW·h/100km+3.5L/100km",
                "totalConsumptionEn": "(19.7kW·h+3.5L)/100km",
                "totalConsumption": "(19.7度+3.5升)/百公里",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        assert m.energy_consumption_ev_unit == "kWh/100km"
        assert m.energy_consumption_fuel_unit == "L/100km"
        assert m.total_energy_ev_unit == "kWh/100km"
        assert m.total_energy_fuel_unit == "L/100km"
        assert m.total_consumption_en_ev_unit == "kWh/100km"
        assert m.total_consumption_en_fuel_unit == "L/100km"
        assert m.total_consumption_ev_unit == "度/百公里"
        assert m.total_consumption_fuel_unit == "升/百公里"

    def test_legacy_field_aliases_to_ev_portion(self) -> None:
        """The legacy non-suffixed string field is rebound to the
        EV-portion of the original combined string for backwards compat;
        the full combined string remains in ``raw``."""
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "6.1+8.4",
                "totalEnergy": "19.7kW·h/100km+3.5L/100km",
                "totalConsumptionEn": "(19.7kW·h+3.5L)/100km",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        # Legacy aliases hold the EV-portion only
        assert m.energy_consumption == "6.1"
        assert m.total_energy == "19.7kW·h/100km"
        assert m.total_consumption_en == "19.7kW·h/100km"
        # Original combined strings preserved in raw
        assert m.raw["energyConsumption"] == "6.1+8.4"
        assert m.raw["totalConsumptionEn"] == "(19.7kW·h+3.5L)/100km"

    def test_et2_nearest_derives_per_leg_from_energy_consumption(self) -> None:
        """At energy_type=2, ``nearestEnergyConsumption`` carries the
        equivalent-petrol number (matches ``avgEqOilConsumption`` in
        getEnergyConsumption). The actual per-leg nearest averages are
        packed into ``energyConsumption`` (`"6.1+8.4"`), and the parser
        surfaces those into ``nearest_energy_consumption_ev/_fuel`` so
        backwards-compat is preserved (legacy alias picks up the EV leg)."""
        m = VehicleRealtimeData.model_validate(
            {
                "energyConsumption": "6.1+8.4",
                "nearestEnergyConsumption": "10.1",
                "nearestEnergyConsumptionUnit": "L/100km",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        # Per-leg fields derive from energyConsumption, not the eq-oil number
        assert m.nearest_energy_consumption_ev == 6.1
        assert m.nearest_energy_consumption_ev_unit == "kWh/100km"
        assert m.nearest_energy_consumption_fuel == 8.4
        assert m.nearest_energy_consumption_fuel_unit == "L/100km"
        # Legacy alias matches the et=0 EV-value behaviour
        assert m.nearest_energy_consumption == "6.1"
        assert m.nearest_energy_consumption_unit == "kWh/100km"
        # Eq-petrol value still recoverable via raw
        assert m.raw["nearestEnergyConsumption"] == "10.1"

    def test_et2_nearest_falls_back_to_fuel_when_no_energy_consumption(self) -> None:
        """If energyConsumption is missing (degenerate hybrid response), the
        legacy alias falls back to the petrol leg via the existing rule."""
        m = VehicleRealtimeData.model_validate(
            {
                "nearestEnergyConsumption": "10.1",
                "nearestEnergyConsumptionUnit": "L/100km",
            },
            context={"energy_type": EnergyType.HYBRID},
        )
        assert m.nearest_energy_consumption_ev is None
        assert m.nearest_energy_consumption_fuel == 10.1
        assert m.nearest_energy_consumption == "10.1"
        assert m.nearest_energy_consumption_unit == "L/100km"

    def test_legacy_alias_does_not_fall_back_for_pure_ice(self) -> None:
        """Pure-ICE vehicles (energy_type=1) keep legacy aliases as None —
        their petrol data is exposed exclusively via _fuel fields."""
        m = VehicleRealtimeData.model_validate(
            {"energyConsumption": "8.4"},
            context={"energy_type": EnergyType.ICE},
        )
        assert m.energy_consumption_fuel == 8.4
        assert m.energy_consumption is None


# ---------------------------------------------------------------------------
# EnergyConsumption — getEnergyConsumption response parsing
# ---------------------------------------------------------------------------


from pybyd.models.energy import EnergyConsumption  # noqa: E402


class TestEnergyConsumptionParsing:
    """Parse the four-section getEnergyConsumption response.

    Fixtures mirror the real captures in
    captures/logs_decrypted/force_energy_{0,1,2}/.
    """

    PT0_RAW = {
        "selfGraph": {
            "energyConsumption": ["8.3", "8.4", "8.0", "8.0", "8.7", "10.1", "6.1"],
            "energyConsumptionUnit": "kWh/100km",
        },
        "cumulativeEnergyConsumption": {
            "mileageUnit": "km",
            "evUnit": "kWh/100km",
            "avgOilConsumption": "--",
            "avgEvConsumption": "19.7",
            "oilUnit": "--",
            "totalMileage": "443",
        },
        "time": 1778212356,
        "nearestEnergyConsumption": {
            "driveDistribution": "99",
            "otherDistribution": "0",
            "evConsumption": "3.05",
            "electDistribution": "1",
            "avgOilConsumption": "--",
            "evValueUnit": "kW·h",
            "airDistribution": "0",
            "avgEvConsumption": "6.1",
            "avgEqOilConsumption": "--",
            "oilUnit": "--",
            "evUnit": "kWh/100km",
            "oilConsumption": "--",
            "oilValueUnit": "--",
        },
        "autoModelGraph": {
            "energyConsumption": ["0", "0", "0", "0", "0", "0", "0"],
            "energyConsumptionUnit": "kWh/100km",
        },
    }

    PT2_RAW = {
        "selfGraph": {
            "energyConsumption": ["8.3", "8.4", "8.0", "8.0", "8.7", "10.1", "10.1"],
            "energyConsumptionUnit": "L/100km",
        },
        "cumulativeEnergyConsumption": {
            "mileageUnit": "km",
            "evUnit": "kWh/100km",
            "avgOilConsumption": "3.5",
            "avgEvConsumption": "19.7",
            "oilUnit": "L/100km",
            "totalMileage": "443",
        },
        "time": 1778212356,
        "nearestEnergyConsumption": {
            "driveDistribution": "99",
            "otherDistribution": "0",
            "evConsumption": "3.05",
            "electDistribution": "1",
            "avgOilConsumption": "8.4",
            "evValueUnit": "kW·h",
            "airDistribution": "0",
            "avgEvConsumption": "6.1",
            "avgEqOilConsumption": "10.1",
            "oilUnit": "L/100km",
            "evUnit": "kWh/100km",
            "oilConsumption": "4.2",
            "oilValueUnit": "L",
        },
        "autoModelGraph": {
            "energyConsumption": ["8.3", "8.3", "8.3", "8.2", "8.2", "8.4", "8.4"],
            "energyConsumptionUnit": "L/100km",
        },
    }

    def test_pt0_ev_view(self) -> None:
        m = EnergyConsumption.model_validate(self.PT0_RAW)
        assert m.self_graph is not None
        assert m.self_graph.energy_consumption == [8.3, 8.4, 8.0, 8.0, 8.7, 10.1, 6.1]
        assert m.self_graph.energy_consumption_unit == "kWh/100km"
        assert m.cumulative_energy_consumption is not None
        assert m.cumulative_energy_consumption.avg_ev_consumption == 19.7
        assert m.cumulative_energy_consumption.avg_oil_consumption is None
        assert m.cumulative_energy_consumption.total_mileage == 443.0
        assert m.nearest_energy_consumption is not None
        assert m.nearest_energy_consumption.avg_ev_consumption == 6.1
        assert m.nearest_energy_consumption.ev_consumption == 3.05
        assert m.nearest_energy_consumption.avg_oil_consumption is None
        assert m.nearest_energy_consumption.drive_distribution == 99
        assert m.nearest_energy_consumption.elect_distribution == 1
        assert m.timestamp is not None

    def test_pt2_hybrid_view(self) -> None:
        m = EnergyConsumption.model_validate(self.PT2_RAW)
        c = m.cumulative_energy_consumption
        assert c is not None
        assert c.avg_ev_consumption == 19.7
        assert c.avg_oil_consumption == 3.5
        assert c.ev_unit == "kWh/100km"
        assert c.oil_unit == "L/100km"
        n = m.nearest_energy_consumption
        assert n is not None
        assert (n.avg_ev_consumption, n.avg_oil_consumption) == (6.1, 8.4)
        assert (n.ev_consumption, n.oil_consumption) == (3.05, 4.2)
        assert n.avg_eq_oil_consumption == 10.1
        g = m.auto_model_graph
        assert g is not None
        assert g.energy_consumption == [8.3, 8.3, 8.3, 8.2, 8.2, 8.4, 8.4]

    def test_sentinel_strings_become_none(self) -> None:
        """`"--"` numeric sentinels become None on the parsed fields."""
        m = EnergyConsumption.model_validate(
            {"cumulativeEnergyConsumption": {"avgOilConsumption": "--", "avgEvConsumption": "--"}}
        )
        assert m.cumulative_energy_consumption is not None
        assert m.cumulative_energy_consumption.avg_ev_consumption is None
        assert m.cumulative_energy_consumption.avg_oil_consumption is None

    def test_empty_payload_yields_empty_sections(self) -> None:
        m = EnergyConsumption.model_validate({})
        assert m.self_graph is None
        assert m.cumulative_energy_consumption is None
        assert m.nearest_energy_consumption is None
        assert m.auto_model_graph is None
        assert m.timestamp is None


# ---------------------------------------------------------------------------
# HVAC placeholder filter
# ---------------------------------------------------------------------------

# The asleep-HVAC placeholder as observed on the wire: every
# sensor-population field literal 0, setpoints 0, state enums inert.
_HVAC_PLACEHOLDER_RAW = {
    "tempInCar": 0,
    "tempOutCar": 0,
    "pm": 0,
    "acSwitch": 0,
    "status": 0,
    "mainSettingTemp": 0.0,
    "refrigeratorState": -1,
}


class TestHvacPlaceholderFilter:
    """apply_hvac_filters: the all-zero sensor placeholder must never surface."""

    def test_first_poll_placeholder_drops_sensor_fields(self) -> None:
        # A parked car's HVAC block sleeps within minutes, so the first
        # poll after a consumer restart almost always lands on the
        # placeholder. It must be rejected wholesale (like the realtime
        # zero-drop family), not accepted as a baseline of literal 0s.
        incoming = HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW)

        filtered = apply_hvac_filters(None, incoming)

        assert filtered.temp_in_car is None
        assert filtered.temp_out_car is None
        assert filtered.pm is None

    def test_first_poll_sentinel_and_zero_placeholder_drops_fields(self) -> None:
        # The exact wire shape captured from an affected vehicle: tempInCar
        # arrives as the -129 sentinel (normalised to None by the model)
        # while tempOutCar and pm are literal 0. The all-zero-OR-MISSING
        # triad must be rejected wholesale on first poll too — this guards
        # the sentinel-normalisation → placeholder-filter interaction.
        incoming = HvacStatus.model_validate(
            {
                "tempInCar": -129,
                "tempOutCar": 0,
                "pm": 0,
                "acSwitch": 0,
                "status": 0,
                "mainSettingTemp": 0.0,
                "refrigeratorState": -1,
            }
        )
        assert incoming.temp_in_car is None

        filtered = apply_hvac_filters(None, incoming)

        assert filtered.temp_in_car is None
        assert filtered.temp_out_car is None
        assert filtered.pm is None

    def test_placeholder_clears_fields_despite_previous_real_values(self) -> None:
        # Previous real readings are NOT pinned over the placeholder:
        # temperatures and particulates keep changing while the car
        # sleeps, so a pinned reading goes stale while looking live.
        # A poll with no live sensor data yields no sensor values.
        previous = HvacStatus.model_validate({"tempInCar": 22.0, "tempOutCar": 14.0, "pm": 8.0})
        incoming = HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW)

        filtered = apply_hvac_filters(previous, incoming)

        assert filtered.temp_in_car is None
        assert filtered.temp_out_car is None
        assert filtered.pm is None

    def test_placeholder_does_not_resurface_zero_over_none_previous(self) -> None:
        # previous.temp_in_car is None (a -129 sentinel was normalised
        # away); the placeholder's literal 0 must resolve to None, and
        # the remaining fields clear alongside it.
        previous = HvacStatus.model_validate({"tempInCar": -129, "tempOutCar": 14.0, "pm": 8.0})
        assert previous.temp_in_car is None
        incoming = HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW)

        filtered = apply_hvac_filters(previous, incoming)

        assert filtered.temp_in_car is None
        assert filtered.temp_out_car is None
        assert filtered.pm is None

    def test_placeholder_chain_stays_none_after_first_poll_rejection(self) -> None:
        # Two consecutive placeholders from a cold start: the second one
        # must not resurface 0 just because the (rejected) first left all
        # sensor fields at None.
        first = apply_hvac_filters(None, HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW))
        second = apply_hvac_filters(first, HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW))

        assert second.temp_in_car is None
        assert second.temp_out_car is None
        assert second.pm is None

    def test_mixed_payload_passes_through(self) -> None:
        # A payload carrying any real sensor value is not a placeholder:
        # a genuine 0 °C winter reading on one sensor round-trips intact.
        previous = HvacStatus.model_validate({"tempInCar": 22.0, "tempOutCar": 14.0, "pm": 8.0})
        incoming = HvacStatus.model_validate({"tempInCar": 5.0, "tempOutCar": 0, "pm": 3.0})

        filtered = apply_hvac_filters(previous, incoming)

        assert filtered.temp_in_car == 5.0
        assert filtered.temp_out_car == 0
        assert filtered.pm == 3.0

    def test_first_poll_genuine_winter_zero_passes_through(self) -> None:
        # Cold-boot consumer on a winter day: exterior genuinely 0 °C but
        # the payload carries other real sensor values, so it is not a
        # placeholder — no over-suppression on first poll.
        incoming = HvacStatus.model_validate({"tempInCar": 0.5, "tempOutCar": 0, "pm": 2.0})

        filtered = apply_hvac_filters(None, incoming)

        assert filtered.temp_in_car == 0.5
        assert filtered.temp_out_car == 0
        assert filtered.pm == 2.0

    def test_genuine_zero_previous_clears_on_placeholder(self) -> None:
        # A genuine 0 accepted earlier from a mixed payload clears like
        # any other previous reading when a placeholder arrives — the
        # policy is uniform: no live data in the poll, no sensor value.
        previous = HvacStatus.model_validate({"tempInCar": 5.0, "tempOutCar": 0, "pm": 3.0})
        incoming = HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW)

        filtered = apply_hvac_filters(previous, incoming)

        assert filtered.temp_in_car is None
        assert filtered.temp_out_car is None
        assert filtered.pm is None

    def test_placeholder_still_updates_non_sensor_fields(self) -> None:
        # Only the sensor-population fields are cleared; setpoints and
        # state enums on the placeholder payload still update.
        previous = HvacStatus.model_validate(
            {"tempInCar": 22.0, "tempOutCar": 14.0, "pm": 8.0, "mainSettingTemp": 24.0}
        )
        incoming = HvacStatus.model_validate(_HVAC_PLACEHOLDER_RAW)

        filtered = apply_hvac_filters(previous, incoming)

        assert filtered.temp_out_car is None
        assert filtered.main_setting_temp == 0.0
