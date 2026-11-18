"""Preconditioning plan endpoints (app 3.6.0 climate scheduling, #91).

Endpoints:
  - /control/standby-vehicles/list         (read plans)
  - /control/standby-vehicles/save         (edit a plan's commands or schedule)
  - /control/standby-vehicles/send         (run a plan now)
  - /control/standby-vehicles/send-result  (poll the run)

The guest account gets ``1001`` on ``list``, so these need the owner.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.standby import StandbyPlan, StandbyPlanList
from pybyd.session import Session

LIST_ENDPOINT = "/control/standby-vehicles/list"
SAVE_ENDPOINT = "/control/standby-vehicles/save"
SEND_ENDPOINT = "/control/standby-vehicles/send"
SEND_RESULT_ENDPOINT = "/control/standby-vehicles/send-result"


async def fetch_standby_plans(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> StandbyPlanList:
    """Fetch the vehicle's preconditioning plans."""
    inner = build_inner_base(config, vin=vin)
    inner["timeZone"] = config.time_zone
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


def build_save_inner(
    config: BydConfig,
    vin: str,
    plan: StandbyPlan,
    *,
    start_time: str,
    weekly_days: Sequence[int],
) -> dict[str, Any]:
    """Build the ``save`` payload the way app 3.6.0 sends it.

    ``executeType`` is always ``"2"`` and ``executeTime`` ``0``; the
    cloud computes the next run.  ``commandId`` is left out of
    ``controlParams`` as the app does.  Empty ``start_time`` and
    ``weekly_days`` unschedule the plan.
    """
    inner: dict[str, Any] = dict(build_inner_base(config, vin=vin))
    inner.update(
        {
            "id": int(plan.id) if plan.id.isdigit() else plan.id,
            "planName": plan.plan_name,
            "planType": plan.plan_type if plan.plan_type is not None else 1,
            "defaultPlan": plan.default_plan if plan.default_plan is not None else 0,
            "executeType": "2",
            "executeTime": 0,
            "startTime": start_time,
            "weeklyDays": ",".join(str(day) for day in sorted(set(weekly_days))),
            "controlParams": json.dumps(
                [
                    {"commandType": command.command_type, "controlParamsMap": command.control_params_map}
                    for command in plan.commands
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
    *,
    start_time: str,
    weekly_days: Sequence[int],
) -> dict[str, Any]:
    """POST ``save``.  The response only carries a ``message``."""
    inner = build_save_inner(config, vin, plan, start_time=start_time, weekly_days=weekly_days)
    decoded = await post_token_json(
        endpoint=SAVE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    return decoded if isinstance(decoded, dict) else {}


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
