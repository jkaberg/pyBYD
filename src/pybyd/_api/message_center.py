"""Message center (account inbox) endpoints.

Endpoints:
  - /app/messageCenter/getUnReadMessageList  (unread messages by group)

The request is account-scoped: it sends ``messageGroup`` without VIN or
pagination fields.
"""

from __future__ import annotations

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.message_center import NotificationCategory, VehicleMsgListResponse
from pybyd.session import Session

_UNREAD_ENDPOINT = "/app/messageCenter/getUnReadMessageList"


async def fetch_notifications(
    config: BydConfig,
    session: Session,
    transport: Transport,
    category: NotificationCategory = NotificationCategory.VEHICLE,
) -> VehicleMsgListResponse:
    """Fetch the unread inbox messages of one category for the logged-in account."""
    inner = build_inner_base(config)
    inner["messageGroup"] = str(int(NotificationCategory(category)))
    decoded = await post_token_json(
        endpoint=_UNREAD_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    return VehicleMsgListResponse.model_validate(decoded)
