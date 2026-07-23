"""Vehicle latest-config models and normalized capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import Field

from pybyd.models._base import BydBaseModel
from pybyd.models.command_gating import known_command_function_nos

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class FeatureSpec:
    """One row in the capability registry.

    Maps a capability flag (used by integrations to gate entity creation)
    to the BYD ``functionNo`` value(s) that signal whether the vehicle
    supports it.  ``category`` distinguishes user-visible categories so
    the integration can group entries (e.g. "command" vs "diagnostic").
    """

    key: str
    name: str
    function_nos: tuple[str, ...]
    category: str
    description: str = ""


# Single source of truth for "feature key → BYD functionNo(s) → human label".
# When BYD adds new functionNos to overseas firmware, append rows here and
# the diagnostic sensor / capability gating updates automatically.
FEATURE_REGISTRY: tuple[FeatureSpec, ...] = (
    # Direct-control commands.
    FeatureSpec("lock", "Lock doors", ("1005",), "command"),
    FeatureSpec("unlock", "Unlock doors", ("1006",), "command"),
    FeatureSpec("climate", "Climate (HVAC)", ("1001", "10300001", "1015"), "command"),
    FeatureSpec("car_on", "Car on / one-tap prep", ("1001", "10300001", "1015"), "command"),
    FeatureSpec("battery_heat", "Battery heating", ("10300002",), "command"),
    FeatureSpec("steering_wheel_heat", "Steering-wheel heat", ("10030010", "10300004"), "command"),
    FeatureSpec("driver_seat_heat", "Driver seat heat", ("10030002", "10300003"), "command"),
    FeatureSpec("driver_seat_ventilation", "Driver seat ventilation", ("10030001", "10300003"), "command"),
    FeatureSpec("passenger_seat_heat", "Passenger seat heat", ("10030005", "10300003"), "command"),
    FeatureSpec("passenger_seat_ventilation", "Passenger seat ventilation", ("10030004", "10300003"), "command"),
    FeatureSpec("rear_left_seat_heat", "Rear left seat heat", ("10030007",), "command"),
    FeatureSpec("rear_right_seat_heat", "Rear right seat heat", ("10030009",), "command"),
    FeatureSpec("find_car", "Find car (horn + lights)", ("1007",), "command"),
    FeatureSpec("flash_lights", "Flash lights", ("1008",), "command"),
    FeatureSpec("close_windows", "Close windows", ("1026",), "command"),
    FeatureSpec("open_windows", "Open windows (vent)", ("1026",), "command"),
    FeatureSpec("location", "Vehicle location (GPS)", ("1014",), "metadata"),
    FeatureSpec("start_charge", "Start charging", ("1012",), "command"),
    FeatureSpec("stop_charge", "Stop charging", ("1012",), "command"),
    FeatureSpec("open_trunk", "Open trunk", ("1020",), "command"),
    FeatureSpec("close_trunk", "Close trunk", ("1021",), "command"),
    FeatureSpec("one_click_shutdown", "Shut car off", ("1031",), "command"),
    # Informational / sensor capabilities.
    FeatureSpec("child_presence_detection", "Child presence detection (CPD)", ("1009",), "sensor"),
    FeatureSpec("digital_key", "Digital key", ("1013",), "metadata"),
    FeatureSpec("nfc_key_3c", "NFC key (3C)", ("10130002",), "metadata"),
    FeatureSpec("energy_analysis", "Energy analysis", ("1022",), "sensor"),
    FeatureSpec("vehicle_health", "Vehicle health summary", ("1023",), "sensor"),
    FeatureSpec("health_tpms", "Health: tire pressure system", ("10230001",), "sensor"),
    FeatureSpec("health_steering", "Health: steering system", ("10230002",), "sensor"),
    FeatureSpec("health_srs", "Health: SRS (airbag)", ("10230003",), "sensor"),
    FeatureSpec("health_powertrain", "Health: powertrain", ("10230004",), "sensor"),
    FeatureSpec("health_battery", "Health: power battery", ("10230005",), "sensor"),
    FeatureSpec("health_esp", "Health: ESP", ("10230007",), "sensor"),
    FeatureSpec("health_charging", "Health: charging system", ("10230010",), "sensor"),
    FeatureSpec("health_braking", "Health: parking brake", ("10230011",), "sensor"),
    FeatureSpec("health_abs", "Health: ABS", ("10230012",), "sensor"),
)


def _registry_function_nos() -> set[str]:
    """All function_nos referenced by FEATURE_REGISTRY rows."""
    return {fn for spec in FEATURE_REGISTRY for fn in spec.function_nos}


def registered_latest_config_function_nos() -> frozenset[str]:
    """Return all latest-config functionNos currently registered by pyBYD.

    Relationship to command gating:
    - Command-related functionNos come from `known_command_function_nos()`.
    - Additional non-command latest-config values (parents/metadata) are listed here.

    How to add more:
    - Add new command values in `models/command_gating.py`.
    - Add non-command latest-config values in this set.
    - Keep tests in `tests/test_latest_config.py` green (`unknown_function_nos` should only contain truly new values).
    """
    return frozenset(
        set(known_command_function_nos())
        | _registry_function_nos()
        | {
            "1002",  # door/window (parent)
            "1003",  # ventilation/heating (parent)
            "1004",  # tire pressure (parent)
            "1014",  # location
            # 1030 = "one-tap prep" (一键备车): the scheduled departure /
            # pre-conditioning flow (BOOKINGAIR under the hood); no separate
            # capability flag. 1031 = "one-click shutdown" (一键熄火): an
            # instant engine-off command, exposed via the one_click_shutdown
            # capability above. Listed here so neither shows up as an unknown
            # function_no. Confirmed distinct on a live Sealion 7 (issue #161).
            "1030",
            "1031",
            "10020001",  # sunroof
            "10020002",  # hood
            "10020003",  # trunk
            "10020004",  # up windows remotely
            "10020005",  # windows
            "10030011",  # seat rows
            "10040001",  # direct tire pressure
        }
    )


def _normalize_code(code: str) -> str:
    return _NON_ALNUM_RE.sub("", code.upper())


class LatestConfigFunction(BydBaseModel):
    """One capability node from getLatestConfig (top-level or second-level)."""

    code: str = ""
    function_name: str = ""
    function_no: str = ""
    sort_num: int | None = None
    cf_fixed_second_level_list: list[LatestConfigFunction] = Field(default_factory=list)

    def iter_flat(self) -> list[LatestConfigFunction]:
        """Flatten current node and all nested second-level nodes."""
        flattened: list[LatestConfigFunction] = [self]
        for child in self.cf_fixed_second_level_list:
            flattened.extend(child.iter_flat())
        return flattened


class VehicleLatestConfig(BydBaseModel):
    """Latest per-vehicle feature configuration from BYD cloud."""

    widget_config_id: str = ""
    config_version: int | None = None
    app_config_version: int | None = None
    style_id: int | None = None
    terminal_type: int | None = None
    cf_fixed_list: list[LatestConfigFunction] = Field(default_factory=list)

    def iter_functions(self) -> list[LatestConfigFunction]:
        """Return all capability nodes including nested second-level items."""
        items: list[LatestConfigFunction] = []
        for item in self.cf_fixed_list:
            items.extend(item.iter_flat())
        return items


class VehicleCapabilities(BydBaseModel):
    """Normalized vehicle capability availability used by integrations.

    Each capability is a tri-state ``bool | None``: ``True`` if the
    vehicle's ``getLatestConfig`` lists the required functionNo(s),
    ``False`` if the lookup ran and the functionNo was absent, and
    ``None`` only when capability resolution itself failed
    (see :meth:`unknown`).

    The full set of supported features and the BYD ``functionNo`` values
    they map to lives in :data:`FEATURE_REGISTRY`.  Adding a new BYD
    feature is a single-line change there — both the model field and
    the diagnostic surface pick it up automatically.
    """

    vin: str = ""
    source: str = "latest_config"

    # Core remote-control commands (existing, retained for backwards compat).
    lock: bool | None = None
    unlock: bool | None = None
    climate: bool | None = None
    car_on: bool | None = None
    battery_heat: bool | None = None
    steering_wheel_heat: bool | None = None

    driver_seat_heat: bool | None = None
    driver_seat_ventilation: bool | None = None
    passenger_seat_heat: bool | None = None
    passenger_seat_ventilation: bool | None = None
    rear_left_seat_heat: bool | None = None
    rear_right_seat_heat: bool | None = None

    find_car: bool | None = None
    flash_lights: bool | None = None
    close_windows: bool | None = None
    open_windows: bool | None = None
    location: bool | None = None
    open_trunk: bool | None = None
    close_trunk: bool | None = None

    # Newly mapped — let integrations gate entities on these instead of
    # leaving them in ``unknown_function_nos``.
    start_charge: bool | None = None
    stop_charge: bool | None = None
    one_click_shutdown: bool | None = None
    child_presence_detection: bool | None = None
    digital_key: bool | None = None
    nfc_key_3c: bool | None = None
    energy_analysis: bool | None = None
    vehicle_health: bool | None = None
    health_tpms: bool | None = None
    health_steering: bool | None = None
    health_srs: bool | None = None
    health_powertrain: bool | None = None
    health_battery: bool | None = None
    health_esp: bool | None = None
    health_charging: bool | None = None
    health_braking: bool | None = None
    health_abs: bool | None = None

    function_nos: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    unknown_function_nos: list[str] = Field(default_factory=list)

    @classmethod
    def from_latest_config(cls, vin: str, latest: VehicleLatestConfig) -> VehicleCapabilities:
        """Build normalized capability flags from a latest-config payload.

        Notes:
        - Capability booleans are derived from `functionNo` only.
        - `unknown_function_nos` is computed against `registered_latest_config_function_nos()`.
        - Capability flags are populated from :data:`FEATURE_REGISTRY` so
          adding a new feature requires changing one place.
        """
        function_nos: set[str] = set()
        normalized_codes: set[str] = set()

        for item in latest.iter_functions():
            if item.function_no:
                function_nos.add(str(item.function_no))
            if item.code:
                normalized = _normalize_code(item.code)
                if normalized:
                    normalized_codes.add(normalized)

        known_function_nos = set(registered_latest_config_function_nos())

        unknown_function_nos = sorted(function_nos - known_function_nos)

        def require(required_function_nos: tuple[str, ...] | list[str]) -> bool:
            return any(function_no in function_nos for function_no in required_function_nos)

        # Drive every flag from the registry so the spec stays the
        # single source of truth.
        flags: dict[str, bool] = {spec.key: require(spec.function_nos) for spec in FEATURE_REGISTRY}

        return cls.model_validate(
            {
                "vin": vin,
                "source": "latest_config",
                **flags,
                "function_nos": sorted(function_nos),
                "codes": sorted(normalized_codes),
                "unknown_function_nos": unknown_function_nos,
                "raw": latest.raw,
            }
        )

    def feature_report(self) -> list[dict[str, object]]:
        """Return one dict per registered feature, with supported/unsupported state.

        Useful for diagnostic sensors and HA diagnostics downloads — gives
        the integration a flat list it can render as a feature matrix
        without having to hardcode a list of capability keys.
        """
        report: list[dict[str, object]] = []
        for spec in FEATURE_REGISTRY:
            supported = getattr(self, spec.key, None)
            report.append(
                {
                    "key": spec.key,
                    "name": spec.name,
                    "category": spec.category,
                    "function_nos": list(spec.function_nos),
                    "supported": bool(supported) if supported is not None else None,
                }
            )
        return report

    def supported_summary(self) -> tuple[int, int]:
        """Return ``(supported_count, total_count)`` across all registry features."""
        total = len(FEATURE_REGISTRY)
        supported = sum(1 for spec in FEATURE_REGISTRY if getattr(self, spec.key, False))
        return supported, total

    @classmethod
    def unknown(cls, vin: str, *, reason: str = "unavailable") -> VehicleCapabilities:
        """Return an unknown capability map (treat as unavailable by strict consumers)."""
        return cls.model_validate(
            {
                "vin": vin,
                "source": f"unknown:{reason}",
            }
        )
