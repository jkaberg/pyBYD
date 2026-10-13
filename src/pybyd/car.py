"""Per-vehicle aggregate — ``BydCar``.

``BydCar`` is the primary domain object for interacting with a single
BYD vehicle.  It wraps the low-level :class:`BydClient` API methods
behind typed capability namespaces and manages vehicle state through
an internal :class:`VehicleStateEngine`.

Usage::

    car = await client.get_car(vin)

    await car.lock.lock()
    await car.hvac.start(temperature=21, duration=20)
    await car.seat.heat(SeatPosition.DRIVER, SeatLevel.HIGH)
    await car.steering.heat(on=True)
    await car.battery.heat(on=True)
    await car.finder.find()
    await car.windows.close()

    state = car.state  # immutable VehicleSnapshot
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from pybyd._capabilities.battery_heat import BatteryHeatCapability
from pybyd._capabilities.finder import FinderCapability
from pybyd._capabilities.hvac import HvacCapability
from pybyd._capabilities.lock import LockCapability
from pybyd._capabilities.seat import SeatCapability
from pybyd._capabilities.steering import SteeringCapability
from pybyd._capabilities.trunk import TrunkCapability
from pybyd._capabilities.windows import WindowsCapability
from pybyd._state_engine import ProjectionSpec, VehicleSnapshot, VehicleStateEngine
from pybyd.exceptions import BydRemoteControlError
from pybyd.models.charging import ChargingStatus
from pybyd.models.control import ChargeChangeResult
from pybyd.models.energy import EnergyConsumption
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.latest_config import VehicleCapabilities
from pybyd.models.realtime import VehicleRealtimeData
from pybyd.models.vehicle import Vehicle

if TYPE_CHECKING:
    from pybyd.client import BydClient

_logger = logging.getLogger(__name__)

_DEFAULT_PROJECTION_TTL: float = 30.0


class BydCar:
    """Per-vehicle aggregate providing typed capability namespaces.

    Holds an internal :class:`VehicleStateEngine` that manages
    immutable :class:`VehicleSnapshot` instances with command
    projections, guard windows, and value-quality validators.

    Parameters
    ----------
    client
        The authenticated :class:`BydClient` instance.
    vin
        Vehicle identification number.
    vehicle
        Vehicle metadata model.
    on_state_changed
        Optional callback fired on every accepted state mutation.
    projection_ttl
        Default TTL for command projections (seconds).
    """

    def __init__(
        self,
        client: BydClient,
        vin: str,
        vehicle: Vehicle,
        *,
        capabilities: VehicleCapabilities | None = None,
        on_state_changed: Callable[[str, VehicleSnapshot], None] | None = None,
        projection_ttl: float = _DEFAULT_PROJECTION_TTL,
    ) -> None:
        self._client = client
        self._vin = vin

        self._engine = VehicleStateEngine(
            vin,
            vehicle,
            on_state_changed=on_state_changed,
            projection_ttl=projection_ttl,
        )
        self._capabilities = capabilities or VehicleCapabilities.model_validate(
            {
                "vin": vin,
                "source": "implicit_default",
                "lock": True,
                "unlock": True,
                "climate": True,
                "car_on": True,
                "battery_heat": True,
                "steering_wheel_heat": True,
                "driver_seat_heat": True,
                "driver_seat_ventilation": True,
                "passenger_seat_heat": True,
                "passenger_seat_ventilation": True,
                "find_car": True,
                "flash_lights": True,
                "close_windows": True,
                "location": True,
            }
        )

        # --- Capability namespaces ---
        self.lock = LockCapability(
            lock_fn=client.lock,
            unlock_fn=client.unlock,
            vin=vin,
            execute_command=self._execute_command,
            available=self._capabilities.lock,
        )
        self.hvac = HvacCapability(
            start_fn=client.start_climate,
            stop_fn=client.stop_climate,
            schedule_fn=client.schedule_climate,
            vin=vin,
            execute_command=self._execute_command,
            get_state=lambda: self._engine.snapshot,
            available=self._capabilities.climate,
        )
        self.seat = SeatCapability(
            set_seat_climate_fn=client.set_seat_climate,
            vin=vin,
            get_state=lambda: self._engine.snapshot,
            execute_command=self._execute_command,
            driver_heat_available=self._capabilities.driver_seat_heat,
            driver_ventilation_available=self._capabilities.driver_seat_ventilation,
            passenger_heat_available=self._capabilities.passenger_seat_heat,
            passenger_ventilation_available=self._capabilities.passenger_seat_ventilation,
        )
        self.steering = SteeringCapability(
            set_seat_climate_fn=client.set_seat_climate,
            vin=vin,
            get_state=lambda: self._engine.snapshot,
            execute_command=self._execute_command,
            available=self._capabilities.steering_wheel_heat,
        )
        self.battery = BatteryHeatCapability(
            set_battery_heat_fn=client.set_battery_heat,
            vin=vin,
            execute_command=self._execute_command,
            available=self._capabilities.battery_heat,
        )
        self.finder = FinderCapability(
            find_fn=client.find_car,
            flash_fn=client.flash_lights,
            vin=vin,
            execute_command=self._execute_command,
            find_available=self._capabilities.find_car,
            flash_available=self._capabilities.flash_lights,
        )
        self.windows = WindowsCapability(
            close_fn=client.close_windows,
            open_fn=client.open_windows,
            vin=vin,
            execute_command=self._execute_command,
            close_available=self._capabilities.close_windows,
            open_available=self._capabilities.open_windows,
        )
        self.trunk = TrunkCapability(
            open_fn=client.open_trunk,
            close_fn=client.close_trunk,
            vin=vin,
            execute_command=self._execute_command,
            open_available=self._capabilities.open_trunk,
            close_available=self._capabilities.close_trunk,
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def vin(self) -> str:
        """Vehicle identification number."""
        return self._vin

    @property
    def state(self) -> VehicleSnapshot:
        """Current projected vehicle state (immutable snapshot)."""
        return self._engine.snapshot

    @property
    def capabilities(self) -> VehicleCapabilities:
        """Normalized capability availability for this vehicle."""
        return self._capabilities

    # ------------------------------------------------------------------
    # Data fetch methods
    # ------------------------------------------------------------------

    async def update_realtime(self) -> VehicleRealtimeData:
        """Fetch fresh realtime data and merge into state engine."""
        data = await self._client.get_vehicle_realtime(self._vin)
        await self._engine.update_realtime(data)
        return data

    async def update_hvac(self) -> HvacStatus:
        """Fetch fresh HVAC data and merge into state engine."""
        data = await self._client.get_hvac_status(self._vin)
        await self._engine.update_hvac(data)
        return data

    async def update_gps(self) -> GpsInfo:
        """Fetch fresh GPS data and merge into state engine."""
        data = await self._client.get_gps_info(self._vin)
        await self._engine.update_gps(data)
        return data

    async def update_charging(self) -> ChargingStatus:
        """Fetch fresh charging data + schedule and merge both into state engine.

        The cloud returns both views from one ``homePage`` call, so a
        force-refresh updates every charging-related field — live state
        AND the configured schedule — in a single round-trip.  MQTT
        pushes (which only carry live state) continue to flow through
        :meth:`handle_mqtt_charging` and never touch the schedule
        section, so a partial-state push can't clobber the schedule.
        """
        status, schedule = await self._client.get_charging_homepage(self._vin)
        await self._engine.update_charging(status)
        await self._engine.update_charging_schedule(schedule)
        return status

    async def start_charging(self) -> ChargeChangeResult:
        """Start charging immediately and wait for the toggle to settle.

        Routes through the BydClient ``_trigger_and_poll`` pipeline:
        prefers the BYD MQTT ``smartCharge`` push for the result and
        falls back to ``/control/smartCharge/changeResult`` polling.

        Raises :class:`BydRemoteControlError` if the toggle reports
        failure or doesn't settle within the polling window.
        """
        return await self._client.start_charging(self._vin)

    async def stop_charging(self) -> ChargeChangeResult:
        """Not supported — the BYD cloud cannot stop an active charge.

        Delegates to :meth:`BydClient.stop_charging`, which always raises
        :class:`BydEndpointNotSupportedError`: the ``changeChargeStatue``
        ``status: "0"`` path is a verified no-op (reports success without
        stopping). Stop an active charge with the key-fob/door-handle
        double-unlock instead.
        """
        return await self._client.stop_charging(self._vin)

    async def update_energy(self) -> EnergyConsumption:
        """Fetch fresh energy consumption data and merge into state engine."""
        data = await self._client.get_energy_consumption(self._vin)
        await self._engine.update_energy(data)
        return data

    async def save_charging_schedule(
        self,
        *,
        start_charge_time: str,
        end_charge_time: str,
        charge_way: str,
        enabled: bool = True,
    ) -> ChargeChangeResult:
        """Push a new smart-charging schedule and refresh the snapshot.

        Routes through :meth:`BydClient.save_charging_schedule` which
        triggers ``saveOrUpdate`` and waits for ``changeResult`` to
        report a terminal state (``res == 2`` success, anything else
        raises).  Once the cloud has confirmed the new schedule,
        refreshes the snapshot via :meth:`update_charging` so both
        the live charging section and the schedule section reflect
        the post-save state.
        """
        result = await self._client.save_charging_schedule(
            self._vin,
            start_charge_time=start_charge_time,
            end_charge_time=end_charge_time,
            charge_way=charge_way,
            enabled=enabled,
        )
        await self.update_charging()
        return result

    # ------------------------------------------------------------------
    # MQTT integration
    # ------------------------------------------------------------------

    def handle_mqtt_realtime(self, data: VehicleRealtimeData) -> None:
        """Handle an MQTT ``vehicleInfo`` push for this vehicle.

        Routes the data through the state engine's guard window (same
        path as HTTP poll results).  Called synchronously from the event
        loop; spawns an async task for the engine update.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._async_handle_mqtt(data))

    async def _async_handle_mqtt(self, data: VehicleRealtimeData) -> None:
        """Async handler for MQTT realtime data."""
        try:
            await self._engine.update_realtime(data)
        except Exception:
            _logger.debug("Failed to process MQTT realtime for vin=%s", self._vin, exc_info=True)

    def handle_mqtt_charging(self, data: ChargingStatus) -> None:
        """Handle an MQTT ``smartCharge`` push for this vehicle.

        Parses the payload as :class:`ChargingStatus` and routes it
        through the state engine.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._async_handle_mqtt_charging(data))

    async def _async_handle_mqtt_charging(self, data: ChargingStatus) -> None:
        """Async handler for MQTT charging data."""
        try:
            await self._engine.update_charging(data)
        except Exception:
            _logger.debug("Failed to process MQTT charging for vin=%s", self._vin, exc_info=True)

    def handle_mqtt_energy(self, data: EnergyConsumption) -> None:
        """Handle an MQTT ``energyConsumption`` push for this vehicle.

        Parses the payload as :class:`EnergyConsumption` and routes it
        through the state engine.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._async_handle_mqtt_energy(data))

    async def _async_handle_mqtt_energy(self, data: EnergyConsumption) -> None:
        """Async handler for MQTT energy consumption data."""
        try:
            await self._engine.update_energy(data)
        except Exception:
            _logger.debug("Failed to process MQTT energy for vin=%s", self._vin, exc_info=True)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def _execute_command(
        self,
        command_fn: Callable[[], Awaitable[Any]],
        projections: list[ProjectionSpec],
    ) -> None:
        """Execute a vehicle command with projection lifecycle management.

        1. Acquire the per-VIN lock (serialises commands)
        2. Register projections (optimistic state update)
        3. Execute the command
        4. On ANY error (incl. ``BydRemoteControlError`` rejection/timeout) —
           rollback projections and re-raise so the failure surfaces

        The optimistic projection only persists when the command actually
        succeeded; the next regular poll cycle (or MQTT push) reconciles
        actual vehicle state.
        """
        async with self._engine.lock:
            command_id = self._engine.register_projections(projections) if projections else ""
            try:
                await command_fn()
            except BydRemoteControlError:
                # Command not confirmed (rejected / timeout / car asleep).
                # Roll back the optimistic state and surface the error instead
                # of painting a fake "success" that the next poll would undo.
                _logger.debug(
                    "Remote control not confirmed for vin=%s (cmd=%s) — " "rolling back projection and raising",
                    self._vin,
                    command_id,
                )
                if command_id:
                    self._engine.rollback_projections(command_id)
                raise
            except Exception:
                if command_id:
                    self._engine.rollback_projections(command_id)
                raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Clean up resources.

        Called when the car is removed from the client or on shutdown.
        Currently a no-op — retained as a lifecycle hook for future use.
        """
