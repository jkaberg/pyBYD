"""Preconditioning plans (``/control/standby-vehicles/*``), payloads from #91."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from pybyd._api import standby as standby_api
from pybyd._api.standby import build_save_inner, is_send_ready
from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.exceptions import BydRemoteControlError
from pybyd.models.standby import StandbyPlanList, StandbyPlanRunResult

_PARAMS = (
    '{"mainSettingTemp":5,"cycleMode":1,"remoteMode":4,"airConditioningMode":1,'
    '"airAccuracy":1,"copilotSettingTemp":5,"timeSpan":1}'
)
_PLAN = {
    "id": 1216038691305533440,
    "planName": "Conducción confort",
    "planType": 1,
    "defaultPlan": 1,
    "executeType": "2",
    "startTime": "13:30",
    "weeklyDays": "4,5,6",
    "executeTime": 1795177800000,
    "recExecuteTime": 1795177800000,
    "timeZone": "Europe/Madrid",
    "controlParams": '[{"commandType":"OPENAIR","controlParamsMap":' + _PARAMS + ',"commandId":"3"}]',
    "controlParamsList": [
        {
            "id": "1",
            "planId": "1216038691305533440",
            "commandId": "3",
            "commandType": "OPENAIR",
            "controlParamsMap": _PARAMS,
        }
    ],
}


def test_list_parses_plan() -> None:
    plans = StandbyPlanList.model_validate({"vin": "VIN", "data": [_PLAN]}).plans
    assert len(plans) == 1
    plan = plans[0]
    assert plan.id == "1216038691305533440"
    assert plan.weekly_days == [4, 5, 6]
    assert plan.scheduled is True
    assert plan.execute_time == datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
    assert plan.commands[0].command_type == "OPENAIR"
    assert plan.commands[0].command_id == "3"
    assert plan.commands[0].control_params_map["mainSettingTemp"] == 5


def test_unscheduled_plan() -> None:
    raw = {**_PLAN, "startTime": "01:00", "weeklyDays": "", "executeTime": 0, "recExecuteTime": 0}
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.weekly_days == []
    assert plan.execute_time is None
    assert plan.scheduled is False


def test_commands_fall_back_to_control_params_string() -> None:
    raw = {k: v for k, v in _PLAN.items() if k != "controlParamsList"}
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.commands[0].control_params_map["timeSpan"] == 1


def test_save_payload_matches_app() -> None:
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    inner = build_save_inner(
        BydConfig(username="u", password="p", time_zone="Europe/Madrid"),
        "VIN",
        plan,
        start_time="12:55",
        weekly_days=[5, 4],
    )
    assert inner["id"] == 1216038691305533440
    assert inner["executeType"] == "2"
    assert inner["executeTime"] == 0
    assert inner["startTime"] == "12:55"
    assert inner["weeklyDays"] == "4,5"
    assert inner["timeZone"] == "Europe/Madrid"
    commands = json.loads(inner["controlParams"])
    assert commands == [{"commandType": "OPENAIR", "controlParamsMap": json.loads(_PARAMS)}]


def test_send_ready() -> None:
    assert is_send_ready({"requestSerial": "X"}) is False
    assert is_send_ready({"res": 1}) is False
    assert is_send_ready({"res": 2}) is True


def _client() -> BydClient:
    return BydClient(BydConfig(username="user@example.invalid", password="unused"))


async def _passthrough(fn):  # type: ignore[no-untyped-def]
    return await fn()


@pytest.mark.parametrize(
    ("start_time", "weekly_days"),
    [("25:00", [1]), ("9:30", [1]), ("10:00", [7]), ("10:00", [-1])],
)
async def test_save_rejects_bad_schedule(start_time: str, weekly_days: list[int]) -> None:
    client = _client()
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    with patch.object(client, "_authed_call", AsyncMock()) as authed, pytest.raises(ValueError):
        await client.save_standby_plan("VIN", plan, start_time=start_time, weekly_days=weekly_days)
    authed.assert_not_awaited()


async def test_save_keeps_unset_values_and_refetches() -> None:
    client = _client()
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    with patch.object(client, "_authed_call", AsyncMock()) as authed:
        await client.save_standby_plan("VIN", plan, weekly_days=[0])
    save_call, list_call = authed.await_args_list
    assert save_call.kwargs == {"start_time": "13:30", "weekly_days": [0]}
    assert list_call.args == (standby_api.fetch_standby_plans, "VIN")


@pytest.mark.parametrize(("res", "raises"), [(1, False), (2, False), (None, False), (3, True)])
async def test_run_result_handling(res: int | None, raises: bool) -> None:
    client = _client()
    result = StandbyPlanRunResult.model_validate({"res": res, "message": "x"})
    with (
        patch.object(client, "_trigger_and_poll", AsyncMock(return_value=result)),
        patch.object(client, "_call_with_reauth", _passthrough),
    ):
        if raises:
            with pytest.raises(BydRemoteControlError):
                await client.run_standby_plan("VIN", "1")
        else:
            out = await client.run_standby_plan("VIN", "1")
            assert out.res == res
            assert out.vin == "VIN"
