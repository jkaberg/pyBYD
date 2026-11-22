"""Tests for session-reauth retry semantics."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd._api import standby as standby_api
from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.exceptions import BydError, BydServerTimeError, BydSessionExpiredError, BydTokenRefreshError
from pybyd.session import Session


def _make_client() -> BydClient:
    config = BydConfig(username="user@example.com", password="secret")
    return BydClient(config)


def _patch_reauth(client: BydClient, calls: list[str]) -> None:
    """Stub out the network-touching reauth steps."""

    async def fake_ensure_session():  # type: ignore[no-untyped-def]
        calls.append("ensure_session")
        return None

    client.ensure_session = fake_ensure_session  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_call_with_reauth_retries_once_by_default() -> None:
    """Default behavior: one retry after session expiry."""
    client = _make_client()
    calls: list[str] = []
    _patch_reauth(client, calls)
    attempts = 0

    async def fn() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BydSessionExpiredError("expired", code="401", endpoint="/x")
        return "ok"

    result = await client._call_with_reauth(fn)  # noqa: SLF001
    assert result == "ok"
    assert attempts == 2
    assert "ensure_session" in calls


@pytest.mark.asyncio
async def test_call_with_reauth_vetoed_after_trigger_dispatch() -> None:
    """retry_if=False must propagate the expiry without re-running fn."""
    client = _make_client()
    calls: list[str] = []
    _patch_reauth(client, calls)
    attempts = 0

    async def fn() -> str:
        nonlocal attempts
        attempts += 1
        raise BydSessionExpiredError("expired", code="401", endpoint="/x")

    with pytest.raises(BydSessionExpiredError):
        await client._call_with_reauth(fn, retry_if=lambda: False)  # noqa: SLF001

    assert attempts == 1
    assert calls == []  # no reauth attempted; nothing to retry into


@pytest.mark.asyncio
async def test_call_with_reauth_retry_if_true_still_retries() -> None:
    """retry_if=True keeps the single-retry behavior."""
    client = _make_client()
    calls: list[str] = []
    _patch_reauth(client, calls)
    attempts = 0

    async def fn() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BydSessionExpiredError("expired", code="401", endpoint="/x")
        return "ok"

    result = await client._call_with_reauth(fn, retry_if=lambda: True)  # noqa: SLF001
    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [BydTokenRefreshError, BydServerTimeError])
async def test_call_with_reauth_veto_covers_refresh_and_clock_drift(exc_type: type[BydError]) -> None:
    """A dispatched command is not re-sent on token refresh or clock-drift errors either."""
    client = _make_client()
    refreshes: list[str] = []

    async def fake_refresh() -> None:
        refreshes.append("refresh")

    client._refresh_or_relogin = fake_refresh  # type: ignore[method-assign]  # noqa: SLF001
    attempts = 0

    async def fn() -> str:
        nonlocal attempts
        attempts += 1
        raise exc_type("boom", code="x", endpoint="/x")

    with pytest.raises(exc_type):
        await client._call_with_reauth(fn, retry_if=lambda: False)  # noqa: SLF001

    assert attempts == 1
    assert refreshes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [BydSessionExpiredError, BydTokenRefreshError])
async def test_run_standby_plan_is_not_re_sent_when_the_result_poll_fails(exc_type: type[BydError]) -> None:
    """An auth error while polling the result must not trigger the plan a second time."""
    client = BydClient(BydConfig(username="user@example.com", password="secret", mqtt_enabled=False))
    client._session = Session(user_id="u1", sign_token="a" * 32, encry_token="b" * 32)  # noqa: SLF001
    client._transport = MagicMock()  # noqa: SLF001
    client._refresh_or_relogin = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
    client.login = AsyncMock()  # type: ignore[method-assign]
    triggers = 0

    async def fake_fetch(endpoint: str, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
        nonlocal triggers
        if endpoint == standby_api.SEND_RESULT_ENDPOINT:
            raise exc_type("boom", code="x", endpoint=endpoint)
        triggers += 1
        return {"res": 1}, "serial-1"

    with (
        patch("pybyd.client._standby_api.make_send_fetch_fn", return_value=fake_fetch),
        pytest.raises(exc_type),
    ):
        await client.run_standby_plan("VIN123", "plan-1", poll_attempts=2, poll_interval=0)

    assert triggers == 1
