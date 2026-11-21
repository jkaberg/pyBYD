"""Tests for the official 33s 10-step command polling ladder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import BydClient, BydConfig
from pybyd._api.control import poll_remote_control
from pybyd._constants import COMMAND_POLL_DELAYS_S, resolve_poll_delays
from pybyd.models.control import RemoteCommand, RemoteControlResult
from pybyd.session import Session


def test_official_poll_ladder_shape_and_total() -> None:
    assert COMMAND_POLL_DELAYS_S == (1.0, 0.5, 0.5, 2.0, 2.0, 2.0, 5.0, 5.0, 5.0, 10.0)
    assert len(COMMAND_POLL_DELAYS_S) == 10
    assert sum(COMMAND_POLL_DELAYS_S) == pytest.approx(33.0)


def test_resolve_poll_delays_variants() -> None:
    assert resolve_poll_delays(10, None) == list(COMMAND_POLL_DELAYS_S)
    assert resolve_poll_delays(3, None) == [1.0, 0.5, 0.5]
    assert resolve_poll_delays(12, None) == [*COMMAND_POLL_DELAYS_S, 10.0, 10.0]
    assert resolve_poll_delays(0, None) == []
    assert resolve_poll_delays(4, 1.5) == [1.5, 1.5, 1.5, 1.5]
    assert resolve_poll_delays(3, 0.0) == [0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_run_remote_control_uses_official_ladder_by_default() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = MagicMock(spec=Session)
    transport = MagicMock()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    responses = [
        ({"controlState": 0, "uuid": "ser-1"}, "ser-1"),  # trigger
        ({"controlState": 0, "uuid": "ser-1"}, "ser-1"),  # poll 1 (1.0s)
        ({"controlState": 0, "uuid": "ser-1"}, "ser-1"),  # poll 2 (0.5s)
        ({"controlState": 0, "uuid": "ser-1"}, "ser-1"),  # poll 3 (0.5s)
        ({"controlState": 1, "uuid": "ser-1"}, "ser-1"),  # poll 4 (2.0s) -> ready
    ]

    with (
        patch("pybyd._api.control._fetch_control_endpoint", new=AsyncMock(side_effect=responses)),
        patch("pybyd._api.control.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)),
    ):
        res = await poll_remote_control(
            cfg,
            session,
            transport,
            "TESTVIN",
            RemoteCommand.FLASH_LIGHTS,
            command_pwd="0123456789abcdef0123456789abcdef",
        )

    assert isinstance(res, RemoteControlResult)
    assert res.control_state == 1
    assert sleeps == [1.0, 0.5, 0.5, 2.0]


@pytest.mark.asyncio
async def test_trigger_and_poll_uses_official_ladder_when_poll_interval_none() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    client._session = MagicMock(spec=Session)
    client._session.is_expired = False
    client._transport = MagicMock()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    fetch_fn = AsyncMock(
        side_effect=[
            ({"res": 1}, "ser-2"),  # trigger
            ({"res": 1}, "ser-2"),  # poll 1 (1.0s)
            ({"res": 1}, "ser-2"),  # poll 2 (0.5s)
            ({"res": 1}, "ser-2"),  # poll 3 (0.5s)
            ({"res": 1}, "ser-2"),  # poll 4 (2.0s)
            ({"res": 2}, "ser-2"),  # poll 5 (2.0s) -> ready
        ]
    )

    with patch("pybyd.client.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        model = await client._trigger_and_poll(
            vin="TESTVIN",
            trigger_endpoint="/control/smartCharge/saveOrUpdate",
            poll_endpoint="/control/smartCharge/changeResult",
            fetch_fn=fetch_fn,
            is_ready=lambda raw: raw.get("res") == 2,
            model_cls=RemoteControlResult,
            label="TestLadder",
            poll_attempts=10,
            poll_interval=None,
        )

    assert model.raw["res"] == 2
    assert sleeps == [1.0, 0.5, 0.5, 2.0, 2.0]
