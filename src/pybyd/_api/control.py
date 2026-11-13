"""Remote control endpoints.

Endpoints:
  - /control/remoteControl (trigger)
  - /control/remoteControlResult (poll)

The inner payload requires ``commandPwd`` (MD5 of the 6-digit control
PIN set in the BYD app) and must **not** include ``instructionCode``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pybyd._api._common import (
    ENDPOINT_NOT_SUPPORTED_CODES,
    build_inner_base,
    post_token_json,
    post_vehicle_json,
)
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import (
    BydApiError,
    BydControlPasswordError,
    BydRateLimitError,
    BydRemoteControlError,
    BydSessionExpiredError,
)
from pybyd.models.control import RemoteCommand, RemoteControlResult, VerifyControlPasswordResponse
from pybyd.session import Session

_logger = logging.getLogger(__name__)

_CONTROL_PASSWORD_ERROR_CODES: frozenset[str] = frozenset({"5005", "5006", "5011"})
_REMOTE_CONTROL_SERVICE_ERROR_CODES: frozenset[str] = frozenset({"1009"})
_REMOTE_CONTROL_GENERIC_ERROR_CODES: frozenset[str] = frozenset({"1001"})
_REMOTE_CONTROL_ENDPOINTS: frozenset[str] = frozenset({"/control/remoteControl", "/control/remoteControlResult"})
_VERIFY_CONTROL_PASSWORD_ENDPOINT = "/vehicle/vehicleswitch/verifyControlPassword"

# Extra error-code mappings for control endpoints.
_CONTROL_EXTRA_CODES: dict[frozenset[str], type[BydApiError]] = {
    _CONTROL_PASSWORD_ERROR_CODES: BydControlPasswordError,
}
_REMOTE_CONTROL_EXTRA_CODES: dict[frozenset[str], type[BydApiError]] = {
    _CONTROL_PASSWORD_ERROR_CODES: BydControlPasswordError,
    _REMOTE_CONTROL_SERVICE_ERROR_CODES: BydRemoteControlError,
    _REMOTE_CONTROL_GENERIC_ERROR_CODES: BydRemoteControlError,
}


def _build_control_inner(
    config: BydConfig,
    vin: str,
    command: RemoteCommand,
    *,
    control_params: dict[str, Any] | None = None,
    command_pwd: str | None = None,
    request_serial: str | None = None,
) -> dict[str, Any]:
    """Build the inner payload for remote control endpoints.

    Parameters
    ----------
    control_params
        Optional command parameters. Serialised to a JSON string as
        ``controlParamsMap`` in the payload.
    command_pwd
        Optional control password (PIN) sent as ``commandPwd``.
    """
    inner: dict[str, Any] = build_inner_base(config, vin=vin, request_serial=request_serial)
    inner["commandPwd"] = command_pwd or ""
    inner["commandType"] = command.value
    if control_params is not None:
        inner["controlParamsMap"] = json.dumps(
            control_params,
            separators=(",", ":"),
            sort_keys=True,
        )
    return inner


def _build_verify_control_password_inner(
    config: BydConfig,
    vin: str,
    command_pwd: str,
) -> dict[str, Any]:
    """Build inner payload for control password verification endpoint."""
    inner: dict[str, Any] = build_inner_base(config, vin=vin)
    inner["commandPwd"] = command_pwd
    inner["functionType"] = "remoteControl"
    return inner


async def verify_control_password(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    command_pwd: str,
) -> VerifyControlPasswordResponse:
    """Verify remote control password for a vehicle.

    Calls ``/vehicle/vehicleswitch/verifyControlPassword`` and returns the
    decrypted inner response payload.
    """
    inner = _build_verify_control_password_inner(config, vin, command_pwd)
    data = await post_token_json(
        endpoint=_VERIFY_CONTROL_PASSWORD_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
        extra_code_map=_CONTROL_EXTRA_CODES,
    )

    raw = data if isinstance(data, dict) else {}
    return VerifyControlPasswordResponse.model_validate({"vin": vin, **raw, "raw": raw})


def _is_remote_control_ready(data: dict[str, Any]) -> bool:
    """Check if remote control result has a terminal state.

    Returns ``True`` when ``controlState`` is defined and not 0
    (pending), when a terminal ``res`` value (≥ 2) is present,
    or when a ``result`` field is present.

    ``res=1`` means "command received / in progress" and is **not**
    terminal — the caller should keep polling.
    """
    if not data:
        return False
    control_state = data.get("controlState")
    if control_state is not None and int(control_state) != 0:
        return True
    res = data.get("res")
    if res is not None:
        return int(res) >= 2
    return "result" in data


def parse_remote_control_result_data(data: dict[str, Any]) -> RemoteControlResult:
    """Parse raw remote-control result payload into a typed model."""
    return RemoteControlResult.model_validate(data)


async def _fetch_control_endpoint(
    endpoint: str,
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    command: RemoteCommand,
    *,
    control_params: dict[str, Any] | None = None,
    command_pwd: str | None = None,
    request_serial: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Fetch a single control endpoint, returning (result_dict, next_serial)."""
    inner = _build_control_inner(
        config,
        vin,
        command,
        control_params=control_params,
        command_pwd=command_pwd,
        request_serial=request_serial,
    )

    # Build extra code map: for remote control endpoints, include service errors.
    # For remote control endpoints, do NOT pass not_supported_codes — code 1001
    # means something different (generic rejection) for commands vs data endpoints.
    is_remote = endpoint in _REMOTE_CONTROL_ENDPOINTS
    extra = _REMOTE_CONTROL_EXTRA_CODES if is_remote else _CONTROL_EXTRA_CODES
    # A guest account must declare itself on commands too. The helper's one
    # retry only follows the refusal that means "wrong userType", which the
    # cloud gives before the car hears anything.
    post = post_vehicle_json if is_remote else post_token_json
    result = await post(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=None if is_remote else ENDPOINT_NOT_SUPPORTED_CODES,
        extra_code_map=extra,
    )

    next_serial = (result.get("requestSerial") if isinstance(result, dict) else None) or request_serial
    if isinstance(result, dict) and next_serial:
        result.setdefault("requestSerial", next_serial)

    return result, next_serial


async def poll_remote_control(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    command: RemoteCommand,
    *,
    control_params: dict[str, Any] | None = None,
    command_pwd: str | None = None,
    poll_attempts: int = 10,
    poll_interval: float = 1.5,
    rate_limit_retries: int = 3,
    rate_limit_delay: float = 5.0,
    command_retries: int = 3,
    command_retry_delay: float = 3.0,
    mqtt_result_waiter: Callable[[str | None], Awaitable[RemoteControlResult | None]] | None = None,
    on_trigger_dispatched: Callable[[str | None], None] | None = None,
) -> RemoteControlResult:
    """Send a remote control command and poll until completion.

    Parameters
    ----------
    config : BydConfig
        Client configuration.
    session : Session
        Authenticated session.
    transport : Transport
        HTTP transport.
    vin : str
        Vehicle Identification Number.
    command : RemoteCommand
        The remote command to send.
    control_params : dict or None
        Command-specific parameters (serialised as ``controlParamsMap``).
    command_pwd : str or None
        Optional control password (PIN).
    poll_attempts : int
        Maximum number of result poll attempts.
    poll_interval : float
        Seconds between poll attempts.
    rate_limit_retries : int
        How many times to retry the initial trigger when the server
        returns code 6024 ("previous command still in progress").
    rate_limit_delay : float
        Seconds to wait between rate-limit retries.
    command_retries : int
        How many times to retry the entire command when it fails
        (controlState=2).  Set to 1 for no retries.
    command_retry_delay : float
        Seconds to wait between command retries.

    Returns
    -------
    RemoteControlResult
        The command result.

    Raises
    ------
    BydRemoteControlError
        If the command fails (controlState=2) after all retries.
    BydRateLimitError
        If the server keeps returning code 6024 after all retries.
    BydApiError
        If the API returns an error.
    """
    last_exc: BydRemoteControlError | None = None

    for cmd_attempt in range(1, command_retries + 1):
        try:
            return await _poll_remote_control_once(
                config,
                session,
                transport,
                vin,
                command,
                control_params=control_params,
                command_pwd=command_pwd,
                poll_attempts=poll_attempts,
                poll_interval=poll_interval,
                rate_limit_retries=rate_limit_retries,
                rate_limit_delay=rate_limit_delay,
                mqtt_result_waiter=mqtt_result_waiter,
                on_trigger_dispatched=on_trigger_dispatched,
            )
        except BydRemoteControlError as exc:
            last_exc = exc
            if cmd_attempt < command_retries:
                _logger.info(
                    "Remote control %s failed (attempt %d/%d), retrying in %.1fs",
                    command.name,
                    cmd_attempt,
                    command_retries,
                    command_retry_delay,
                )
                await asyncio.sleep(command_retry_delay)

    # All retries exhausted – re-raise the last failure
    assert last_exc is not None  # noqa: S101
    raise last_exc


async def _poll_remote_control_once(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    command: RemoteCommand,
    *,
    control_params: dict[str, Any] | None = None,
    command_pwd: str | None = None,
    poll_attempts: int = 10,
    poll_interval: float = 1.5,
    rate_limit_retries: int = 3,
    rate_limit_delay: float = 5.0,
    mqtt_result_waiter: Callable[[str | None], Awaitable[RemoteControlResult | None]] | None = None,
    on_trigger_dispatched: Callable[[str | None], None] | None = None,
) -> RemoteControlResult:
    """Single attempt: trigger + poll.  Raises on failure."""
    # Phase 1: Trigger request (with control params) — retry on 6024
    for rate_attempt in range(1, rate_limit_retries + 1):
        try:
            result, serial = await _fetch_control_endpoint(
                "/control/remoteControl",
                config,
                session,
                transport,
                vin,
                command,
                control_params=control_params,
                command_pwd=command_pwd,
            )
            if on_trigger_dispatched is not None:
                try:
                    on_trigger_dispatched(serial)
                except Exception:
                    _logger.debug("on_trigger_dispatched callback failed", exc_info=True)
            break
        except BydApiError as exc:
            if exc.code == "6024":
                _logger.info(
                    "Remote control %s rate-limited (6024), retry %d/%d in %.1fs",
                    command.name,
                    rate_attempt,
                    rate_limit_retries,
                    rate_limit_delay,
                )
                await asyncio.sleep(rate_limit_delay)
            else:
                raise
    else:
        # All rate-limit retries exhausted
        raise BydRateLimitError(
            f"Remote control {command.name} rate-limited after {rate_limit_retries} retries (code 6024)",
            code="6024",
            endpoint="/control/remoteControl",
        )

    _logger.debug(
        "Remote control %s: controlState=%s serial=%s",
        command.name,
        result.get("controlState") if isinstance(result, dict) else None,
        serial,
    )

    if isinstance(result, dict) and _is_remote_control_ready(result):
        parsed = parse_remote_control_result_data(result)
        if parsed.control_state == 2:
            msg = result.get("message") or result.get("msg") or "controlState=2"
            raise BydRemoteControlError(
                f"Remote control {command.name} failed: {msg}",
                code="2",
                endpoint="/control/remoteControl",
            )
        return parsed

    if not serial:
        # No serial and the immediate result isn't terminal (checked above):
        # we can never correlate an ack, so we cannot confirm execution.
        # Surface this instead of fabricating a tentative-success result.
        _logger.debug(
            "Remote control %s returned no serial and no terminal result; " "cannot confirm execution",
            command.name,
        )
        raise BydRemoteControlError(
            f"Remote control {command.name} could not be confirmed " f"(vehicle returned no request serial)",
            code="no_serial",
            endpoint="/control/remoteControl",
        )

    if mqtt_result_waiter is not None:
        try:
            mqtt_result = await mqtt_result_waiter(serial)
            if mqtt_result is not None:
                mqtt_terminal = _is_remote_control_ready(mqtt_result.model_dump(by_alias=True))
                if not mqtt_terminal:
                    _logger.debug(
                        "Remote control %s MQTT result still pending; falling back to polling",
                        command.name,
                    )
                else:
                    _logger.debug(
                        "Remote control %s resolved via MQTT success=%s state=%s",
                        command.name,
                        mqtt_result.success,
                        mqtt_result.control_state,
                    )
                    if mqtt_result.control_state == 2:
                        raise BydRemoteControlError(
                            f"Remote control {command.name} rejected by vehicle " f"(controlState=2)",
                            code="2",
                            endpoint="/control/remoteControl",
                        )
                    return mqtt_result
            _logger.debug("Remote control %s mqtt_wait returned no result; falling back to polling", command.name)
        except Exception:
            _logger.debug("Remote control %s mqtt_wait failed; falling back to polling", command.name, exc_info=True)

    # Phase 2: Poll for results
    latest = result
    reached_terminal = False
    for attempt in range(1, poll_attempts + 1):
        if poll_interval > 0:
            await asyncio.sleep(poll_interval)

        try:
            latest, serial = await _fetch_control_endpoint(
                "/control/remoteControlResult",
                config,
                session,
                transport,
                vin,
                command,
                request_serial=serial,
            )
            _logger.debug(
                "Remote control %s poll attempt=%d controlState=%s serial=%s",
                command.name,
                attempt,
                latest.get("controlState") if isinstance(latest, dict) else None,
                serial,
            )
            if isinstance(latest, dict) and _is_remote_control_ready(latest):
                reached_terminal = True
                break
        except BydSessionExpiredError:
            raise
        except BydApiError:
            _logger.debug(
                "Remote control %s poll attempt=%d failed",
                command.name,
                attempt,
                exc_info=True,
            )

    parsed = parse_remote_control_result_data(latest if isinstance(latest, dict) else {})
    _logger.debug(
        "Remote control %s final parsed result success=%s state=%s",
        command.name,
        parsed.success,
        parsed.control_state,
    )
    if parsed.control_state == 2:
        msg = (
            (latest.get("message") or latest.get("msg") or "controlState=2")
            if isinstance(latest, dict)
            else "controlState=2"
        )
        raise BydRemoteControlError(
            f"Remote control {command.name} failed: {msg}",
            code="2",
            endpoint="/control/remoteControlResult",
        )
    if not reached_terminal:
        # Loop exhausted without the cloud ever returning a terminal result —
        # typically a deep-asleep/offline car. Surface it as a timeout instead
        # of returning a quiet pending result that callers read as success.
        raise BydRemoteControlError(
            f"Remote control {command.name} not confirmed within timeout " f"(vehicle may be asleep or offline)",
            code="timeout",
            endpoint="/control/remoteControlResult",
        )
    return parsed
