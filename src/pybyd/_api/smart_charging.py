"""Smart charging control endpoints.

Endpoints:
  - /control/smartCharge/homePage            (read schedule + live SoC)
  - /control/smartCharge/changeChargeStatue  (toggle on/off, or start/stop charge)
  - /control/smartCharge/changeResult        (poll until status change settles)
  - /control/smartCharge/saveOrUpdate        (save schedule)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pybyd._api._common import (
    ENDPOINT_NOT_SUPPORTED_CODES,
    build_inner_base,
    post_token_json,
)
from pybyd._api.charging import fetch_charging_homepage
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import BydDataUnavailableError
from pybyd.models.control import CommandAck
from pybyd.models.smart_charging import SmartChargingSchedule
from pybyd.session import Session

_TOGGLE_ENDPOINT = "/control/smartCharge/changeChargeStatue"
_RESULT_ENDPOINT = "/control/smartCharge/changeResult"
_SAVE_ENDPOINT = "/control/smartCharge/saveOrUpdate"
_STOP_ENDPOINT = "/control/smartCharge/stopChargeControl"
_CONTINUE_ENDPOINT = "/control/smartCharge/continueChargeControl"
_SOC_LIMIT_ENDPOINT = "/control/smartCharge/chargeSocLimit"
_GET_AC_LIMIT_ENDPOINT = "/control/smartCharge/getAcCurrentLimit"
_SET_AC_LIMIT_ENDPOINT = "/control/smartCharge/limitCurrentControl"

#: Cloud error codes meaning the cloud accepted the request but couldn't
#: reach the vehicle right now (weak cellular signal, vehicle parked
#: somewhere with no coverage, etc.).  Recoverable — the user should
#: retry once the vehicle is back online.
VEHICLE_UNREACHABLE_CODES: frozenset[str] = frozenset({"6002"})

_logger = logging.getLogger(__name__)


async def fetch_charging_schedule(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> SmartChargingSchedule:
    """Fetch the configured smart-charging schedule.

    Hits the same ``/control/smartCharge/homePage`` endpoint as
    :func:`pybyd._api.charging.fetch_charging_status` but parses the
    nested ``smartChargeDto`` / ``smartJourneyDto`` blocks (the schedule
    config) rather than the top-level live charging fields.

    Two parsers split off the same response so the live state (which
    MQTT also pushes as :class:`ChargingStatus`) and the schedule config
    (HTTP-only, on-demand) can be cached on different snapshot sections
    without one clobbering the other.
    """
    raw = await fetch_charging_homepage(config, session, transport, vin)
    return SmartChargingSchedule.model_validate({"vin": vin, **raw})


async def toggle_smart_charging(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    enable: bool,
) -> CommandAck:
    """Toggle smart charging on or off.

    Parameters
    ----------
    enable : bool
        True to enable smart charging, False to disable.

    Returns
    -------
    CommandAck
        Decoded API acknowledgement.
    """
    inner = build_inner_base(config, vin=vin)
    inner["smartChargeSwitch"] = str(1 if enable else 0)
    decoded = await post_token_json(
        endpoint=_TOGGLE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return CommandAck.model_validate({"vin": vin, **raw, "raw": raw})


async def trigger_charge_change(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    start: bool,
) -> tuple[dict[str, Any], str | None]:
    """POST ``/control/smartCharge/changeChargeStatue`` to start/stop charging.

    The toggle inner-payload always carries an empty ``timeZone`` (per the
    captured BYD-app traffic — see ``references/changeChargeStatue.md``).
    No other endpoint we implement uses this field, so it lives here
    rather than in the shared ``build_inner_base``.

    Returns ``(raw_response, request_serial)``.  The ``requestSerial`` is
    needed to correlate the follow-up MQTT push or ``changeResult`` poll.
    """
    inner = build_inner_base(config, vin=vin)
    inner["timeZone"] = ""
    inner["status"] = "1" if start else "0"
    decoded = await post_token_json(
        endpoint=_TOGGLE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return raw, raw.get("requestSerial")


async def poll_charge_result(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    request_serial: str,
) -> tuple[dict[str, Any], str | None]:
    """POST ``/control/smartCharge/changeResult`` to check toggle progress.

    Returns ``(raw_response, request_serial)``.  Caller (typically
    :meth:`BydClient._trigger_and_poll`) inspects ``raw["res"]``: ``1`` =
    still pending, ``2`` = success terminal, anything else = failure
    terminal.
    """
    inner = build_inner_base(config, vin=vin, request_serial=request_serial)
    decoded = await post_token_json(
        endpoint=_RESULT_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    _logger.debug(
        "smartCharge changeResult vin=%s res=%s message=%s",
        vin,
        raw.get("res"),
        raw.get("message"),
    )
    return raw, raw.get("requestSerial") or request_serial


def make_change_charge_fetch_fn(*, start: bool) -> Callable[..., Awaitable[tuple[dict[str, Any], str | None]]]:
    """Adapter: select :func:`trigger_charge_change` or :func:`poll_charge_result`
    based on the endpoint URL :meth:`BydClient._trigger_and_poll` calls us with.

    The two operations have different inner-payload shapes (trigger needs
    ``status``/``timeZone``; poll needs ``requestSerial``), so a single
    generic ``fetch_fn`` like :func:`pybyd._api.realtime.fetch_realtime_endpoint`
    doesn't apply — we dispatch to the right helper at the seam.
    """

    async def _fetch(
        endpoint: str,
        config: BydConfig,
        session: Session,
        transport: Transport,
        vin: str,
        request_serial: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if endpoint == _RESULT_ENDPOINT:
            return await poll_charge_result(config, session, transport, vin, request_serial or "")
        return await trigger_charge_change(config, session, transport, vin, start=start)

    return _fetch


def make_charge_control_fetch_fn(
    trigger: Callable[..., Awaitable[tuple[dict[str, Any], str | None]]],
) -> Callable[..., Awaitable[tuple[dict[str, Any], str | None]]]:
    """Adapter for a charge-control trigger polled through ``changeResult``."""

    async def _fetch(
        endpoint: str,
        config: BydConfig,
        session: Session,
        transport: Transport,
        vin: str,
        request_serial: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if endpoint == _RESULT_ENDPOINT:
            return await poll_charge_result(config, session, transport, vin, request_serial or "")
        return await trigger(config, session, transport, vin)

    return _fetch


def make_save_charging_schedule_fetch_fn(
    *,
    start_charge_time: str,
    end_charge_time: str,
    charge_way: str,
    enabled: bool,
) -> Callable[..., Awaitable[tuple[dict[str, Any], str | None]]]:
    """Adapter for :meth:`BydClient._trigger_and_poll` for ``saveOrUpdate``.

    Dispatches the trigger leg to :func:`trigger_save_charging_schedule`
    and the poll leg to :func:`poll_charge_result` — the same pattern
    used for ``changeChargeStatue`` start/stop, since ``saveOrUpdate``
    follow-ups also resolve via ``changeResult``.
    """

    async def _fetch(
        endpoint: str,
        config: BydConfig,
        session: Session,
        transport: Transport,
        vin: str,
        request_serial: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if endpoint == _RESULT_ENDPOINT:
            return await poll_charge_result(config, session, transport, vin, request_serial or "")
        return await trigger_save_charging_schedule(
            config,
            session,
            transport,
            vin,
            start_charge_time=start_charge_time,
            end_charge_time=end_charge_time,
            charge_way=charge_way,
            enabled=enabled,
        )

    return _fetch


def is_charge_change_ready(payload: dict[str, Any]) -> bool:
    """Predicate for :meth:`BydClient._trigger_and_poll`'s ``is_ready``.

    The trigger response carries only ``requestSerial`` (not ready).
    The result/MQTT-push response carries ``res``: ``1`` = pending (keep
    polling), ``2`` = success (terminal, ready), other values = failure
    (also terminal — surface to the caller, who decides whether to raise).
    """
    res = payload.get("res")
    return isinstance(res, int) and res != 1


async def trigger_save_charging_schedule(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    start_charge_time: str,
    end_charge_time: str,
    charge_way: str,
    enabled: bool = True,
) -> tuple[dict[str, Any], str | None]:
    """POST ``/control/smartCharge/saveOrUpdate`` to push a new schedule.

    Wire format mirrors the BYD-app capture under
    ``captures/logs_decrypted/timetable_set_*/01_control_smartCharge_saveOrUpdate.json``:
    ``startChargeTime`` / ``endChargeTime`` are ``"HH:MM"`` strings (or
    the sentinel ``"full"`` on ``endChargeTime``), ``chargeWay`` is the
    repeat selector, and ``status`` is the enabled flag.

    Returns ``(raw_response, request_serial)``.  The ``requestSerial``
    is then used to poll ``changeResult`` until the cloud confirms the
    schedule was applied (``res == 2``) or returns a terminal failure
    — the same lifecycle as ``changeChargeStatue`` start/stop.

    Parameters
    ----------
    start_charge_time : str
        Schedule start as ``"HH:MM"``.
    end_charge_time : str
        Schedule end as ``"HH:MM"`` or the literal ``"full"`` sentinel
        (charge until full within the window).
    charge_way : str
        Repeat selector: ``"s"`` (single one-shot), ``"e"`` (every day),
        or comma-separated weekday indices like ``"0,1,2,3,4"``
        (``0`` = Monday).
    enabled : bool
        Whether the schedule is active.  Defaults to ``True``.
    """
    inner = build_inner_base(config, vin=vin)
    inner.update(
        {
            "startChargeTime": start_charge_time,
            "endChargeTime": end_charge_time,
            "chargeWay": charge_way,
            "status": "1" if enabled else "0",
            "timeZone": "",
        }
    )
    decoded = await post_token_json(
        endpoint=_SAVE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
        extra_code_map={VEHICLE_UNREACHABLE_CODES: BydDataUnavailableError},
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return raw, raw.get("requestSerial")


async def trigger_stop_charge(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> tuple[dict[str, Any], str | None]:
    """POST ``/control/smartCharge/stopChargeControl`` to stop an active charge.

    Returns ``(raw_response, request_serial)``.
    """
    inner = build_inner_base(config, vin=vin)
    decoded = await post_token_json(
        endpoint=_STOP_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return raw, raw.get("requestSerial")


async def trigger_continue_charge(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> tuple[dict[str, Any], str | None]:
    """POST ``/control/smartCharge/continueChargeControl`` to resume charging.

    Returns ``(raw_response, request_serial)``.
    """
    inner = build_inner_base(config, vin=vin)
    decoded = await post_token_json(
        endpoint=_CONTINUE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return raw, raw.get("requestSerial")


async def set_charge_soc_limit(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    soc_limit: int,
) -> CommandAck:
    """POST ``/control/smartCharge/chargeSocLimit`` to set target SoC limit."""
    inner: dict[str, Any] = build_inner_base(config, vin=vin)
    inner["chargeSocLimit"] = soc_limit
    decoded = await post_token_json(
        endpoint=_SOC_LIMIT_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return CommandAck.model_validate({"vin": vin, **raw, "raw": raw})


async def fetch_ac_current_limit(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    energy_type: str = "0",
) -> dict[str, Any]:
    """POST ``/control/smartCharge/getAcCurrentLimit`` to query AC current limits."""
    inner = build_inner_base(config, vin=vin)
    inner["energyType"] = energy_type
    decoded = await post_token_json(
        endpoint=_GET_AC_LIMIT_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    return decoded if isinstance(decoded, dict) else {}


async def trigger_set_ac_current_limit(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    limit_value: str,
) -> CommandAck:
    """POST ``/control/smartCharge/limitCurrentControl`` to set AC current limit."""
    inner = build_inner_base(config, vin=vin)
    inner["limitCurrentValue"] = str(limit_value)
    decoded = await post_token_json(
        endpoint=_SET_AC_LIMIT_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return CommandAck.model_validate({"vin": vin, **raw, "raw": raw})
