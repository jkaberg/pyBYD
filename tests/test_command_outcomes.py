"""Tests for surfacing real remote-control outcomes.

Two halves of the behaviour:

1. ``_poll_remote_control_once`` raises ``BydRemoteControlError`` instead of
   returning a fabricated tentative-success result when the command can never
   be confirmed (no request serial, or the poll loop times out).
2. ``BydCar._execute_command`` rolls back the optimistic projection and
   re-raises on ``BydRemoteControlError`` rather than swallowing it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import pybyd._api.control as control
from pybyd._api.control import _poll_remote_control_once, poll_remote_control
from pybyd.car import BydCar
from pybyd.exceptions import BydApiError, BydRateLimitError, BydRemoteControlError
from pybyd.models.control import RemoteCommand, RemoteControlResult


def _make_fetch_stub(responses: list[tuple[dict[str, Any], str | None]]):
    """Return an async stub for ``_fetch_control_endpoint``.

    Yields the queued ``(result, serial)`` tuples in order; repeats the last
    one once exhausted so poll loops of any length stay fed.
    """
    calls = {"i": 0}

    async def _stub(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        idx = min(calls["i"], len(responses) - 1)
        calls["i"] += 1
        return responses[idx]

    return _stub


@pytest.mark.asyncio
async def test_no_serial_raises_instead_of_tentative_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trigger returns a non-terminal result with no serial -> raise."""
    monkeypatch.setattr(
        control,
        "_fetch_control_endpoint",
        _make_fetch_stub([({}, None)]),
    )

    with pytest.raises(BydRemoteControlError) as exc_info:
        await _poll_remote_control_once(None, None, None, "VIN", RemoteCommand.FLASH_LIGHTS, mqtt_result_waiter=None)

    assert exc_info.value.code == "no_serial"


@pytest.mark.asyncio
async def test_timeout_raises_when_poll_never_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serial present but the poll loop never reaches a terminal state -> timeout."""
    # Trigger: has serial, not terminal. Polls: res=1 (in progress) forever.
    monkeypatch.setattr(
        control,
        "_fetch_control_endpoint",
        _make_fetch_stub([({"requestSerial": "S1"}, "S1"), ({"res": 1}, "S1")]),
    )

    with pytest.raises(BydRemoteControlError) as exc_info:
        await _poll_remote_control_once(
            None,
            None,
            None,
            "VIN",
            RemoteCommand.FLASH_LIGHTS,
            poll_attempts=2,
            poll_interval=0,
            mqtt_result_waiter=None,
        )

    assert exc_info.value.code == "timeout"


class _FakeEngine:
    """Minimal state-engine stand-in tracking projection lifecycle calls."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.registered = 0
        self.rolled_back: list[str] = []

    def register_projections(self, specs: Any) -> str:
        self.registered += 1
        return "cmd-1"

    def rollback_projections(self, command_id: str) -> None:
        self.rolled_back.append(command_id)


class _StubCar:
    """Just enough of BydCar to drive _execute_command."""

    def __init__(self) -> None:
        self._engine = _FakeEngine()
        self._vin = "VIN"


@pytest.mark.asyncio
async def test_execute_command_rolls_back_and_reraises_on_control_error() -> None:
    """A BydRemoteControlError rolls back the projection and propagates."""
    car = _StubCar()

    async def _failing() -> None:
        raise BydRemoteControlError("rejected", code="2", endpoint="/control/remoteControl")

    with pytest.raises(BydRemoteControlError):
        await BydCar._execute_command(car, _failing, ["projection"])

    assert car._engine.rolled_back == ["cmd-1"]


@pytest.mark.asyncio
async def test_execute_command_keeps_projection_on_success() -> None:
    """A successful command must NOT roll back the optimistic projection."""
    car = _StubCar()

    async def _ok() -> None:
        return None

    await BydCar._execute_command(car, _ok, ["projection"])

    assert car._engine.registered == 1
    assert car._engine.rolled_back == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("poll_payload", "expected_msg"),
    [
        ({"res": 3, "message": "Door is open"}, "Door is open"),
        ({"controlState": 2, "message": "Vehicle refused"}, "Vehicle refused"),
        ({"res": 2, "batteryHeatResult": 6, "message": "Conditions not met"}, "Conditions not met"),
    ],
)
async def test_poll_remote_control_default_does_not_replay_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
    poll_payload: dict[str, Any],
    expected_msg: str,
) -> None:
    """Default command_retries=1 never replays a refused command (avoids 6024 collisions)."""
    endpoints: list[str] = []
    sleeps: list[float] = []
    responses: list[tuple[dict[str, Any], str | None] | Exception] = [
        ({"requestSerial": "S1"}, "S1"),
        (poll_payload, "S1"),
        # If a replay were attempted, BYD would answer 6024 and mask the refusal.
        BydApiError("The last operation has not finished", code="6024", endpoint="/control/remoteControl"),
    ]

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    with pytest.raises(BydRemoteControlError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.LOCK,
            poll_interval=0.0,
        )

    assert exc_info.value.code == "2"
    assert expected_msg in str(exc_info.value)
    assert endpoints == ["/control/remoteControl", "/control/remoteControlResult"]
    assert sleeps == []


@pytest.mark.asyncio
async def test_poll_remote_control_default_does_not_replay_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default command_retries=1 does not re-trigger an unconfirmed timed-out actuation."""
    endpoints: list[str] = []
    sleeps: list[float] = []

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        if endpoint == "/control/remoteControl":
            return ({"requestSerial": "S1"}, "S1")
        return ({"res": 1}, "S1")

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    with pytest.raises(BydRemoteControlError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.OPEN_TRUNK,
            poll_attempts=2,
            poll_interval=0.0,
        )

    assert exc_info.value.code == "timeout"
    assert endpoints == [
        "/control/remoteControl",
        "/control/remoteControlResult",
        "/control/remoteControlResult",
    ]
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger_response", "poll_response"),
    [
        (({"controlState": 2, "message": "Refused immediately"}, "S1"), None),
        (({"res": 3, "message": "Immediate res=3"}, "S1"), None),
        (({"requestSerial": "S1"}, "S1"), ({"res": 3, "message": "Refused on poll"}, "S1")),
        (({"requestSerial": "S1"}, "S1"), ({"controlState": 2, "message": "Poll controlState=2"}, "S1")),
    ],
)
async def test_poll_remote_control_never_replays_terminal_refusal_even_with_command_retries(
    monkeypatch: pytest.MonkeyPatch,
    trigger_response: tuple[dict[str, Any], str | None],
    poll_response: tuple[dict[str, Any], str | None] | None,
) -> None:
    """Explicit command_retries > 1 still stops immediately on terminal vehicle refusal (code='2')."""
    endpoints: list[str] = []
    sleeps: list[float] = []

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        if endpoint == "/control/remoteControl":
            return trigger_response
        assert poll_response is not None
        return poll_response

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    with pytest.raises(BydRemoteControlError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.LOCK,
            poll_interval=0.0,
            command_retries=3,
            command_retry_delay=3.0,
        )

    assert exc_info.value.code == "2"
    expected_endpoints = ["/control/remoteControl"]
    if poll_response is not None:
        expected_endpoints.append("/control/remoteControlResult")
    assert endpoints == expected_endpoints
    assert sleeps == []


@pytest.mark.asyncio
async def test_mqtt_terminal_refusal_raises_immediately_without_polling_or_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An MQTT ack with controlState=2/res=3 surfaces immediately without falling back to polling or replay."""
    endpoints: list[str] = []
    sleeps: list[float] = []

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        return ({"requestSerial": "S1"}, "S1")

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def _mqtt_refusal(_serial: str | None) -> RemoteControlResult:
        return RemoteControlResult.model_validate({"res": 3, "message": "Door open", "requestSerial": "S1"})

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    with pytest.raises(BydRemoteControlError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.LOCK,
            poll_interval=0.0,
            command_retries=3,
            command_retry_delay=3.0,
            mqtt_result_waiter=_mqtt_refusal,
        )

    assert exc_info.value.code == "2"
    assert "Door open" in str(exc_info.value)
    assert endpoints == ["/control/remoteControl"]
    assert sleeps == []


@pytest.mark.asyncio
async def test_mqtt_waiter_error_still_falls_back_to_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception from mqtt_result_waiter still falls back to HTTP polling."""
    endpoints: list[str] = []

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        if endpoint == "/control/remoteControl":
            return ({"requestSerial": "S1"}, "S1")
        return ({"res": 2, "requestSerial": "S1"}, "S1")

    async def _broken_waiter(_serial: str | None) -> RemoteControlResult | None:
        raise RuntimeError("mqtt disconnected")

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)

    res = await poll_remote_control(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        "VIN",
        RemoteCommand.LOCK,
        poll_interval=0.0,
        mqtt_result_waiter=_broken_waiter,
    )

    assert res.success is True
    assert endpoints == ["/control/remoteControl", "/control/remoteControlResult"]


@pytest.mark.asyncio
async def test_explicit_command_retries_retries_timeout_and_service_error_but_stops_on_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in command_retries > 1 retries transient failures (timeout, 1009) and still halts on refusal."""
    endpoints: list[str] = []
    sleeps: list[float] = []
    responses: list[tuple[dict[str, Any], str | None] | Exception] = [
        # Attempt 1: service error 1009 on trigger -> retry
        BydRemoteControlError("service error", code="1009", endpoint="/control/remoteControl"),
        # Attempt 2: trigger accepted, poll times out -> retry
        ({"requestSerial": "S2"}, "S2"),
        ({"res": 1}, "S2"),
        # Attempt 3: trigger accepted, poll succeeds
        ({"requestSerial": "S3"}, "S3"),
        ({"res": 2}, "S3"),
    ]

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    res = await poll_remote_control(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        "VIN",
        RemoteCommand.FLASH_LIGHTS,
        poll_attempts=1,
        poll_interval=0.0,
        command_retries=3,
        command_retry_delay=2.5,
    )

    assert res.success is True
    assert endpoints == [
        "/control/remoteControl",
        "/control/remoteControl",
        "/control/remoteControlResult",
        "/control/remoteControl",
        "/control/remoteControlResult",
    ]
    assert sleeps == [2.5, 2.5]

    # Now verify attempt 1 timeout -> attempt 2 refusal stops before attempt 3.
    endpoints.clear()
    sleeps.clear()
    responses = [
        ({"requestSerial": "S1"}, "S1"),
        ({"res": 1}, "S1"),
        ({"requestSerial": "S2"}, "S2"),
        ({"res": 3, "message": "Rejected"}, "S2"),
    ]

    with pytest.raises(BydRemoteControlError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.FLASH_LIGHTS,
            poll_attempts=1,
            poll_interval=0.0,
            command_retries=3,
            command_retry_delay=2.5,
        )

    assert exc_info.value.code == "2"
    assert endpoints == [
        "/control/remoteControl",
        "/control/remoteControlResult",
        "/control/remoteControl",
        "/control/remoteControlResult",
    ]
    assert sleeps == [2.5]


@pytest.mark.asyncio
async def test_initial_trigger_6024_still_retried_with_default_command_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code 6024 on the initial trigger (never accepted) is still retried via rate_limit_retries."""
    endpoints: list[str] = []
    sleeps: list[float] = []
    responses: list[tuple[dict[str, Any], str | None] | Exception] = [
        BydApiError("Last operation not finished", code="6024", endpoint="/control/remoteControl"),
        BydApiError("Last operation not finished", code="6024", endpoint="/control/remoteControl"),
        ({"requestSerial": "S1"}, "S1"),
        ({"res": 2}, "S1"),
    ]

    async def _fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        endpoints.append(endpoint)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(control, "_fetch_control_endpoint", _fake_fetch)
    monkeypatch.setattr(control.asyncio, "sleep", _fake_sleep)

    res = await poll_remote_control(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        "VIN",
        RemoteCommand.LOCK,
        poll_interval=0.0,
        rate_limit_retries=3,
        rate_limit_delay=4.0,
    )

    assert res.success is True
    assert endpoints == [
        "/control/remoteControl",
        "/control/remoteControl",
        "/control/remoteControl",
        "/control/remoteControlResult",
    ]
    assert sleeps == [4.0, 4.0]

    # Exhausting rate_limit_retries still raises BydRateLimitError.
    sleeps.clear()
    responses = [
        BydApiError("Last operation not finished", code="6024", endpoint="/control/remoteControl"),
        BydApiError("Last operation not finished", code="6024", endpoint="/control/remoteControl"),
    ]

    with pytest.raises(BydRateLimitError) as exc_info:
        await poll_remote_control(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "VIN",
            RemoteCommand.LOCK,
            poll_interval=0.0,
            rate_limit_retries=2,
            rate_limit_delay=4.0,
        )

    assert exc_info.value.code == "6024"
    assert sleeps == [4.0, 4.0]
