"""Tests for 1030 token refresh (/app/token/refresh) and 1003 server clock sync."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import (
    BydClient,
    BydConfig,
    BydServerTimeError,
    BydSessionExpiredError,
    BydTokenRefreshError,
)
from pybyd._api._common import _raise_for_code, build_inner_base, post_token_json
from pybyd._api.login import parse_login_response
from pybyd._transport import SecureTransport
from pybyd.models.token import AuthToken
from pybyd.session import Session


def test_raise_for_code_1030_and_1003() -> None:
    with pytest.raises(BydTokenRefreshError) as exc_1030:
        _raise_for_code(endpoint="/test", code="1030", message="token time diff")
    assert isinstance(exc_1030.value, BydSessionExpiredError)

    with pytest.raises(BydServerTimeError):
        _raise_for_code(endpoint="/test", code="1003", message="server time diff")


def test_transport_sync_clock_from_header_threshold() -> None:
    transport = SecureTransport(
        config=BydConfig(username="u@example.com", password="secret"),
        codec=MagicMock(),
        http_session=MagicMock(),
    )
    now_s = time.time()
    within = format_datetime(datetime.fromtimestamp(now_s + 5, tz=UTC), usegmt=True)
    transport._sync_clock_from_header(within)
    assert transport.server_time_offset_ms == 0

    drifted = format_datetime(datetime.fromtimestamp(now_s + 120, tz=UTC), usegmt=True)
    transport._sync_clock_from_header(drifted)
    assert 115_000 <= transport.server_time_offset_ms <= 125_000

    transport._sync_clock_from_header(within)
    assert transport.server_time_offset_ms == 0


@pytest.mark.asyncio
async def test_post_token_json_applies_server_time_offset() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = Session(user_id="u1", sign_token="s" * 32, encry_token="e" * 32)
    transport = MagicMock()
    transport.server_time_offset_ms = 90_000
    transport.post_secure = AsyncMock(return_value={"code": "0", "respondData": ""})

    inner = build_inner_base(cfg, now_ms=1_700_000_000_000)
    captured: dict[str, object] = {}

    def fake_envelope(c, s, inner_dict, now_ms, user_type=None):
        captured["inner_ts"] = inner_dict["timeStamp"]
        captured["outer_now_ms"] = now_ms
        return {"encryData": "x"}, s.content_key()

    with patch("pybyd._api._common.build_token_outer_envelope", side_effect=fake_envelope):
        await post_token_json(
            endpoint="/test",
            config=cfg,
            session=session,
            transport=transport,
            inner=inner,
        )

    assert int(str(captured["inner_ts"])) == int(captured["outer_now_ms"])
    assert int(str(captured["inner_ts"])) > 1_700_000_000_000


def test_session_with_refreshed_tokens_preserves_vin_caches() -> None:
    session = Session(user_id="u1", sign_token="a" * 32, encry_token="b" * 32, refresh_token="rt1")
    session.guest_vins["VIN1"] = True
    session.user_type_settled.add(("VIN1", "/ep"))

    refreshed = session.with_refreshed_tokens(sign_token="c" * 32, encry_token="d" * 32, refresh_token="rt2")
    assert refreshed.sign_token == "c" * 32
    assert refreshed.encry_token == "d" * 32
    assert refreshed.refresh_token == "rt2"
    assert refreshed.guest_vins == {"VIN1": True}
    assert refreshed.user_type_settled == {("VIN1", "/ep")}


@pytest.mark.asyncio
async def test_call_with_reauth_refreshes_token_on_1030_when_refresh_token_present() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    client._session = Session(user_id="u1", sign_token="a" * 32, encry_token="b" * 32, refresh_token="rt1")
    client._transport = MagicMock()

    calls = 0

    async def flaky_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BydTokenRefreshError("1030", code="1030", endpoint="/test")
        return "ok"

    new_tok = AuthToken(user_id="u1", sign_token="c" * 32, encry_token="d" * 32, refresh_token="rt2", raw={})
    with patch("pybyd.client.refresh_session_token", new=AsyncMock(return_value=new_tok)) as mock_ref:
        result = await client._call_with_reauth(flaky_call)

    assert result == "ok"
    assert calls == 2
    mock_ref.assert_awaited_once()
    assert client._session.sign_token == "c" * 32
    assert client._session.refresh_token == "rt2"


@pytest.mark.asyncio
async def test_call_with_reauth_relogins_on_1030_without_refresh_token() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    client._session = Session(user_id="u1", sign_token="a" * 32, encry_token="b" * 32, refresh_token="")
    client._transport = MagicMock()

    calls = 0

    async def flaky_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BydTokenRefreshError("1030", code="1030", endpoint="/test")
        return "ok"

    with patch.object(client, "ensure_session", new=AsyncMock()) as mock_ensure:
        result = await client._call_with_reauth(flaky_call)

    assert result == "ok"
    assert calls == 2
    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_with_reauth_retries_on_1003_without_invalidating_session() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", mqtt_enabled=False)
    client = BydClient(cfg)
    orig_session = Session(user_id="u1", sign_token="a" * 32, encry_token="b" * 32)
    client._session = orig_session

    calls = 0

    async def flaky_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BydServerTimeError("1003", code="1003", endpoint="/test")
        return "ok"

    result = await client._call_with_reauth(flaky_call)
    assert result == "ok"
    assert calls == 2
    assert client._session is orig_session


def test_parse_login_response_raises_server_time_error_on_1003() -> None:
    with pytest.raises(BydServerTimeError):
        parse_login_response({"code": "1003", "message": "clock skew"}, "secret")
