"""Stopping MQTT ends pending waits like a timeout, so reads fall back to HTTP.

A decrypt failure schedules a re-login, and the re-login restarts MQTT
(``_stop_mqtt``) while a read may be waiting for its push. Cancelling the
waiters' futures raised ``CancelledError`` out of that read instead of
letting it poll over HTTP.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from pybyd.client import BydClient
from pybyd.models import GpsInfo


def _client() -> BydClient:
    client = BydClient.__new__(BydClient)  # no __init__: avoid network/config
    client._config = SimpleNamespace(mqtt_timeout=10.0)  # type: ignore[assignment]
    client._loop = None
    client._mqtt_runtime = MagicMock(is_running=True)
    client._mqtt_waiters = []
    client._data_poll_serials = set()
    return client


async def _until_waiting(client: BydClient) -> None:
    for _ in range(100):
        if client._mqtt_waiters:
            return
        await asyncio.sleep(0)
    raise AssertionError("no MQTT waiter was registered")


@pytest.mark.asyncio
async def test_stopping_mqtt_ends_a_wait_like_a_timeout() -> None:
    client = _client()
    wait = asyncio.create_task(client._mqtt_wait("VIN", event_type="vehicleInfo", serial="S1"))
    await _until_waiting(client)

    client._stop_mqtt()

    assert await asyncio.wait_for(wait, 1) is None
    assert client._mqtt_waiters == []


@pytest.mark.asyncio
async def test_cancelling_the_caller_still_cancels() -> None:
    client = _client()
    wait = asyncio.create_task(client._mqtt_wait("VIN", event_type="vehicleInfo", serial="S1"))
    await _until_waiting(client)

    wait.cancel()

    with pytest.raises(asyncio.CancelledError):
        await wait
    assert client._mqtt_waiters == []


@pytest.mark.asyncio
async def test_a_read_falls_back_to_http_when_mqtt_restarts_under_it() -> None:
    client = _client()
    client.ensure_session = MagicMock(side_effect=lambda: asyncio.sleep(0, result="session"))  # type: ignore[method-assign]
    client._require_transport = MagicMock(return_value="transport")  # type: ignore[method-assign]
    calls: list[str] = []

    async def fetch(endpoint: str, *args: Any) -> tuple[dict[str, Any], str | None]:
        calls.append(endpoint)
        if endpoint == "/trigger":
            return {}, "S1"
        return {"latitude": 52.0, "longitude": 4.0}, "S1"

    read = asyncio.create_task(
        client._trigger_and_poll(
            vin="VIN",
            trigger_endpoint="/trigger",
            poll_endpoint="/poll",
            fetch_fn=fetch,
            is_ready=lambda raw: "latitude" in raw,
            model_cls=GpsInfo,
            label="GPS",
            mqtt_event_type="vehicleInfo",
            poll_interval=0,
        )
    )
    await _until_waiting(client)

    client._stop_mqtt()  # what a decrypt-failure re-login does

    gps = await asyncio.wait_for(read, 1)
    assert gps.latitude == 52.0
    assert calls == ["/trigger", "/poll"]
