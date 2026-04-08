"""Vehicle list endpoint.

Endpoints:
  - /app/account/getAllListByUserId (overseas)
  - /app/auth/getAllListByUserId (CN)
"""

from __future__ import annotations

from typing import Any

from pybyd._api._common import build_inner_base, post_token_json
from pybyd._api.cn_envelope import build_cn_vehicle_list_inner
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.vehicle import Vehicle
from pybyd.session import Session

_ENDPOINT_OVERSEAS = "/app/account/getAllListByUserId"
_ENDPOINT_CN = "/app/auth/getAllListByUserId"


def _vehicle_list_items(decoded: Any) -> list[Any]:
    if isinstance(decoded, list):
        return decoded
    if isinstance(decoded, dict):
        nested = decoded.get("diLinkAutoInfoList")
        if isinstance(nested, list):
            return nested
    return []


async def fetch_vehicle_list(
    config: BydConfig,
    session: Session,
    transport: Transport,
) -> list[Vehicle]:
    """Fetch all vehicles associated with the authenticated user."""
    if config.is_china_region:
        inner = build_cn_vehicle_list_inner(config)
        endpoint = _ENDPOINT_CN
    else:
        inner = build_inner_base(config)
        endpoint = _ENDPOINT_OVERSEAS
    decoded = await post_token_json(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
    )
    items = _vehicle_list_items(decoded)
    return [Vehicle.model_validate(item) for item in items if isinstance(item, dict)]
