"""Preconditioning plan endpoints (app 3.6.0 climate scheduling, #91).

Endpoints:
  - /control/standby-vehicles/list         (read plans)
  - /control/standby-vehicles/save         (create, or edit commands and schedule)
  - /control/standby-vehicles/setDefault   (make a plan the default)
  - /control/standby-vehicles/delete       (delete a user plan)
  - /control/standby-vehicles/send         (run a plan now)
  - /control/standby-vehicles/send-result  (poll the run)

``list`` answers ``1001`` unless the inner payload carries ``userId``.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import BydStandbyScheduleError
from pybyd.models.standby import (
    COMMAND_CLIMATE,
    EXECUTE_DAILY,
    EXECUTE_ONCE,
    EXECUTE_WEEKLY,
    PLAN_TYPE_USER,
    StandbyPlan,
    StandbyPlanList,
    StandbySchedule,
)
from pybyd.session import Session

LIST_ENDPOINT = "/control/standby-vehicles/list"
SAVE_ENDPOINT = "/control/standby-vehicles/save"
SET_DEFAULT_ENDPOINT = "/control/standby-vehicles/setDefault"
DELETE_ENDPOINT = "/control/standby-vehicles/delete"
SEND_ENDPOINT = "/control/standby-vehicles/send"
SEND_RESULT_ENDPOINT = "/control/standby-vehicles/send-result"

SCHEDULE_REFUSED_CODES: frozenset[str] = frozenset({"6111"})
"""Once-plan time under 20 min ahead or over 30 days out (#91)."""


async def fetch_standby_plans(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> StandbyPlanList:
    """Fetch the vehicle's preconditioning plans."""
    inner = build_inner_base(config, vin=vin)
    inner["timeZone"] = config.time_zone
    inner["userId"] = session.user_id
    decoded = await post_token_json(
        endpoint=LIST_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    # the decrypted payload is either the list itself or {"data": [...]}
    plans = decoded if isinstance(decoded, list) else (decoded or {}).get("data") or []
    return StandbyPlanList.model_validate({"vin": vin, "data": plans})


def _plan_id(plan_id: str) -> int | str:
    # the app sends the 64-bit id as a JSON number everywhere but send
    return int(plan_id) if plan_id.isdigit() else plan_id


def _epoch_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def _next_daily(at: dt.datetime, now: dt.datetime) -> dt.datetime:
    # the app sends the next occurrence of the time, keep a stale one moving
    while at <= now:
        at += dt.timedelta(days=1)
    return at


def schedule_fields(schedule: StandbySchedule, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """``save`` fields for a schedule, as app 3.6.0 sends them."""
    if schedule.repeat == "once":
        assert schedule.at is not None
        return {"executeType": EXECUTE_ONCE, "executeTime": _epoch_ms(schedule.at)}
    if schedule.repeat == "daily":
        assert schedule.at is not None
        at = _next_daily(schedule.at, now or dt.datetime.now(dt.UTC))
        return {"executeType": EXECUTE_DAILY, "executeTime": _epoch_ms(at)}
    if schedule.repeat == "weekly":
        return {
            "executeType": EXECUTE_WEEKLY,
            "executeTime": 0,
            "startTime": schedule.start_time,
            "weeklyDays": ",".join(str(day) for day in schedule.weekly_days),
        }
    # the app's "Scheduled time" toggle off
    return {"executeType": EXECUTE_WEEKLY, "executeTime": 0, "startTime": "", "weeklyDays": ""}


def build_save_inner(
    config: BydConfig,
    vin: str,
    plan: StandbyPlan,
    schedule: StandbySchedule,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build the ``save`` payload the way app 3.6.0 sends it.

    A plan without an ``id`` is created; the response doesn't carry the
    new id, only the next ``list`` does.  ``commandId`` is left out of
    ``controlParams`` as the app does, and ``OPENAIR`` goes first.

    Raises ``ValueError`` when the plan has no ``OPENAIR`` command, since
    every plan runs the A/C.
    """
    commands = sorted(plan.commands, key=lambda command: command.command_type != COMMAND_CLIMATE)
    if not commands or commands[0].command_type != COMMAND_CLIMATE:
        raise ValueError("a plan needs an OPENAIR command, seats and steering wheel only go on top of the A/C")
    inner: dict[str, Any] = dict(build_inner_base(config, vin=vin))
    if plan.id:
        inner["id"] = _plan_id(plan.id)
    inner.update(
        {
            "planName": plan.plan_name,
            "planType": plan.plan_type if plan.plan_type is not None else PLAN_TYPE_USER,
            "defaultPlan": plan.default_plan if plan.default_plan is not None else 0,
            **schedule_fields(schedule, now=now),
            "controlParams": json.dumps(
                [
                    {"commandType": command.command_type, "controlParamsMap": command.control_params_map}
                    for command in commands
                ],
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            "timeZone": config.time_zone,
        }
    )
    return inner


async def save_standby_plan(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    plan: StandbyPlan,
    schedule: StandbySchedule,
) -> dict[str, Any]:
    """POST ``save``.  The response only carries a ``message``, which can
    warn that a run less than 20 min ahead skips today."""
    decoded = await post_token_json(
        endpoint=SAVE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=build_save_inner(config, vin, plan, schedule),
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
        extra_code_map={SCHEDULE_REFUSED_CODES: BydStandbyScheduleError},
    )
    return decoded if isinstance(decoded, dict) else {}


async def _post_plan_id(
    endpoint: str,
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    plan_id: str,
) -> dict[str, Any]:
    inner: dict[str, Any] = dict(build_inner_base(config, vin=vin))
    inner["id"] = _plan_id(plan_id)
    inner["timeZone"] = config.time_zone
    decoded = await post_token_json(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    return decoded if isinstance(decoded, dict) else {}


async def set_default_standby_plan(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    plan_id: str,
) -> dict[str, Any]:
    """POST ``setDefault``.  The cloud clears the flag on the other plans."""
    return await _post_plan_id(SET_DEFAULT_ENDPOINT, config, session, transport, vin, plan_id)


async def delete_standby_plan(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    plan_id: str,
) -> dict[str, Any]:
    """POST ``delete``.  The app only offers it on user plans."""
    return await _post_plan_id(DELETE_ENDPOINT, config, session, transport, vin, plan_id)


async def trigger_send_standby_plan(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    plan_id: str,
) -> tuple[dict[str, Any], str | None]:
    """POST ``send`` to run a plan now.

    The app sends ``id`` and ``executeTime`` as strings here and no
    ``vin``; ``cancelReservation: "0"`` keeps the schedule.
    """
    now_ms = int(time.time() * 1000)
    inner: dict[str, Any] = dict(build_inner_base(config, now_ms=now_ms))
    inner.update(
        {
            "id": str(plan_id),
            "cancelReservation": "0",
            "executeTime": str(now_ms),
            "timeZone": config.time_zone,
        }
    )
    decoded = await post_token_json(
        endpoint=SEND_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return raw, raw.get("requestSerial")


async def poll_send_result(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    request_serial: str,
) -> tuple[dict[str, Any], str | None]:
    """POST ``send-result`` once."""
    inner = build_inner_base(config, vin=vin, request_serial=request_serial)
    decoded = await post_token_json(
        endpoint=SEND_RESULT_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    raw.setdefault("requestSerial", request_serial)
    return raw, request_serial


def make_send_fetch_fn(plan_id: str) -> Callable[..., Awaitable[tuple[dict[str, Any], str | None]]]:
    """Adapter for :meth:`BydClient._trigger_and_poll`."""

    async def _fetch(
        endpoint: str,
        config: BydConfig,
        session: Session,
        transport: Transport,
        vin: str,
        request_serial: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if endpoint == SEND_RESULT_ENDPOINT:
            return await poll_send_result(config, session, transport, vin, request_serial or "")
        return await trigger_send_standby_plan(config, session, transport, vin, plan_id)

    return _fetch


def is_send_ready(payload: dict[str, Any]) -> bool:
    """``res`` 1 is in progress; anything else is terminal."""
    res = payload.get("res")
    return isinstance(res, int) and res != 1
