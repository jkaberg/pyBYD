"""Vehicle state engine — immutable snapshot management with command projections.

This module is the core of the pyBYD state management layer, providing
merge, projection, guard, reconcile, and rollback semantics.

Key concepts
------------
- **VehicleSnapshot** — frozen composite of all known vehicle state sections.
- **ProjectionSpec** — lightweight spec created by capabilities to describe
  expected post-command state.
- **FieldProjection** — a ``ProjectionSpec`` enriched with lifecycle metadata
  (command ID, TTL, creation timestamp).
- **VehicleStateEngine** — per-vehicle engine that merges incoming data,
  applies value-quality validators, manages projections and guard windows,
  and emits ``on_state_changed`` callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from pybyd._validators import apply_gps_filters, apply_hvac_filters, apply_realtime_filters
from pybyd.models.charging import ChargingStatus
from pybyd.models.energy import EnergyConsumption
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import VehicleRealtimeData
from pybyd.models.smart_charging import SmartChargingSchedule
from pybyd.models.vehicle import Vehicle

_logger = logging.getLogger(__name__)

# Sentinel for missing attributes
_MISSING: object = object()


# ------------------------------------------------------------------
# Immutable snapshot
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VehicleSnapshot:
    """Immutable composite snapshot of all known vehicle state.

    A new instance is created on every accepted state change.
    Consumers compare identity (``is``) or field values to detect changes.
    """

    vehicle: Vehicle
    realtime: VehicleRealtimeData | None = None
    hvac: HvacStatus | None = None
    gps: GpsInfo | None = None
    charging: ChargingStatus | None = None
    energy: EnergyConsumption | None = None
    charging_schedule: SmartChargingSchedule | None = None


# ------------------------------------------------------------------
# Projection data classes
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Lightweight specification for a field projection.

    Created by capability classes to describe the expected post-command
    state.  The :class:`VehicleStateEngine` wraps these into full
    :class:`FieldProjection` instances with lifecycle metadata.
    """

    section: str
    """State section: ``"realtime"``, ``"hvac"``, ``"gps"``, ``"charging"``,
    ``"energy"``, ``"charging_schedule"``."""

    field_name: str
    """Field name within the section model (e.g. ``"left_front_door_lock"``)."""

    expected_value: Any
    """The value this field should have after the command succeeds."""


@dataclass(frozen=True, slots=True)
class FieldProjection:
    """A single field-level state projection with lifecycle metadata."""

    section: str
    field_name: str
    expected_value: Any
    command_id: str
    created_at: float = dc_field(default_factory=time.monotonic)
    ttl: float = 30.0

    @property
    def is_expired(self) -> bool:
        """Whether this projection has exceeded its TTL."""
        return (time.monotonic() - self.created_at) > self.ttl


# ------------------------------------------------------------------
# State engine
# ------------------------------------------------------------------


class VehicleStateEngine:
    """Per-vehicle state management engine.

    Manages the lifecycle of:

    - **Partial merges** — each ``update_*()`` replaces one section
    - **Value-quality validators** — GPS Null Island, zero-SOC spike
    - **Command projections** — optimistic state overlaid on base data
    - **Guard windows** — reject contradicting data during projection TTL
    - **Rollback** — revert projections on command failure

    All mutations are serialised behind a single :class:`asyncio.Lock`.
    """

    def __init__(
        self,
        vin: str,
        vehicle: Vehicle,
        *,
        on_state_changed: Callable[[str, VehicleSnapshot], None] | None = None,
        projection_ttl: float = 30.0,
    ) -> None:
        self._vin = vin
        self._vehicle = vehicle
        self._on_state_changed = on_state_changed
        self._projection_ttl = projection_ttl
        self._lock = asyncio.Lock()
        self._next_command_id = 0

        # Base (accepted) state per section
        self._base_realtime: VehicleRealtimeData | None = None
        self._base_hvac: HvacStatus | None = None
        self._base_gps: GpsInfo | None = None
        self._base_charging: ChargingStatus | None = None
        self._base_energy: EnergyConsumption | None = None
        self._base_charging_schedule: SmartChargingSchedule | None = None

        # Active projections
        self._projections: list[FieldProjection] = []

        # Current projected snapshot
        self._snapshot = VehicleSnapshot(vehicle=vehicle)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def lock(self) -> asyncio.Lock:
        """The per-vehicle mutation/command lock."""
        return self._lock

    @property
    def snapshot(self) -> VehicleSnapshot:
        """Current projected vehicle state snapshot."""
        return self._snapshot

    @property
    def projection_ttl(self) -> float:
        """Default TTL for new projections (seconds)."""
        return self._projection_ttl

    @property
    def active_projections(self) -> list[FieldProjection]:
        """Currently active (non-expired) projections (read-only copy)."""
        return [p for p in self._projections if not p.is_expired]

    # ------------------------------------------------------------------
    # Projection management (caller must hold self._lock)
    # ------------------------------------------------------------------

    def register_projections(self, specs: list[ProjectionSpec]) -> str:
        """Register field projections and rebuild the snapshot.

        Returns the ``command_id`` grouping these projections.

        .. important:: Must be called while holding :pyattr:`lock`.
        """
        command_id = self._generate_command_id()
        now = time.monotonic()
        for spec in specs:
            self._projections.append(
                FieldProjection(
                    section=spec.section,
                    field_name=spec.field_name,
                    expected_value=spec.expected_value,
                    command_id=command_id,
                    created_at=now,
                    ttl=self._projection_ttl,
                )
            )
        self._rebuild_snapshot()
        return command_id

    def rollback_projections(self, command_id: str) -> None:
        """Remove all projections for *command_id* and rebuild snapshot.

        .. important:: Must be called while holding :pyattr:`lock`.
        """
        before = len(self._projections)
        self._projections = [p for p in self._projections if p.command_id != command_id]
        removed = before - len(self._projections)
        if removed:
            _logger.debug("Rolled back %d projections for %s (vin=%s)", removed, command_id, self._vin)
        self._rebuild_snapshot()

    # ------------------------------------------------------------------
    # Data updates (acquire lock internally)
    # ------------------------------------------------------------------

    async def update_realtime(self, data: VehicleRealtimeData) -> None:
        """Merge incoming realtime data through centralized filters and guard window."""
        async with self._lock:
            validated = apply_realtime_filters(self._base_realtime, data)
            self._base_realtime = validated
            self._reconcile_projections("realtime", validated)
            self._rebuild_snapshot()

    async def update_hvac(self, data: HvacStatus) -> None:
        """Merge incoming HVAC data through centralized filters and guard window."""
        async with self._lock:
            validated = apply_hvac_filters(self._base_hvac, data)
            self._base_hvac = validated
            self._reconcile_projections("hvac", validated)
            self._rebuild_snapshot()

    async def update_gps(self, data: GpsInfo) -> None:
        """Merge incoming GPS data through centralized filters and guard window."""
        async with self._lock:
            validated = apply_gps_filters(self._base_gps, data)
            self._base_gps = validated
            if validated is not None:
                self._reconcile_projections("gps", validated)
            self._rebuild_snapshot()

    async def update_charging(self, data: ChargingStatus) -> None:
        """Merge incoming charging data through the guard window."""
        async with self._lock:
            self._base_charging = data
            self._reconcile_projections("charging", data)
            self._rebuild_snapshot()

    async def update_energy(self, data: EnergyConsumption) -> None:
        """Merge incoming energy consumption data."""
        async with self._lock:
            self._base_energy = data
            self._reconcile_projections("energy", data)
            self._rebuild_snapshot()

    async def update_charging_schedule(self, data: SmartChargingSchedule) -> None:
        """Merge incoming smart-charging schedule data."""
        async with self._lock:
            self._base_charging_schedule = data
            self._reconcile_projections("charging_schedule", data)
            self._rebuild_snapshot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_command_id(self) -> str:
        self._next_command_id += 1
        return f"cmd-{self._next_command_id}"

    def _reconcile_projections(self, section: str, data: object) -> None:
        """Remove projections confirmed by incoming data; expire stale ones.

        A projection is **confirmed** when the incoming data's field value
        matches the projection's expected value.  Confirmed and expired
        projections are removed; contradicting projections are kept (guard
        window — the projected value continues to overlay the base data).
        """
        remaining: list[FieldProjection] = []
        for p in self._projections:
            if p.is_expired:
                _logger.debug(
                    "Projection expired: %s.%s (cmd=%s, vin=%s)",
                    p.section,
                    p.field_name,
                    p.command_id,
                    self._vin,
                )
                continue
            if p.section != section:
                remaining.append(p)
                continue
            # Check if incoming data confirms this projection
            actual = getattr(data, p.field_name, _MISSING)
            if actual is not _MISSING and actual == p.expected_value:
                _logger.debug(
                    "Projection confirmed: %s.%s = %s (cmd=%s, vin=%s)",
                    p.section,
                    p.field_name,
                    actual,
                    p.command_id,
                    self._vin,
                )
                continue  # confirmed — drop projection
            # Contradicting or unknown — keep projection (guard window)
            remaining.append(p)
        self._projections = remaining

    def _get_projection_updates(self, section: str) -> dict[str, Any]:
        """Collect active projection field overrides for a section."""
        updates: dict[str, Any] = {}
        for p in self._projections:
            if p.section == section and not p.is_expired:
                updates[p.field_name] = p.expected_value
        return updates

    def _project_realtime(self) -> VehicleRealtimeData | None:
        """Build projected realtime data (base + projection overlay)."""
        base = self._base_realtime
        if base is None:
            return None
        updates = self._get_projection_updates("realtime")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _project_hvac(self) -> HvacStatus | None:
        """Build projected HVAC data (base + projection overlay)."""
        base = self._base_hvac
        if base is None:
            return None
        updates = self._get_projection_updates("hvac")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _project_gps(self) -> GpsInfo | None:
        """Build projected GPS data (base + projection overlay)."""
        base = self._base_gps
        if base is None:
            return None
        updates = self._get_projection_updates("gps")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _project_charging(self) -> ChargingStatus | None:
        """Build projected charging data (base + projection overlay)."""
        base = self._base_charging
        if base is None:
            return None
        updates = self._get_projection_updates("charging")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _project_energy(self) -> EnergyConsumption | None:
        """Build projected energy data (base + projection overlay)."""
        base = self._base_energy
        if base is None:
            return None
        updates = self._get_projection_updates("energy")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _project_charging_schedule(self) -> SmartChargingSchedule | None:
        """Build projected charging-schedule data (base + projection overlay)."""
        base = self._base_charging_schedule
        if base is None:
            return None
        updates = self._get_projection_updates("charging_schedule")
        if not updates:
            return base
        return base.model_copy(update=updates)

    def _rebuild_snapshot(self) -> None:
        """Rebuild projected snapshot from base state + active projections."""
        # Clean expired projections
        self._projections = [p for p in self._projections if not p.is_expired]

        new_snapshot = VehicleSnapshot(
            vehicle=self._vehicle,
            realtime=self._project_realtime(),
            hvac=self._project_hvac(),
            gps=self._project_gps(),
            charging=self._project_charging(),
            energy=self._project_energy(),
            charging_schedule=self._project_charging_schedule(),
        )
        self._snapshot = new_snapshot

        if self._on_state_changed is not None:
            try:
                self._on_state_changed(self._vin, new_snapshot)
            except Exception:
                _logger.debug("on_state_changed callback failed for vin=%s", self._vin, exc_info=True)
