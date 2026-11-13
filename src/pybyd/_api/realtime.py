"""Vehicle realtime data endpoints.

Endpoints:
  - /vehicleInfo/vehicle/vehicleRealTimeRequest  (trigger)
  - /vehicleInfo/vehicle/vehicleRealTimeResult    (poll)
"""

from __future__ import annotations

import time
from typing import Any

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_vehicle_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.vehicle import EnergyType
from pybyd.session import Session


async def fetch_realtime_endpoint(
    endpoint: str,
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    request_serial: str | None = None,
    *,
    energy_type: EnergyType = EnergyType.EV,
) -> tuple[dict[str, Any], str | None]:
    """Fetch a single realtime endpoint, returning (vehicle_info_dict, next_serial).

    ``energy_type`` mirrors the per-vehicle ``energyType`` returned by the
    vehicle-list endpoint. Hybrid vehicles (``EnergyType.HYBRID``) return
    combined EV+ICE range/consumption fields; sending ``EnergyType.EV`` for
    a hybrid causes the cloud to zero-out the ICE-side fields.
    """
    now_ms = int(time.time() * 1000)
    inner = build_inner_base(config, now_ms=now_ms, vin=vin, request_serial=request_serial)
    inner["energyType"] = str(int(energy_type))
    inner["tboxVersion"] = config.tbox_version

    decoded = await post_vehicle_json(
        endpoint=endpoint,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        now_ms=now_ms,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    vehicle_info: dict[str, Any] = decoded if isinstance(decoded, dict) else {}
    next_serial = vehicle_info.get("requestSerial") or request_serial

    return vehicle_info, next_serial
