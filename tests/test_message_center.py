"""Tests for the message center (vehicle inbox) endpoint and models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import pybyd._api.message_center as mc
from pybyd._api.message_center import _UNREAD_ENDPOINT, fetch_notifications
from pybyd.exceptions import BydApiError
from pybyd.models.message_center import NotificationCategory, VehicleMsgItemInfo, VehicleMsgListResponse

# Shape observed on the EU host (bare list, ``pushId`` identifier).
_ITEMS: list[dict[str, Any]] = [
    {
        "content": "2026-10-02 20:40,ATTO 3 El coche comience a cargar.",
        "jumpUrl": "",
        "messageGroup": 1,
        "pushId": 211902416,
        "pushTime": 1790966443550,  # milliseconds
        "title": "Empezar carga",
        "titleId": 1,
    },
    {
        "messageId": "102",  # alternative keys still accepted
        "messageTitle": "Door open",
        "content": "--",
        "pushTime": "1790966443",  # seconds, as string
        "messageGroup": "",
    },
]
_EXPECTED_TIME = datetime.fromtimestamp(1790966443, tz=UTC)


def test_endpoint_path() -> None:
    assert _UNREAD_ENDPOINT == "/app/messageCenter/getUnReadMessageList"


def test_item_parses_camel_case_and_timestamps() -> None:
    first = VehicleMsgItemInfo.model_validate(_ITEMS[0])
    assert first.message_id == 211902416
    assert first.title == "Empezar carga"
    assert first.title_id == 1
    assert first.push_time == _EXPECTED_TIME
    assert first.message_group == NotificationCategory.VEHICLE
    assert first.jump_url is None  # "" sentinel stripped
    assert first.raw["pushId"] == 211902416

    second = VehicleMsgItemInfo.model_validate(_ITEMS[1])
    assert second.message_id == 102
    assert second.title == "Door open"
    assert second.content is None  # "--" sentinel stripped
    assert second.message_group is None
    assert second.push_time == _EXPECTED_TIME


def test_response_accepts_bare_list() -> None:
    resp = VehicleMsgListResponse.model_validate(_ITEMS)
    assert [m.message_id for m in resp.messages] == [211902416, 102]


@pytest.mark.parametrize("key", ["list", "messageList", "rows", "records"])
def test_response_unwraps_dict(key: str) -> None:
    resp = VehicleMsgListResponse.model_validate({key: _ITEMS})
    assert len(resp.messages) == 2


def test_response_empty() -> None:
    assert VehicleMsgListResponse.model_validate({}).messages == []
    assert VehicleMsgListResponse.model_validate([]).messages == []


@pytest.mark.asyncio
async def test_fetch_sends_message_group_without_vin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "build_inner_base", lambda *a, **k: dict(k))
    seen: dict[str, Any] = {}

    async def _fake_post(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _ITEMS

    monkeypatch.setattr(mc, "post_token_json", _fake_post)
    resp = await fetch_notifications(None, None, None)  # type: ignore[arg-type]
    assert seen["endpoint"] == _UNREAD_ENDPOINT
    assert seen["inner"] == {"messageGroup": "1"}
    assert "vin" not in seen["inner"]
    assert len(resp.messages) == 2

    await fetch_notifications(None, None, None, NotificationCategory.SYSTEM)  # type: ignore[arg-type]
    assert seen["inner"]["messageGroup"] == "2"


@pytest.mark.asyncio
async def test_fetch_handles_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "build_inner_base", lambda *a, **k: {})

    async def _fake_post(**kwargs: Any) -> Any:
        return "garbage"

    monkeypatch.setattr(mc, "post_token_json", _fake_post)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await fetch_notifications(None, None, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_propagates_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "build_inner_base", lambda *a, **k: {})

    async def _fake_post(**kwargs: Any) -> Any:
        raise BydApiError("boom", code="1007", endpoint=_UNREAD_ENDPOINT)

    monkeypatch.setattr(mc, "post_token_json", _fake_post)
    with pytest.raises(BydApiError):
        await fetch_notifications(None, None, None)  # type: ignore[arg-type]


# ── NotificationCategory ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "group"), [(NotificationCategory.VEHICLE, "1"), (NotificationCategory.SYSTEM, "2")]
)
async def test_category_maps_to_message_group(
    monkeypatch: pytest.MonkeyPatch, category: NotificationCategory, group: str
) -> None:
    monkeypatch.setattr(mc, "build_inner_base", lambda *a, **k: dict(k))
    seen: dict[str, Any] = {}

    async def _fake_post(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _ITEMS[:1]

    monkeypatch.setattr(mc, "post_token_json", _fake_post)
    resp = await fetch_notifications(None, None, None, category)  # type: ignore[arg-type]
    assert seen["inner"]["messageGroup"] == group
    assert len(resp.messages) == 1


@pytest.mark.asyncio
async def test_client_get_all_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    from pybyd.client import BydClient

    async def _fake_get(self: Any, category: NotificationCategory) -> list[VehicleMsgItemInfo]:
        return [VehicleMsgItemInfo.model_validate(_ITEMS[0])]

    class _Fake:
        get_notifications = _fake_get

    result = await BydClient.get_all_notifications(_Fake())  # type: ignore[arg-type]
    assert set(result) == {NotificationCategory.VEHICLE, NotificationCategory.SYSTEM}
