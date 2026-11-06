"""Message center (vehicle inbox) endpoints.

Endpoints:
  - /app/messageCenter/getUnReadMessageList  (inbox messages by group)

The verified EU request is account-scoped and sends ``messageGroup`` without
VIN or pagination fields.
"""

from __future__ import annotations

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.message_center import (
    CATEGORY_MESSAGE_GROUPS,
    MessageGroup,
    NotificationCategory,
    VehicleMsgListResponse,
)
from pybyd.session import Session

_UNREAD_ENDPOINT = "/app/messageCenter/getUnReadMessageList"


async def fetch_unread_messages(
    config: BydConfig,
    session: Session,
    transport: Transport,
    message_group: int = MessageGroup.VEHICLE,
) -> VehicleMsgListResponse:
    """Fetch the inbox messages of the given group for the logged-in account."""
    inner = build_inner_base(config)
    inner["messageGroup"] = str(int(message_group))
    decoded = await post_token_json(
        endpoint=_UNREAD_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    return VehicleMsgListResponse.model_validate(decoded)


async def fetch_notifications(
    config: BydConfig,
    session: Session,
    transport: Transport,
    category: NotificationCategory = NotificationCategory.VEHICLE,
) -> VehicleMsgListResponse:
    """Fetch the unread inbox messages of an owner-account category."""
    group = CATEGORY_MESSAGE_GROUPS[NotificationCategory(category)]
    return await fetch_unread_messages(config, session, transport, group)
