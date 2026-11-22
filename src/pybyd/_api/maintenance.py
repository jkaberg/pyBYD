"""Vehicle service history endpoint (``/maintenance?s=App.AppMaintenance.getInfoFromOthers``).

``App.AppMaintenance.getRecInfo`` (next recommended service) is left out on
purpose: it returns empty fields on every server tested and hangs ~60 s
before a 504 on the MEA cluster. See jkaberg/pyBYD#114.
"""

from __future__ import annotations

from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import BydApiError
from pybyd.models.maintenance import MaintenanceHistory, MaintenanceRecord
from pybyd.session import Session

MAINTENANCE_HISTORY_ENDPOINT = "/maintenance?s=App.AppMaintenance.getInfoFromOthers"

# owner email and workshop contact/location, kept out of ``raw`` so it
# doesn't end up in logs or diagnostics dumps
_PERSONAL_KEYS = frozenset({"customer_email", "shop_address", "shop_phone", "latitude", "longitude"})


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
    return data if isinstance(data, dict) else decoded


def _scrub(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _PERSONAL_KEYS}


async def fetch_maintenance_history(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> MaintenanceHistory:
    """Fetch past workshop visits for *vin*."""
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
    items = [_scrub(item) for item in raw_list if isinstance(item, dict)] if isinstance(raw_list, list) else []
    records = [MaintenanceRecord.model_validate({**item, "vin": vin, "raw": item}) for item in items]
    return MaintenanceHistory(vin=vin, records=records, raw={**data, "list": items})
