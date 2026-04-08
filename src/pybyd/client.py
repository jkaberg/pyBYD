"""High-level async client for the BYD vehicle API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

import aiohttp

from pybyd._api import charging as _charging_api
from pybyd._api import control as _control_api
from pybyd._api import energy as _energy_api
from pybyd._api import gps as _gps_api
from pybyd._api import hvac as _hvac_api
from pybyd._api import latest_config as _latest_config_api
from pybyd._api import push_notifications as _push_api
from pybyd._api import realtime as _realtime_api
from pybyd._api import smart_charging as _smart_api
from pybyd._api import vehicle as _vehicle_api
from pybyd._api import vehicle_settings as _settings_api
from pybyd._api.login import build_login_request, parse_login_response
from pybyd._api.login_cn import build_cn_login_request, parse_cn_login_response
from pybyd._crypto.bangcle import BangcleCodec
from pybyd._crypto.wbsk import WbskCodec
from pybyd._transport import EnvelopeCodec
from pybyd._crypto.hashing import md5_hex
from pybyd._mqtt import BydMqttRuntime, MqttEvent, fetch_mqtt_bootstrap
from pybyd._transport import SecureTransport
from pybyd.config import BydConfig
from pybyd.exceptions import (
    BydControlPasswordError,
    BydDataUnavailableError,
    BydEndpointNotSupportedError,
    BydError,
    BydSessionExpiredError,
)
from pybyd.models._base import BydBaseModel
from pybyd.models.charging import ChargingStatus
from pybyd.models.command_gating import evaluate_command_gate
from pybyd.models.control import (
    BatteryHeatParams,
    ClimateScheduleParams,
    ClimateStartParams,
    CommandAck,
    CommandAckDiagnostics,
    CommandAckEvent,
    CommandLifecycleEvent,
    CommandLifecycleStatus,
    ControlParams,
    RemoteCommand,
    RemoteControlResult,
    SeatClimateParams,
    VerifyControlPasswordResponse,
)
from pybyd.models.energy import EnergyConsumption
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus
from pybyd.models.latest_config import VehicleCapabilities, VehicleLatestConfig
from pybyd.models.push_notification import PushNotificationState
from pybyd.models.realtime import VehicleRealtimeData
from pybyd.models.smart_charging import SmartChargingSchedule
from pybyd.models.vehicle import Vehicle
from pybyd.session import Session

if TYPE_CHECKING:
    from pybyd.car import BydCar

_logger = logging.getLogger(__name__)

T = TypeVar("T")
_M = TypeVar("_M", bound=BydBaseModel)

#: MQTT error codes that definitively indicate the requested data is
#: unavailable (e.g. no GPS satellite fix).  When received via MQTT with a
#: matching serial the HTTP-poll fallback is skipped entirely.
_MQTT_DATA_UNAVAILABLE_CODES: frozenset[str] = frozenset({"6051"})


@dataclass(slots=True)
class _MqttWaiter:
    """A pending MQTT wait registered by a client method.

    Matching rules: every non-None field must match the incoming event.
    Correlation is strict on VIN + ``requestSerial`` only.
    """

    vin: str
    future: asyncio.Future[dict[str, Any]]
    event_type: str | None = None
    serial: str | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class _PendingCommand:
    """A pending remote command awaiting deterministic ACK match."""

    command: str
    created_at: float = field(default_factory=time.monotonic)


def _now_ms() -> int:
    """Current epoch timestamp in milliseconds."""
    return int(time.time() * 1000)


class BydClient:
    """Async client for the BYD vehicle API.

    Usage::

        async with BydClient(config) as client:
            await client.login()
            vehicles = await client.get_vehicles()
    """

    def __init__(
        self,
        config: BydConfig,
        *,
        session: aiohttp.ClientSession | None = None,
        on_vehicle_info: Callable[[str, VehicleRealtimeData], None] | None = None,
        on_mqtt_event: Callable[[str, str, dict[str, Any]], None] | None = None,
        on_command_ack: Callable[[CommandAckEvent], None] | None = None,
        on_command_lifecycle: Callable[[CommandLifecycleEvent], None] | None = None,
        command_ack_ttl_seconds: float = 300.0,
    ) -> None:
        self._config = config
        self._external_session = session is not None
        self._http_session = session
        self._codec: EnvelopeCodec = WbskCodec() if config.is_china_region else BangcleCodec()
        self._transport: SecureTransport | None = None
        self._session: Session | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mqtt_runtime: BydMqttRuntime | None = None
        self._mqtt_waiters: list[_MqttWaiter] = []
        self._mqtt_reauth_at: float = 0.0
        self._on_vehicle_info = on_vehicle_info
        self._on_mqtt_event_cb = on_mqtt_event
        self._on_command_ack_cb = on_command_ack
        self._on_command_lifecycle_cb = on_command_lifecycle
        self._command_ack_ttl_seconds = command_ack_ttl_seconds if command_ack_ttl_seconds > 0 else 300.0
        self._pending_commands: dict[tuple[str, str], _PendingCommand] = {}
        self._pending_matched_count = 0
        self._pending_expired_count = 0
        self._pending_uncorrelated_count = 0
        # Serials from _trigger_and_poll (data polls like GPS/realtime).
        # Used to distinguish data-poll MQTT acks from remote-control command acks.
        self._data_poll_serials: set[str] = set()
        # Per-VIN BydCar instances for domain-level state management.
        self._cars: dict[str, BydCar] = {}
        # Per-VIN normalized capability availability.
        self._vehicle_capabilities: dict[str, VehicleCapabilities] = {}
        # Whether remote commands are enabled (set by verify_command_access).
        self._commands_enabled: bool = False

    # ------------------------------------------------------------------
    # Context manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BydClient:
        await self.async_start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.async_close()

    async def async_start(self) -> None:
        """Initialise the client transport and codec.

        Called automatically by ``async with BydClient(...)``, but can
        also be invoked directly when the lifecycle is managed manually.
        """
        self._loop = asyncio.get_running_loop()
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
        self._transport = SecureTransport(
            self._config,
            self._codec,
            self._http_session,
            logger=_logger,
        )
        await self._codec.async_load_tables()  # Bangcle loads .bin; WBSK loads JSON tables

    async def async_close(self) -> None:
        """Tear down the client transport and MQTT connection.

        Called automatically by ``async with BydClient(...)``, but can
        also be invoked directly when the lifecycle is managed manually.
        """
        # Close all managed BydCar instances
        for car in self._cars.values():
            car.close()
        self._cars.clear()
        self._vehicle_capabilities.clear()
        self._stop_mqtt()
        if not self._external_session and self._http_session is not None:
            await self._http_session.close()
            self._http_session = None
        self._transport = None
        self._loop = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """Authenticate against the BYD API and obtain session tokens."""
        transport = self._require_transport()
        if self._config.is_china_region:
            outer = build_cn_login_request(self._config, _now_ms())
            response = await transport.post_secure("/app/auth/login", outer)
            token = parse_cn_login_response(
                response,
                self._config.password,
                target_brand=self._config.target_brand,
            )
        else:
            outer = build_login_request(self._config, _now_ms())
            response = await transport.post_secure("/app/account/login", outer)
            token = parse_login_response(response, self._config.password)

        ttl = self._config.session_ttl if self._config.session_ttl > 0 else float("inf")
        self._session = Session(
            user_id=token.user_id,
            sign_token=token.sign_token,
            encry_token=token.encry_token,
            super_id=token.super_id,
            ttl=ttl,
        )
        # Update the running MQTT runtime's key immediately so any
        # in-flight messages benefit from the new key, then restart
        # the connection with fresh credentials.
        if self._mqtt_runtime is not None:
            self._mqtt_runtime.update_decrypt_key(self._session.content_key())
        self._stop_mqtt()
        await self._ensure_mqtt_started()

    async def ensure_session(self) -> Session:
        """Return an active session, re-authenticating if expired."""
        if self._session is not None and not self._session.is_expired:
            return self._session
        await self.login()
        assert self._session is not None  # noqa: S101
        return self._session

    def invalidate_session(self) -> None:
        """Force session invalidation (next call will re-authenticate).

        Note: ``_commands_enabled`` is intentionally preserved across
        re-authentication because the control PIN itself does not change.
        To reset command access, create a new :class:`BydClient` instance.
        """
        self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_transport(self) -> SecureTransport:
        if self._transport is None:
            raise BydError("Client not initialized. Use 'async with BydClient(...) as client:'")
        return self._transport

    def _resolve_command_pwd(self, command_pwd: str | None) -> str:
        """Normalize control password (uppercase MD5 hex of PIN)."""
        if command_pwd is not None:
            stripped = command_pwd.strip()
            if len(stripped) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in stripped):
                return stripped.upper()
            return md5_hex(stripped)
        if self._config.control_pin:
            return md5_hex(self._config.control_pin)
        return ""

    def _require_command_pwd(self, command_pwd: str | None) -> str:
        """Resolve and return the control PIN hash, or raise.

        Raises :class:`BydControlPasswordError` if no PIN is available
        *or* if command access has not been verified via
        :meth:`verify_command_access`.
        """
        if not self._commands_enabled:
            raise BydControlPasswordError(
                "Command access not available. "
                "Call verify_command_access() during setup, "
                "or check that the control PIN is correct.",
                code="commands_disabled",
                endpoint="",
            )
        resolved = self._resolve_command_pwd(command_pwd)
        if not resolved:
            raise BydControlPasswordError(
                "No control PIN configured. Set config.control_pin or pass command_pwd.",
                code="no_pin",
                endpoint="",
            )
        return resolved

    def _emit_command_lifecycle(
        self,
        *,
        status: CommandLifecycleStatus,
        vin: str,
        request_serial: str | None,
        command: str | None,
        ack: CommandAckEvent | None = None,
        reason: str | None = None,
    ) -> None:
        """Emit a lifecycle event to the optional callback."""
        cb = self._on_command_lifecycle_cb
        if cb is None:
            return
        try:
            event = CommandLifecycleEvent.model_validate(
                {
                    "status": status,
                    "vin": vin,
                    "requestSerial": request_serial,
                    "command": command,
                    "timestamp": _now_ms(),
                    "ack": ack,
                    "reason": reason,
                }
            )
            loop = self._loop

            def _invoke() -> None:
                try:
                    cb(event)
                except Exception:
                    _logger.debug("on_command_lifecycle callback failed", exc_info=True)

            if loop is not None and loop.is_running():
                loop.call_soon(_invoke)
            else:
                _invoke()
        except Exception:
            _logger.debug("on_command_lifecycle callback failed", exc_info=True)

    def _expire_pending_commands(self) -> int:
        """Expire stale pending command entries and emit lifecycle events."""
        now = time.monotonic()
        expired_keys = [
            key
            for key, pending in self._pending_commands.items()
            if (now - pending.created_at) >= self._command_ack_ttl_seconds
        ]

        for vin, serial in expired_keys:
            pending = self._pending_commands.pop((vin, serial), None)
            if pending is None:
                continue
            self._pending_expired_count += 1
            self._emit_command_lifecycle(
                status=CommandLifecycleStatus.EXPIRED,
                vin=vin,
                request_serial=serial,
                command=pending.command,
                reason="pending_ttl_exceeded",
            )

        return len(expired_keys)

    def _register_pending_command(self, vin: str, request_serial: str, command: str) -> None:
        """Register a pending command keyed by (vin, request_serial)."""
        self._expire_pending_commands()
        self._pending_commands[(vin, request_serial)] = _PendingCommand(command=command)
        self._emit_command_lifecycle(
            status=CommandLifecycleStatus.REGISTERED,
            vin=vin,
            request_serial=request_serial,
            command=command,
        )

    def get_command_ack_diagnostics(self) -> CommandAckDiagnostics:
        """Return a snapshot of command ACK correlation diagnostics."""
        self._expire_pending_commands()
        pending_by_vin: dict[str, int] = {}
        for vin, _serial in self._pending_commands:
            pending_by_vin[vin] = pending_by_vin.get(vin, 0) + 1

        return CommandAckDiagnostics.model_validate(
            {
                "pending": len(self._pending_commands),
                "matched": self._pending_matched_count,
                "expired": self._pending_expired_count,
                "uncorrelated": self._pending_uncorrelated_count,
                "pending_by_vin": pending_by_vin,
            }
        )

    async def _call_with_reauth(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run an API call, retrying once on session expiry."""
        try:
            return await fn()
        except BydSessionExpiredError:
            self.invalidate_session()
            await self.ensure_session()
            return await fn()

    async def _authed_call(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Run *fn(config, session, transport, \\*args, \\*\\*kwargs)* with auto re-auth.

        Most simple endpoints follow an identical pattern: acquire session
        and transport, call an API function, retry on session expiry.
        This helper eliminates the per-method inner closure boilerplate.
        """

        async def _call() -> T:
            session = await self.ensure_session()
            transport = self._require_transport()
            return await fn(self._config, session, transport, *args, **kwargs)

        return await self._call_with_reauth(_call)

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------

    async def _ensure_mqtt_started(self) -> None:
        """Best-effort MQTT startup (failures must not break REST flow)."""
        if not self._config.mqtt_enabled:
            return
        if self._mqtt_runtime is not None and self._mqtt_runtime.is_running:
            return
        session = self._session
        transport = self._transport
        loop = self._loop or asyncio.get_running_loop()
        if session is None or transport is None:
            return
        try:
            bootstrap = await fetch_mqtt_bootstrap(self._config, session, transport)
            runtime = BydMqttRuntime(
                loop=loop,
                decrypt_key_hex=session.content_key(),
                on_event=self._on_mqtt_event,
                on_decrypt_error=self._schedule_mqtt_reauth,
                keepalive=self._config.mqtt_keepalive,
                logger=_logger,
            )
            # runtime.start() performs blocking I/O (TLS handshake,
            # TCP connect) so run it in an executor to avoid blocking
            # the asyncio event loop.
            await loop.run_in_executor(None, runtime.start, bootstrap)
            self._mqtt_runtime = runtime
        except Exception:
            _logger.debug("MQTT startup failed", exc_info=True)

    def _stop_mqtt(self) -> None:
        runtime = self._mqtt_runtime
        self._mqtt_runtime = None
        if runtime is not None:
            runtime.stop()
        # Cancel any pending MQTT waiters so callers don't hang
        for w in self._mqtt_waiters:
            if not w.future.done():
                w.future.cancel()
        self._mqtt_waiters.clear()

    _MQTT_REAUTH_COOLDOWN_S: float = 60.0

    def _schedule_mqtt_reauth(self) -> None:
        """Schedule a background re-authentication after MQTT decrypt failure.

        Runs on the asyncio event loop (dispatched via ``call_soon_threadsafe``
        from the paho thread).  Rate-limited to at most once per
        ``_MQTT_REAUTH_COOLDOWN_S`` seconds to prevent loops.
        """
        now = time.monotonic()
        if now - self._mqtt_reauth_at < self._MQTT_REAUTH_COOLDOWN_S:
            return
        self._mqtt_reauth_at = now
        _logger.info("MQTT decrypt failed — scheduling re-authentication")
        asyncio.ensure_future(self._mqtt_reauth())  # noqa: RUF006

    async def _mqtt_reauth(self) -> None:
        """Background re-authentication to recover from MQTT key mismatch."""
        try:
            await self.login()
            _logger.info("MQTT re-auth succeeded — MQTT restarted with new key")
        except Exception:
            _logger.debug("MQTT re-auth recovery failed", exc_info=True)

    def _on_mqtt_event(self, event: MqttEvent) -> None:
        """Handle a decrypted MQTT event (called from the MQTT thread via call_soon_threadsafe)."""
        # BYD wraps payloads in data.respondData
        data = event.payload.get("data")
        respond_data_raw = data.get("respondData") if isinstance(data, dict) else event.payload

        if not isinstance(respond_data_raw, dict):
            # Error payloads (e.g. code 6051 "no GPS") carry no respondData.
            # Fall back to the data envelope so MQTT waiters still get resolved.
            if isinstance(data, dict) and "code" in data:
                respond_data_raw = data
            else:
                return

        # Normalise to a standalone dict (avoid mutating the original payload)
        respond_data: dict[str, Any] = dict(respond_data_raw)

        # Derive strict requestSerial used by _mqtt_wait from data.uuid.
        serial: str | None = None
        if isinstance(data, dict):
            uuid_value = data.get("uuid")
            if isinstance(uuid_value, str) and uuid_value:
                serial = uuid_value

        if serial:
            respond_data.setdefault("requestSerial", serial)

        # --- 1) Generic callback — fire for every MQTT event (debug / logging) ---
        if self._on_mqtt_event_cb is not None and event.vin:
            try:
                self._on_mqtt_event_cb(event.event, event.vin, respond_data)
            except Exception:
                _logger.debug("on_mqtt_event callback failed", exc_info=True)

        # --- 2) vehicleInfo callback for on_vehicle_info + BydCar routing ---
        if event.event == "vehicleInfo" and event.vin:
            realtime_parsed: VehicleRealtimeData | None = None
            try:
                realtime_parsed = VehicleRealtimeData.model_validate(respond_data)
            except Exception:
                _logger.debug("Failed to parse MQTT vehicleInfo", exc_info=True)

            if realtime_parsed is not None:
                # Fire user callback
                if self._on_vehicle_info is not None:
                    try:
                        self._on_vehicle_info(event.vin, realtime_parsed)
                    except Exception:
                        _logger.debug("on_vehicle_info callback failed", exc_info=True)

                # Route to BydCar instance (through guard window)
                car = self._cars.get(event.vin)
                if car is not None:
                    car.handle_mqtt_realtime(realtime_parsed)

        # --- 2b) smartCharge callback — route to BydCar ---
        if event.event == "smartCharge" and event.vin:
            charging_parsed: ChargingStatus | None = None
            try:
                charging_parsed = ChargingStatus.model_validate(respond_data)
            except Exception:
                _logger.debug("Failed to parse MQTT smartCharge", exc_info=True)

            if charging_parsed is not None:
                car = self._cars.get(event.vin)
                if car is not None:
                    car.handle_mqtt_charging(charging_parsed)

        # --- 2c) energyConsumption callback — route to BydCar ---
        if event.event == "energyConsumption" and event.vin:
            energy_parsed: EnergyConsumption | None = None
            try:
                energy_parsed = EnergyConsumption.model_validate(respond_data)
            except Exception:
                _logger.debug("Failed to parse MQTT energyConsumption", exc_info=True)

            if energy_parsed is not None:
                car = self._cars.get(event.vin)
                if car is not None:
                    car.handle_mqtt_energy(energy_parsed)

        # --- 3) Dispatch to generic MQTT waiters ---
        serial_value = respond_data.get("requestSerial")
        serial = serial_value if isinstance(serial_value, str) and serial_value else None

        matched: list[_MqttWaiter] = []
        remaining: list[_MqttWaiter] = []
        for w in self._mqtt_waiters:
            if w.future.done():
                remaining.append(w)
                continue

            if w.vin != event.vin:
                remaining.append(w)
                continue

            if w.event_type is not None and w.event_type != event.event:
                remaining.append(w)
                continue

            if w.serial is None or w.serial == serial:
                matched.append(w)
                continue

            remaining.append(w)
        self._mqtt_waiters = remaining
        for w in matched:
            if not w.future.done():
                w.future.set_result(respond_data)

        # --- 4) Command ack callback — only for genuine remote-control acks ---
        # Fire on_command_ack when this is a remoteControl event whose serial
        # does NOT belong to an in-flight data poll (GPS, realtime).  This
        # lets the integration distinguish actual command acks from data-poll
        # MQTT responses that happen to arrive as "remoteControl" events.
        if event.vin and event.event == "remoteControl" and (serial is None or serial not in self._data_poll_serials):
            try:
                raw_uuid: str | None = None
                if isinstance(data, dict):
                    uuid_candidate = data.get("uuid")
                    if isinstance(uuid_candidate, str) and uuid_candidate:
                        raw_uuid = uuid_candidate

                ack_timestamp: int | None = None
                for key in ("time", "timestamp"):
                    value = respond_data.get(key)
                    if value is not None:
                        try:
                            ack_timestamp = int(value)
                        except (TypeError, ValueError):
                            ack_timestamp = None
                        break

                ack_result: str | None = None
                for key in ("result", "message", "msg"):
                    value = respond_data.get(key)
                    if value is not None:
                        ack_result = value if isinstance(value, str) else str(value)
                        break

                ack_success = RemoteControlResult.model_validate(respond_data).success
                ack_event = CommandAckEvent.model_validate(
                    {
                        "vin": event.vin,
                        "requestSerial": serial,
                        "raw_uuid": raw_uuid,
                        "result": ack_result,
                        "success": ack_success,
                        "timestamp": ack_timestamp,
                        "raw": dict(event.payload),
                    }
                )

                self._expire_pending_commands()

                if serial is None:
                    self._pending_uncorrelated_count += 1
                    self._emit_command_lifecycle(
                        status=CommandLifecycleStatus.UNCORRELATED,
                        vin=event.vin,
                        request_serial=None,
                        command=None,
                        ack=ack_event,
                        reason="missing_request_serial",
                    )
                else:
                    pending = self._pending_commands.pop((event.vin, serial), None)
                    if pending is None:
                        self._pending_uncorrelated_count += 1
                        self._emit_command_lifecycle(
                            status=CommandLifecycleStatus.UNCORRELATED,
                            vin=event.vin,
                            request_serial=serial,
                            command=None,
                            ack=ack_event,
                            reason="pending_not_found",
                        )
                    else:
                        self._pending_matched_count += 1
                        self._emit_command_lifecycle(
                            status=CommandLifecycleStatus.MATCHED,
                            vin=event.vin,
                            request_serial=serial,
                            command=pending.command,
                            ack=ack_event,
                        )

                if self._on_command_ack_cb is not None:
                    loop = self._loop
                    cb = self._on_command_ack_cb

                    def _invoke_ack() -> None:
                        try:
                            cb(ack_event)
                        except Exception:
                            _logger.debug("on_command_ack callback failed", exc_info=True)

                    if loop is not None and loop.is_running():
                        loop.call_soon(_invoke_ack)
                    else:
                        _invoke_ack()
            except Exception:
                _logger.debug("on_command_ack callback failed", exc_info=True)

    async def _mqtt_wait(
        self,
        vin: str,
        *,
        event_type: str | None = None,
        serial: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Wait for an MQTT event matching the given criteria.

        Parameters
        ----------
        vin
            Vehicle to match.
        event_type
            MQTT event name to match (e.g. ``"vehicleInfo"``,
            ``"remoteControl"``).  ``None`` matches any event.
        serial
            ``requestSerial`` to match.  ``None`` matches any serial.
        timeout
            Seconds to wait.  Falls back to ``config.mqtt_timeout``.

        Returns
        -------
        dict or None
            The ``respondData`` dict from the MQTT payload, or ``None``
            on timeout / MQTT disabled.
        """
        runtime = self._mqtt_runtime
        effective_timeout = timeout if timeout is not None else self._config.mqtt_timeout
        if runtime is None or not runtime.is_running or effective_timeout <= 0:
            return None
        loop = self._loop or asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        waiter = _MqttWaiter(vin=vin, event_type=event_type, serial=serial, future=fut)
        self._mqtt_waiters.append(waiter)
        try:
            return await asyncio.wait_for(fut, effective_timeout)
        except TimeoutError:
            return None
        finally:
            with contextlib.suppress(ValueError):
                self._mqtt_waiters.remove(waiter)

    # ------------------------------------------------------------------
    # Read endpoints
    # ------------------------------------------------------------------

    async def _trigger_and_poll(
        self,
        *,
        vin: str,
        trigger_endpoint: str,
        poll_endpoint: str,
        fetch_fn: Callable[..., Awaitable[tuple[dict[str, Any], str | None]]],
        is_ready: Callable[[dict[str, Any]], bool],
        model_cls: type[_M],
        label: str,
        mqtt_event_type: str | None = None,
        mqtt_timeout: float | None = None,
        poll_attempts: int = 10,
        poll_interval: float = 1.5,
        signal_retries: int = 2,
    ) -> _M:
        """Generic trigger → MQTT wait → HTTP poll fallback.

        Parameters
        ----------
        fetch_fn
            Async callable with signature
            ``(endpoint, config, session, transport, vin, serial?) -> (dict, serial)``.
        is_ready
            Predicate that returns ``True`` when the raw dict has useful data.
        model_cls
            Pydantic model to ``model_validate`` the final dict.
        label
            Human-readable label for debug logging (e.g. ``"Realtime"``).
        signal_retries
            Maximum consecutive ``BydDataUnavailableError`` responses before
            giving up early (vehicle likely has no signal). Defaults to 2.
        """
        session = await self.ensure_session()
        transport = self._require_transport()

        # Phase 1: Trigger
        trigger_info, serial = await fetch_fn(
            trigger_endpoint,
            self._config,
            session,
            transport,
            vin,
        )
        merged_latest = trigger_info if isinstance(trigger_info, dict) else {}

        if isinstance(trigger_info, dict) and is_ready(trigger_info):
            return model_cls.model_validate(merged_latest)

        if not serial:
            return model_cls.model_validate(merged_latest)

        # Register the serial so _on_mqtt_event can distinguish data-poll
        # responses from genuine remote-control command acks.
        trigger_serial: str = serial
        self._data_poll_serials.add(trigger_serial)
        try:
            # Phase 2: MQTT wait (preferred)
            mqtt_raw = await self._mqtt_wait(
                vin,
                event_type=mqtt_event_type,
                serial=serial,
                timeout=mqtt_timeout,
            )
            if isinstance(mqtt_raw, dict) and is_ready(mqtt_raw):
                _logger.debug("%s data received via MQTT for vin=%s", label, vin)
                return model_cls.model_validate(mqtt_raw)

            # Check if MQTT delivered a definitive "data unavailable" error
            # (e.g. code 6051 = no GPS signal).  Skip HTTP polling entirely.
            if isinstance(mqtt_raw, dict):
                mqtt_code = mqtt_raw.get("code")
                if isinstance(mqtt_code, str) and mqtt_code in _MQTT_DATA_UNAVAILABLE_CODES:
                    _logger.debug(
                        "%s unavailable via MQTT (code=%s) for vin=%s; "
                        "skipping HTTP poll, falling back to last known data",
                        label,
                        mqtt_code,
                        vin,
                    )
                    return model_cls.model_validate(merged_latest)

            # Phase 3: HTTP poll fallback
            _logger.debug("MQTT timeout; falling back to HTTP polling for %s vin=%s", label, vin)
            consecutive_unavailable = 0
            for attempt in range(1, poll_attempts + 1):
                if poll_interval > 0:
                    await asyncio.sleep(poll_interval)
                try:
                    latest, serial = await fetch_fn(
                        poll_endpoint,
                        self._config,
                        session,
                        transport,
                        vin,
                        serial,
                    )
                    consecutive_unavailable = 0  # reset on success
                    if isinstance(latest, dict):
                        merged_latest = latest
                    if isinstance(latest, dict) and is_ready(latest):
                        _logger.debug("%s ready via HTTP vin=%s attempt=%d", label, vin, attempt)
                        break
                except BydSessionExpiredError:
                    raise
                except BydDataUnavailableError:
                    consecutive_unavailable += 1
                    _logger.debug(
                        "%s data unavailable (attempt=%d/%d) — vehicle may lack signal",
                        label,
                        attempt,
                        poll_attempts,
                    )
                    if consecutive_unavailable >= signal_retries:
                        _logger.debug(
                            "%s giving up after %d consecutive signal failures for vin=%s; "
                            "falling back to last known data",
                            label,
                            consecutive_unavailable,
                            vin,
                        )
                        break
                except Exception:
                    consecutive_unavailable = 0  # reset — different error type
                    _logger.debug("%s poll attempt=%d failed", label, attempt, exc_info=True)

            return model_cls.model_validate(merged_latest)
        finally:
            self._data_poll_serials.discard(trigger_serial)

    async def get_vehicles(self) -> list[Vehicle]:
        """Fetch all vehicles associated with the account."""
        return await self._authed_call(_vehicle_api.fetch_vehicle_list)

    async def get_car(
        self,
        vin: str,
        *,
        vehicle: Vehicle | None = None,
        on_state_changed: Callable[[str, Any], None] | None = None,
        projection_ttl: float = 30.0,
    ) -> BydCar:
        """Obtain a :class:`BydCar` aggregate for the given VIN.

        Returns a cached instance if one already exists.  Otherwise
        fetches vehicle metadata (unless *vehicle* is provided) and
        creates a new :class:`BydCar` with capability namespaces and
        an internal state engine.

        Parameters
        ----------
        vin
            Vehicle identification number.
        vehicle
            Optional pre-fetched :class:`Vehicle` model.  If ``None``,
            the vehicle list is fetched to find the matching VIN.
        on_state_changed
            Optional callback ``(vin, VehicleSnapshot) -> None`` fired
            on every accepted state mutation.
        projection_ttl
            Default TTL for command projections (seconds).

        Returns
        -------
        BydCar
            Per-vehicle aggregate with typed capability namespaces.

        Raises
        ------
        BydError
            If the VIN is not found in the account's vehicle list.
        """
        from pybyd.car import BydCar  # delayed import to avoid circular dependency

        existing = self._cars.get(vin)
        if existing is not None:
            return existing

        if vehicle is None:
            vehicles = await self.get_vehicles()
            vehicle = next((v for v in vehicles if v.vin == vin), None)
            if vehicle is None:
                raise BydError(f"Vehicle {vin} not found in account")

        capabilities = await self.get_vehicle_capabilities(vin)

        car = BydCar(
            self,
            vin,
            vehicle,
            capabilities=capabilities,
            on_state_changed=on_state_changed,
            projection_ttl=projection_ttl,
        )
        self._cars[vin] = car
        return car

    async def get_latest_configs(self, vins: list[str]) -> dict[str, VehicleLatestConfig]:
        """Fetch raw latest-config payload for one or more VINs."""
        return await self._authed_call(_latest_config_api.fetch_latest_config, vins)

    async def get_latest_config(self, vin: str) -> VehicleLatestConfig:
        """Fetch raw latest-config payload for a single VIN."""
        configs = await self.get_latest_configs([vin])
        config = configs.get(vin)
        if config is None:
            raise BydDataUnavailableError(
                f"Latest config unavailable for VIN {vin}",
                code="latest_config_missing",
                endpoint="/vehicle/vehicleswitch/getLatestConfig",
            )
        return config

    async def get_vehicle_capabilities(
        self,
        vin: str,
        *,
        force_refresh: bool = False,
    ) -> VehicleCapabilities:
        """Return normalized vehicle capability availability for a VIN."""
        if not force_refresh:
            cached = self._vehicle_capabilities.get(vin)
            if cached is not None:
                return cached

        try:
            latest = await self.get_latest_config(vin)
            caps = VehicleCapabilities.from_latest_config(vin, latest)
        except Exception:
            _logger.debug("Failed to build capabilities for vin=%s", vin, exc_info=True)
            caps = VehicleCapabilities.unknown(vin, reason="fetch_error")

        self._vehicle_capabilities[vin] = caps
        return caps

    async def get_vehicle_realtime(
        self,
        vin: str,
        *,
        poll_attempts: int = 10,
        poll_interval: float = 1.5,
        mqtt_timeout: float | None = None,
        signal_retries: int = 2,
    ) -> VehicleRealtimeData:
        """Trigger + wait for realtime vehicle data.

        Sends a trigger request, then waits up to *mqtt_timeout* seconds
        for an MQTT ``vehicleInfo`` push.  Falls back to HTTP polling of
        ``vehicleRealTimeResult`` only if MQTT doesn't deliver in time.
        """

        async def _call() -> VehicleRealtimeData:
            return await self._trigger_and_poll(
                vin=vin,
                trigger_endpoint="/vehicleInfo/vehicle/vehicleRealTimeRequest",
                poll_endpoint="/vehicleInfo/vehicle/vehicleRealTimeResult",
                fetch_fn=_realtime_api.fetch_realtime_endpoint,
                is_ready=VehicleRealtimeData.is_ready_raw,
                model_cls=VehicleRealtimeData,
                label="Realtime",
                mqtt_event_type="vehicleInfo",
                mqtt_timeout=mqtt_timeout,
                poll_attempts=poll_attempts,
                poll_interval=poll_interval,
                signal_retries=signal_retries,
            )

        return await self._call_with_reauth(_call)

    async def get_gps_info(
        self,
        vin: str,
        *,
        poll_attempts: int = 10,
        poll_interval: float = 1.5,
        mqtt_timeout: float | None = None,
        signal_retries: int = 2,
    ) -> GpsInfo:
        """Trigger + wait for GPS info.

        Sends a trigger request, then waits for an MQTT push carrying
        the ``requestSerial``.  Falls back to HTTP polling of
        ``getGpsInfoResult`` if MQTT doesn't deliver in time.
        """

        async def _call() -> GpsInfo:
            if self._config.is_china_region:
                session = await self.ensure_session()
                transport = self._require_transport()
                decoded, _serial = await _gps_api.fetch_gps_endpoint(
                    "/vehicleInfo/gps/locationRequestService",
                    self._config,
                    session,
                    transport,
                    vin,
                    None,
                )
                return GpsInfo.model_validate(decoded)

            return await self._trigger_and_poll(
                vin=vin,
                trigger_endpoint="/control/getGpsInfo",
                poll_endpoint="/control/getGpsInfoResult",
                fetch_fn=_gps_api.fetch_gps_endpoint,
                is_ready=_gps_api.is_gps_info_ready,
                model_cls=GpsInfo,
                label="GPS",
                mqtt_timeout=mqtt_timeout,
                poll_attempts=poll_attempts,
                poll_interval=poll_interval,
                signal_retries=signal_retries,
            )

        return await self._call_with_reauth(_call)

    async def get_hvac_status(self, vin: str) -> HvacStatus:
        """Fetch HVAC / climate status."""
        return await self._authed_call(_hvac_api.fetch_hvac_status, vin)

    async def get_charging_status(self, vin: str) -> ChargingStatus:
        """Fetch charging status."""
        return await self._authed_call(_charging_api.fetch_charging_status, vin)

    async def get_energy_consumption(self, vin: str) -> EnergyConsumption:
        """Fetch energy consumption data."""
        return await self._authed_call(_energy_api.fetch_energy_consumption, vin)

    async def get_push_state(self, vin: str) -> PushNotificationState:
        """Fetch push notification state."""
        return await self._authed_call(_push_api.fetch_push_state, vin)

    async def set_push_state(self, vin: str, *, enable: bool) -> CommandAck:
        """Enable or disable push notifications."""
        return await self._authed_call(_push_api.set_push_state, vin, enable=enable)

    # ------------------------------------------------------------------
    # Control commands
    # ------------------------------------------------------------------

    @property
    def commands_enabled(self) -> bool:
        """Whether remote commands are available.

        Returns ``True`` only after :meth:`verify_command_access` succeeds.
        A new :class:`BydClient` instance is required to retry after failure.
        """
        return self._commands_enabled

    async def verify_command_access(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> VerifyControlPasswordResponse:
        """Verify the control PIN and enable remote commands.

        Must be called **once** during setup before issuing any remote
        commands.  Makes a single HTTP call to ``verifyControlPassword``.

        On success, :attr:`commands_enabled` becomes ``True`` for the
        lifetime of this client (survives session re-authentication).

        On failure (wrong PIN or account locked), raises
        :class:`~pybyd.exceptions.BydControlPasswordError` and commands
        remain disabled.  Create a new :class:`BydClient` to retry.
        """
        resolved = self._resolve_command_pwd(command_pwd)
        if not resolved:
            raise BydControlPasswordError(
                "No control PIN configured. Set config.control_pin or pass command_pwd.",
                code="no_pin",
                endpoint="",
            )
        result = await self._authed_call(_control_api.verify_control_password, vin, resolved)
        self._commands_enabled = True
        _logger.info("Command access verified for VIN %s…%s", vin[:3], vin[-4:])
        return result

    async def _remote_control(
        self,
        vin: str,
        command: RemoteCommand,
        *,
        control_params: Mapping[str, Any] | ControlParams | None = None,
        command_pwd: str | None = None,
        poll_attempts: int = 10,
        poll_interval: float = 1.5,
    ) -> RemoteControlResult:
        """Internal: send a remote command and poll/wait for result."""
        capabilities = await self.get_vehicle_capabilities(vin)

        params_dict: dict[str, Any] | None = None
        if control_params is not None:
            if isinstance(control_params, ControlParams):
                params_dict = control_params.to_control_params_map()
            else:
                params_dict = dict(control_params)

        gate = evaluate_command_gate(command, capabilities, control_params=params_dict)
        if not gate.supported:
            raise BydEndpointNotSupportedError(
                (f"Remote command {command.value} blocked for VIN {vin}: gate={gate.gate_id} reason={gate.reason}"),
                code="command_gate_blocked",
                endpoint="/control/remoteControl",
            )

        async def _mqtt_result_waiter(serial: str | None) -> RemoteControlResult | None:
            """Adapter: generic _mqtt_wait → RemoteControlResult."""
            if serial is None:
                return None
            raw = await self._mqtt_wait(vin, event_type="remoteControl", serial=serial)
            if raw is None:
                return None
            raw.setdefault("requestSerial", serial)
            return RemoteControlResult.model_validate(raw)

        async def _call() -> RemoteControlResult:
            session = await self.ensure_session()
            transport = self._require_transport()
            return await _control_api.poll_remote_control(
                self._config,
                session,
                transport,
                vin,
                command,
                control_params=params_dict,
                command_pwd=command_pwd,
                poll_attempts=poll_attempts,
                poll_interval=poll_interval,
                mqtt_result_waiter=_mqtt_result_waiter,
                on_trigger_dispatched=lambda serial: (
                    self._register_pending_command(vin, serial, command.value) if serial else None
                ),
            )

        return await self._call_with_reauth(_call)

    async def lock(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Lock the vehicle."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.LOCK, command_pwd=pwd)

    async def unlock(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Unlock the vehicle."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.UNLOCK, command_pwd=pwd)

    async def start_climate(
        self,
        vin: str,
        *,
        params: ClimateStartParams,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Start climate control with the given parameters."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(
            vin,
            RemoteCommand.START_CLIMATE,
            control_params=params,
            command_pwd=pwd,
        )

    async def stop_climate(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Stop climate control."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.STOP_CLIMATE, command_pwd=pwd)

    async def flash_lights(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Flash vehicle lights."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.FLASH_LIGHTS, command_pwd=pwd)

    async def close_windows(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Close all windows."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.CLOSE_WINDOWS, command_pwd=pwd)

    async def find_car(
        self,
        vin: str,
        *,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Activate find-my-car (horn + lights)."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(vin, RemoteCommand.FIND_CAR, command_pwd=pwd)

    async def schedule_climate(
        self,
        vin: str,
        *,
        params: ClimateScheduleParams,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Schedule climate control."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(
            vin,
            RemoteCommand.SCHEDULE_CLIMATE,
            control_params=params,
            command_pwd=pwd,
        )

    async def set_seat_climate(
        self,
        vin: str,
        *,
        params: SeatClimateParams,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Set seat heating/ventilation."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(
            vin,
            RemoteCommand.SEAT_CLIMATE,
            control_params=params,
            command_pwd=pwd,
        )

    async def set_battery_heat(
        self,
        vin: str,
        *,
        params: BatteryHeatParams,
        command_pwd: str | None = None,
    ) -> RemoteControlResult:
        """Enable or disable battery heating."""
        pwd = self._require_command_pwd(command_pwd)
        return await self._remote_control(
            vin,
            RemoteCommand.BATTERY_HEAT,
            control_params=params,
            command_pwd=pwd,
        )

    async def save_charging_schedule(
        self,
        vin: str,
        schedule: SmartChargingSchedule,
    ) -> CommandAck:
        """Save a smart charging schedule."""
        if (
            schedule.target_soc is None
            or schedule.start_hour is None
            or schedule.start_minute is None
            or schedule.end_hour is None
            or schedule.end_minute is None
        ):
            raise ValueError("SmartChargingSchedule must have all time fields set")
        target_soc = schedule.target_soc
        start_hour = schedule.start_hour
        start_minute = schedule.start_minute
        end_hour = schedule.end_hour
        end_minute = schedule.end_minute

        async def _call() -> CommandAck:
            session = await self.ensure_session()
            transport = self._require_transport()
            return await _smart_api.save_charging_schedule(
                self._config,
                session,
                transport,
                vin,
                target_soc=target_soc,
                start_hour=start_hour,
                start_minute=start_minute,
                end_hour=end_hour,
                end_minute=end_minute,
            )

        return await self._call_with_reauth(_call)

    async def toggle_smart_charging(self, vin: str, *, enable: bool) -> CommandAck:
        """Enable or disable smart charging."""
        return await self._authed_call(_smart_api.toggle_smart_charging, vin, enable=enable)

    async def rename_vehicle(self, vin: str, *, name: str) -> CommandAck:
        """Rename a vehicle."""
        return await self._authed_call(_settings_api.rename_vehicle, vin, name=name)
