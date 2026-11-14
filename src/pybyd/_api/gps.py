"""GPS info endpoints.

Endpoints:
  - /control/getGpsInfo        (trigger)
  - /control/getGpsInfoResult  (poll)
"""

from __future__ import annotations

from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_vehicle_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.exceptions import BydDataUnavailableError
from pybyd.session import Session

#: API error codes indicating GPS data is temporarily unavailable
#: (e.g. vehicle has no satellite fix while parked in a garage).
GPS_DATA_UNAVAILABLE_CODES: frozenset[str] = frozenset({"6051"})


def is_gps_info_ready(gps_info: dict[str, Any]) -> bool:
    """Check if GPS data has meaningful content."""
    return bool(gps_info) and set(gps_info.keys()) != {"requestSerial"}


async def fetch_gps_endpoint(
    endpoint: str,
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    request_serial: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Fetch a single GPS endpoint, returning (gps_info_dict, next_serial)."""
    inner = build_inner_base(config, vin=vin, request_serial=request_serial)
    decoded = await post_vehicle_json(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
        extra_code_map={GPS_DATA_UNAVAILABLE_CODES: BydDataUnavailableError},
    )
    if not isinstance(decoded, dict):
        return {}, request_serial
    next_serial = decoded.get("requestSerial") if isinstance(decoded.get("requestSerial"), str) else request_serial
    return decoded, next_serial
