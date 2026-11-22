"""Vehicle maintenance endpoints (``/maintenance?s=App.AppMaintenance.*``).

Endpoints:
  - ``/maintenance?s=App.AppMaintenance.getInfoFromOthers`` (service history list)
  - ``/maintenance?s=App.AppMaintenance.getRecInfo``        (next recommended service)
"""

from __future__ import annotations

from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import BydApiError
from pybyd.models.maintenance import (
    MaintenanceHistory,
    MaintenanceRecommendation,
    MaintenanceRecord,
)
from pybyd.session import Session

MAINTENANCE_HISTORY_ENDPOINT = "/maintenance?s=App.AppMaintenance.getInfoFromOthers"
MAINTENANCE_REC_ENDPOINT = "/maintenance?s=App.AppMaintenance.getRecInfo"


def _unwrap_maintenance_data(decoded: Any, *, endpoint: str) -> dict[str, Any]:
    """Extract ``data`` from the ``{"ret": 200, "data": ..., "msg": ...}`` envelope."""
    if not isinstance(decoded, dict):
        return {}
    if "ret" in decoded and decoded["ret"] != 200:
        raise BydApiError(
            str(decoded.get("msg") or f"Maintenance call failed ({endpoint})"),
            code=str(decoded["ret"]),
            endpoint=endpoint,
        )
    data = decoded.get("data")
    if isinstance(data, dict):
        return data
    return decoded


async def fetch_maintenance_history(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> MaintenanceHistory:
    """Fetch past maintenance records for *vin*."""
    inner = build_inner_base(config, vin=vin)
    decoded = await post_token_json(
        endpoint=MAINTENANCE_HISTORY_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    data = _unwrap_maintenance_data(decoded, endpoint=MAINTENANCE_HISTORY_ENDPOINT)
    raw_list = data.get("list")
    records = (
        [
            MaintenanceRecord.model_validate({"vin": vin, **item, "raw": item})
            for item in raw_list
            if isinstance(item, dict)
        ]
        if isinstance(raw_list, list)
        else []
    )
    return MaintenanceHistory(vin=vin, records=records, raw=data)


async def fetch_maintenance_recommendation(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> MaintenanceRecommendation:
    """Fetch next recommended maintenance date/mileage for *vin*."""
    inner = build_inner_base(config, vin=vin)
    decoded = await post_token_json(
        endpoint=MAINTENANCE_REC_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    data = _unwrap_maintenance_data(decoded, endpoint=MAINTENANCE_REC_ENDPOINT)
    return MaintenanceRecommendation.model_validate({"vin": vin, **data, "raw": data})
