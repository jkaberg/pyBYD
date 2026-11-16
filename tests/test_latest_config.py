"""Tests for FEATURE_REGISTRY-driven VehicleCapabilities.

The fixtures here use a synthetic ``functionNo`` list shaped after a
real BYD overseas vehicle dump (44 entries) but contain **no VIN,
plate, IMEI or any other identifying value**.  They are shareable as
PR evidence.
"""

from __future__ import annotations

import pytest

from pybyd.models.latest_config import (
    FEATURE_REGISTRY,
    LatestConfigFunction,
    VehicleCapabilities,
    VehicleLatestConfig,
    registered_latest_config_function_nos,
)

# ---------------------------------------------------------------------------
# Fixtures — anonymised function_no lists in the shape returned by
# /vehicle/vehicleswitch/getLatestConfig.cfFixedList[*].functionNo
# ---------------------------------------------------------------------------

# Captured from a real BYD overseas vehicle: 44 functionNos, no PII included.
_OVERSEAS_FUNCTION_NOS: list[str] = [
    "1001",
    "1002",
    "1003",
    "1004",
    "1005",
    "1006",
    "1007",
    "1008",
    "1009",
    "1012",
    "1013",
    "1014",
    "1020",
    "1021",
    "1022",
    "1023",
    "1026",
    "1030",
    "1031",
    "10020002",
    "10020003",
    "10020004",
    "10020005",
    "10030001",
    "10030002",
    "10030004",
    "10030005",
    "10030007",
    "10030009",
    "10030010",
    "10040001",
    "10130002",
    "10230001",
    "10230002",
    "10230003",
    "10230004",
    "10230005",
    "10230007",
    "10230010",
    "10230011",
    "10230012",
    "10300001",
    "10300003",
    "10300004",
]


def _build_latest(function_nos: list[str]) -> VehicleLatestConfig:
    """Build a minimal VehicleLatestConfig from a flat list of functionNos."""
    items = [LatestConfigFunction.model_validate({"functionNo": fn}) for fn in function_nos]
    return VehicleLatestConfig.model_validate({"cfFixedList": items})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_registry_covers_real_overseas_dump() -> None:
    """Every functionNo BYD reports for the test vehicle must be recognised.

    Reproduces a real overseas vehicle dump (anonymised).  Failing here
    means BYD added a functionNo that pyBYD has never mapped and an
    integration would silently miss the corresponding feature.
    """
    known = registered_latest_config_function_nos()
    unmapped = [fn for fn in _OVERSEAS_FUNCTION_NOS if fn not in known]
    assert unmapped == [], f"{len(unmapped)} unmapped functionNos: {unmapped}"


def test_from_latest_config_marks_each_registry_feature_supported_or_not() -> None:
    """Every FEATURE_REGISTRY row gets a True/False on a real dump."""
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(_OVERSEAS_FUNCTION_NOS))
    for spec in FEATURE_REGISTRY:
        value = getattr(caps, spec.key)
        assert value in (True, False), f"{spec.key} resolved to {value!r}, expected bool"


@pytest.mark.parametrize(
    "expected_key, should_be",
    [
        ("lock", True),
        ("unlock", True),
        ("climate", True),
        ("find_car", True),
        ("flash_lights", True),
        ("close_windows", True),
        ("location", True),
        ("start_charge", True),
        ("stop_charge", True),
        ("open_trunk", True),
        ("close_trunk", True),
        ("one_click_shutdown", True),
        ("child_presence_detection", True),
        ("digital_key", True),
        ("nfc_key_3c", True),
        ("vehicle_health", True),
        ("health_tpms", True),
        ("health_abs", True),
        ("rear_left_seat_heat", True),
        ("rear_right_seat_heat", True),
        # the test vehicle does NOT report 10300002 (battery heat).  Pinning this
        # so a regression that "everything is True" is caught immediately.
        ("battery_heat", False),
    ],
)
def test_specific_capability_resolution(expected_key: str, should_be: bool) -> None:
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(_OVERSEAS_FUNCTION_NOS))
    assert (
        getattr(caps, expected_key) is should_be
    ), f"expected {expected_key} to be {should_be} for the test vehicle dump"


def test_electric_ac_function_no_enables_climate_capability() -> None:
    """Some vehicles report remote HVAC as Electric A/C functionNo 1015."""
    latest = VehicleLatestConfig.model_validate(
        {
            "cfFixedList": [
                {
                    "code": "Electric A/C",
                    "functionName": "电动空调",
                    "functionNo": "1015",
                    "sortNum": 15,
                }
            ]
        }
    )
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", latest)
    assert caps.climate is True
    assert caps.car_on is True
    assert caps.unknown_function_nos == []


def test_supported_summary_pins_count() -> None:
    """Lock the count so registry growth is a deliberate, reviewed change."""
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(_OVERSEAS_FUNCTION_NOS))
    supported, total = caps.supported_summary()
    assert total == len(FEATURE_REGISTRY)
    # the test vehicle supports every registry feature except battery_heat and sentry.
    assert supported == total - 2, f"the test vehicle fixture supported={supported}/{total} (expected total-2)"


def test_feature_report_returns_one_row_per_registry_entry() -> None:
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(_OVERSEAS_FUNCTION_NOS))
    report = caps.feature_report()
    assert len(report) == len(FEATURE_REGISTRY)
    keys_in_report = {row["key"] for row in report}
    keys_in_registry = {spec.key for spec in FEATURE_REGISTRY}
    assert keys_in_report == keys_in_registry
    # Every row carries the four contractual keys integrations rely on.
    for row in report:
        assert set(row.keys()) >= {"key", "name", "category", "function_nos", "supported"}


def test_unknown_function_nos_is_empty_for_known_dump() -> None:
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(_OVERSEAS_FUNCTION_NOS))
    assert caps.unknown_function_nos == []


def test_truly_unknown_function_no_surfaces_in_unknown_list() -> None:
    """A functionNo not in the registry must end up in unknown_function_nos."""
    fns = list(_OVERSEAS_FUNCTION_NOS) + ["99999999"]
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(fns))
    assert "99999999" in caps.unknown_function_nos


def test_no_function_nos_means_no_features_supported() -> None:
    """Defensive: a vehicle with nothing reported should have all flags False."""
    caps = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest([]))
    supported, total = caps.supported_summary()
    assert supported == 0
    assert total == len(FEATURE_REGISTRY)
    for spec in FEATURE_REGISTRY:
        assert getattr(caps, spec.key) is False, f"{spec.key} should be False with no functionNos"


def test_sentry_capability_tracks_1024_function_no() -> None:
    """sentry is True when 1024 is present and False when omitted."""
    without_sentry = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(["1005"]))
    with_sentry = VehicleCapabilities.from_latest_config("VIN_PLACEHOLDER", _build_latest(["1005", "1024"]))

    assert without_sentry.sentry is False
    assert with_sentry.sentry is True
    assert with_sentry.unknown_function_nos == []
