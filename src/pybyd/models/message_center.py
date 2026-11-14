"""Message center (account inbox) models.

``/app/messageCenter/getUnReadMessageList`` returns a bare list of
messages (charging started/stopped, status alerts, ...), e.g.::

    [{"pushId": 211902416, "pushTime": 1790966443550, "messageGroup": 1,
      "titleId": 1, "title": "Empezar carga",
      "content": "2026-10-02 20:40,BYD ATTO 3 EVO El coche comience a cargar.",
      "jumpUrl": ""}]

The EU host identifies messages by ``pushId``; ``messageId`` is accepted
as a fallback.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from pybyd.models._base import BydBaseModel, BydTimestamp

#: Keys under which the message list may be wrapped in a dict response.
_LIST_KEYS: tuple[str, ...] = ("list", "messageList", "rows", "records")


class NotificationCategory(enum.IntEnum):
    """Inbox category, the value is the ``messageGroup`` sent to the API."""

    VEHICLE = 1
    SYSTEM = 2


class VehicleMsgItemInfo(BydBaseModel):
    """A single message from the account inbox."""

    message_id: int | str = Field(validation_alias=AliasChoices("pushId", "messageId"))
    title: str | None = Field(default=None, validation_alias=AliasChoices("title", "messageTitle"))
    title_id: int | None = None
    content: str | None = Field(default=None, validation_alias=AliasChoices("content", "messageContent"))
    push_time: BydTimestamp = None
    message_group: int | None = None
    jump_url: str | None = None

    @field_validator("message_id", mode="before")
    @classmethod
    def _normalise_id(cls, value: Any) -> Any:
        if isinstance(value, str) and value.isdecimal():
            return int(value)
        return value


class VehicleMsgListResponse(BydBaseModel):
    """Decoded response of ``/app/messageCenter/getUnReadMessageList``."""

    messages: list[VehicleMsgItemInfo] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _unwrap_list(cls, values: Any) -> Any:
        """Accept a bare list (observed) or a dict wrapping the list."""
        if isinstance(values, list):
            return {"messages": values, "raw": {"list": values}}
        if isinstance(values, dict) and "messages" not in values:
            for key in _LIST_KEYS:
                items = values.get(key)
                if isinstance(items, list):
                    return {**values, "messages": items}
        return values
