"""Preconditioning plans (``/control/standby-vehicles/*``), payloads from #91."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from pybyd._api import standby as standby_api
from pybyd._api.standby import build_save_inner, is_send_ready, schedule_fields
from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.exceptions import BydError, BydRemoteControlError, BydStandbyScheduleError
from pybyd.models.standby import (
    PlanClimate,
    PlanSeats,
    StandbyPlan,
    StandbyPlanCommand,
    StandbyPlanList,
    StandbyPlanRunResult,
    StandbySchedule,
)

MADRID = ZoneInfo("Europe/Madrid")
_CONFIG = BydConfig(username="u", password="p", time_zone="Europe/Madrid")

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


def test_new_plan_has_null_schedule() -> None:
    # a plan created in the app, never scheduled
    raw = {
        "id": "2",
        "planName": "Test plan",
        "planType": 2,
        "defaultPlan": 0,
        "executeType": "0",
        "startTime": None,
        "weeklyDays": None,
        "executeTime": 0,
        "controlParamsList": [{"commandType": "OPENAIR", "controlParamsMap": _PARAMS}],
    }
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.start_time == ""
    assert plan.weekly_days == []
    assert plan.scheduled is False
    assert plan.preset is False
    assert plan.schedule == StandbySchedule.off()


@pytest.mark.parametrize(
    ("execute_type", "repeat"),
    [("0", "once"), ("1", "daily")],
)
def test_once_and_daily_scheduled_by_execute_time(execute_type: str, repeat: str) -> None:
    raw = {**_PLAN, "executeType": execute_type, "startTime": None, "weeklyDays": "", "executeTime": 1795371000000}
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.scheduled is True
    assert plan.schedule.repeat == repeat
    assert plan.schedule.at == datetime(2026, 11, 22, 18, 10, tzinfo=UTC)


def test_weekly_days_without_time_is_not_scheduled() -> None:
    raw = {**_PLAN, "startTime": "", "executeTime": 0, "recExecuteTime": 0}
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.weekly_days == [4, 5, 6]
    assert plan.scheduled is False


def test_save_payload_matches_app() -> None:
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    inner = build_save_inner(_CONFIG, "VIN", plan, StandbySchedule.weekly("12:55", [5, 4]))
    assert inner["id"] == 1216038691305533440
    assert inner["executeType"] == "2"
    assert inner["executeTime"] == 0
    assert inner["startTime"] == "12:55"
    assert inner["weeklyDays"] == "4,5"
    assert inner["timeZone"] == "Europe/Madrid"
    commands = json.loads(inner["controlParams"])
    assert commands == [{"commandType": "OPENAIR", "controlParamsMap": json.loads(_PARAMS)}]


def test_schedule_fields_match_app() -> None:
    # Sunday 22 at 19:10 once, and 23:12 every day saved on Wednesday 18 at 15:42
    once = datetime(2026, 11, 22, 19, 10, tzinfo=MADRID)
    daily = datetime(2026, 11, 18, 23, 12, tzinfo=MADRID)
    now = datetime(2026, 11, 18, 15, 42, tzinfo=MADRID)
    assert schedule_fields(StandbySchedule.once(once)) == {"executeType": "0", "executeTime": 1795371000000}
    assert schedule_fields(StandbySchedule.daily(daily), now=now) == {
        "executeType": "1",
        "executeTime": 1795039920000,
    }
    assert schedule_fields(StandbySchedule.off()) == {
        "executeType": "2",
        "executeTime": 0,
        "startTime": "",
        "weeklyDays": "",
    }


def test_daily_moves_past_time_to_next_day() -> None:
    at = datetime(2026, 11, 18, 7, 0, tzinfo=MADRID)
    fields = schedule_fields(StandbySchedule.daily(at), now=at + timedelta(days=2, hours=1))
    assert fields["executeTime"] == int((at + timedelta(days=3)).timestamp() * 1000)


def test_create_payload_has_no_id() -> None:
    plan = StandbyPlan(plan_name="Test plan", plan_type=2, default_plan=0, commands=[StandbyPlanCommand.climate(21)])
    inner = build_save_inner(_CONFIG, "VIN", plan, StandbySchedule.off())
    assert "id" not in inner
    assert inner["planType"] == 2
    assert inner["defaultPlan"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repeat": "weekly", "start_time": "25:00", "weekly_days": (1,)},
        {"repeat": "weekly", "start_time": "9:30", "weekly_days": (1,)},
        {"repeat": "weekly", "start_time": "10:00", "weekly_days": (7,)},
        {"repeat": "weekly", "start_time": "10:00", "weekly_days": ()},
        {"repeat": "once"},
        {"repeat": "daily", "at": datetime(2026, 11, 18, 7, 0)},
    ],
)
def test_schedule_rejects_bad_values(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        StandbySchedule(**kwargs)


async def test_list_sends_user_id() -> None:
    # without userId the cloud answers 1001, owner account or not
    session = SimpleNamespace(user_id="42")
    with patch.object(standby_api, "post_token_json", AsyncMock(return_value=[_PLAN])) as post:
        plans = await standby_api.fetch_standby_plans(_CONFIG, session, None, "VIN")  # type: ignore[arg-type]
    assert post.await_args.kwargs["inner"]["userId"] == "42"
    assert len(plans.plans) == 1


@pytest.mark.parametrize(
    ("fn", "endpoint"),
    [
        (standby_api.delete_standby_plan, standby_api.DELETE_ENDPOINT),
        (standby_api.set_default_standby_plan, standby_api.SET_DEFAULT_ENDPOINT),
    ],
)
async def test_plan_id_endpoints(fn, endpoint: str) -> None:  # type: ignore[no-untyped-def]
    with patch.object(standby_api, "post_token_json", AsyncMock(return_value={"message": "ok"})) as post:
        await fn(_CONFIG, None, None, "VIN", "1216038691305533440")
    assert post.await_args.kwargs["endpoint"] == endpoint
    inner = post.await_args.kwargs["inner"]
    assert inner["id"] == 1216038691305533440
    assert inner["vin"] == "VIN"


def test_send_ready() -> None:
    assert is_send_ready({"requestSerial": "X"}) is False
    assert is_send_ready({"res": 1}) is False
    assert is_send_ready({"res": 2}) is True


def _client() -> BydClient:
    return BydClient(BydConfig(username="user@example.invalid", password="unused"))


async def _passthrough(fn):  # type: ignore[no-untyped-def]
    return await fn()


async def test_save_keeps_schedule_and_refetches() -> None:
    client = _client()
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    fresh = StandbyPlanList.model_validate({"data": [_PLAN]})
    with patch.object(client, "_authed_call", AsyncMock(side_effect=[{"message": "saved"}, fresh])) as authed:
        await client.save_standby_plan("VIN", plan)
    save_call, list_call = authed.await_args_list
    assert save_call.args[3] == StandbySchedule.weekly("13:30", [4, 5, 6])
    assert list_call.args == (standby_api.fetch_standby_plans, "VIN")


async def test_save_returns_skip_today_warning() -> None:
    # daily inside 20 min: code 0, saved, but today is skipped (#91)
    client = _client()
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    warning = "Conducción confort plan saved. The scheduled time is within 20 min, and will not be executed today"
    fresh = StandbyPlanList.model_validate({"data": [_PLAN]})
    with patch.object(client, "_authed_call", AsyncMock(side_effect=[{"message": warning}, fresh])):
        plans = await client.save_standby_plan("VIN", plan)
    assert plans.message == warning
    assert StandbyPlanList.model_validate({"data": [_PLAN]}).message is None


async def test_save_maps_6111_to_schedule_error() -> None:
    # once inside 20 min: "at least 20 min from now and within the next 30 days" (#91)
    plan = StandbyPlanList.model_validate({"data": [_PLAN]}).plans[0]
    with patch.object(standby_api, "post_token_json", AsyncMock(return_value={})) as post:
        await standby_api.save_standby_plan(_CONFIG, None, None, "VIN", plan, StandbySchedule.off())  # type: ignore[arg-type]
    code_map = post.await_args.kwargs["extra_code_map"]
    assert any("6111" in codes and exc is BydStandbyScheduleError for codes, exc in code_map.items())


async def test_create_returns_new_plan() -> None:
    client = _client()
    preset = StandbyPlanList.model_validate({"data": [_PLAN]})
    created = StandbyPlanList.model_validate(
        {"data": [_PLAN, {"id": "7", "planName": "Test plan", "planType": 2, "executeType": "0"}]}
    )
    calls = AsyncMock(side_effect=[preset, {"message": "saved"}, created])
    with patch.object(client, "_authed_call", calls):
        plan = await client.create_standby_plan("VIN", "Test plan", preset.plans[0].commands)
    assert plan.id == "7"
    saved = calls.await_args_list[1].args[2]
    assert saved.id == ""
    assert saved.commands[0].command_id is None


async def test_create_fails_without_new_plan() -> None:
    client = _client()
    preset = StandbyPlanList.model_validate({"data": [_PLAN]})
    with (
        patch.object(client, "_authed_call", AsyncMock(side_effect=[preset, {}, preset])),
        pytest.raises(BydError),
    ):
        await client.create_standby_plan("VIN", "Test plan", preset.plans[0].commands)


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


# the three sections of the plan edit screen, as saved by the app in #91
_CAPTURED_COMMANDS = [
    {
        "commandType": "OPENAIR",
        "controlParamsMap": {
            "remoteMode": 4,
            "timeSpan": 1,
            "mainSettingTemp": 5,
            "copilotSettingTemp": 5,
            "cycleMode": 1,
            "airConditioningMode": 1,
            "airAccuracy": 1,
        },
    },
    {
        "commandType": "VENTILATIONHEATING",
        "controlParamsMap": {
            "mainHeat": 1,
            "mainVentilation": 3,
            "copilotHeat": 3,
            "copilotVentilation": 1,
            "lrSeatHeatState": 0,
            "rrSeatHeatState": 0,
            "lrSeatVentilationState": 0,
            "rrSeatVentilationState": 0,
            "lrThirdHeatState": 0,
            "rrThirdHeatState": 0,
            "lrThirdVentilationState": 0,
            "rrThirdVentilationState": 0,
            "chairType": 8,
        },
    },
    {
        "commandType": "STEERINGWHEELHEATING",
        "controlParamsMap": {"steeringWheelHeatState": 1, "chairType": 5},
    },
]
_CAPTURED_SEATS = PlanSeats(
    driver_heat=True, driver_ventilation=False, passenger_heat=False, passenger_ventilation=True
)


def test_builders_match_app() -> None:
    commands = [
        StandbyPlanCommand.climate(19, 10),
        StandbyPlanCommand.seats(_CAPTURED_SEATS),
        StandbyPlanCommand.steering_wheel(),
    ]
    plan = StandbyPlan(plan_name="x", commands=commands)
    inner = build_save_inner(_CONFIG, "VIN", plan, StandbySchedule.off())
    assert json.loads(inner["controlParams"]) == _CAPTURED_COMMANDS


def test_readers() -> None:
    raw = {
        **_PLAN,
        "controlParamsList": [{**c, "controlParamsMap": json.dumps(c["controlParamsMap"])} for c in _CAPTURED_COMMANDS],
    }
    plan = StandbyPlanList.model_validate({"data": [raw]}).plans[0]
    assert plan.climate() == PlanClimate(temperature=19, duration=10)
    assert plan.climate(temp_offset=16) == PlanClimate(temperature=21, duration=10)
    assert plan.seats == _CAPTURED_SEATS
    assert plan.seats.active is True
    assert plan.steering_wheel_heat is True


def test_readers_without_commands() -> None:
    plan = StandbyPlan(commands=[StandbyPlanCommand.climate(22, 30)])
    assert plan.climate() == PlanClimate(temperature=22, duration=30)
    assert plan.seats is None
    assert plan.steering_wheel_heat is False


def test_absent_seat_feature_is_zero() -> None:
    command = StandbyPlanCommand.seats(PlanSeats(driver_heat=True, passenger_heat=False))
    assert command.control_params_map["mainHeat"] == 1
    assert command.control_params_map["copilotHeat"] == 3
    assert command.control_params_map["mainVentilation"] == 0
    assert PlanSeats(passenger_heat=False).active is False


def test_recirculation() -> None:
    # captured: 22 °C with internal circulation saves cycleMode 2 (#91)
    command = StandbyPlanCommand.climate(20, 10, recirculation=True)
    assert command.control_params_map["cycleMode"] == 2
    plan = StandbyPlan(commands=[command])
    assert plan.climate() == PlanClimate(temperature=20, duration=10, recirculation=True)


def test_save_puts_climate_first() -> None:
    plan = StandbyPlan(commands=[StandbyPlanCommand.steering_wheel(), StandbyPlanCommand.climate(21)])
    inner = build_save_inner(_CONFIG, "VIN", plan, StandbySchedule.off())
    assert [c["commandType"] for c in json.loads(inner["controlParams"])] == ["OPENAIR", "STEERINGWHEELHEATING"]


def test_save_needs_climate() -> None:
    plan = StandbyPlan(commands=[StandbyPlanCommand.steering_wheel()])
    with pytest.raises(ValueError, match="OPENAIR"):
        build_save_inner(_CONFIG, "VIN", plan, StandbySchedule.off())
