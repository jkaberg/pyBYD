"""Vehicle list endpoint.

Endpoint:
  - /app/account/getAllListByUserId
"""

from __future__ import annotations

from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.vehicle import EmpowerDetail, Vehicle
from pybyd.session import Session

_ENDPOINT = "/app/account/getAllListByUserId"
_EMPOWER_DETAIL_ENDPOINT = "/vehicle/empower/query/empowerDetailByVin"


async def fetch_vehicle_list(
    config: BydConfig,
    session: Session,
    transport: Transport,
) -> list[Vehicle]:
    """Fetch all vehicles associated with the authenticated user."""
    inner = build_inner_base(config)
    decoded = await post_token_json(
        endpoint=_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
    )
    items = decoded if isinstance(decoded, list) else []
    return [Vehicle.model_validate(item) for item in items]


async def fetch_empower_detail_by_vin(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    empower_id: int,
) -> EmpowerDetail:
    """Fetch granular guest authorization details for a shared vehicle."""
    inner = build_inner_base(config, vin=vin)
    inner["empowerId"] = str(empower_id)
    decoded = await post_token_json(
        endpoint=_EMPOWER_DETAIL_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    payload: dict[str, Any] = decoded if isinstance(decoded, dict) else {}
    raw_data = payload.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    merged = {
        "vin": vin,
        **data,
        "empowerControlPwdStatus": bool(payload.get("empowerControlPwdStatus", False)),
        "raw": payload,
    }
    return EmpowerDetail.model_validate(merged)
