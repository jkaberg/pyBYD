"""Realtime polling must not surface the asleep placeholder (issue #20)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import BydClient, BydConfig
from pybyd.exceptions import BydDataUnavailableError
from pybyd.models.realtime import VehicleRealtimeData
from pybyd.session import Session

# captured on a Seal (MEA): online, but every field zeroed
PLACEHOLDER = {
    "onlineState": 1,
    "time": 0,
    "elecPercent": 0,
    "enduranceMileage": 0,
    "leftFrontTirepressure": 0.0,
    "tirePressUnit": 3,
    "tempInCar": 0,
    "leftFrontDoorLock": 0,
}
READY = {**PLACEHOLDER, "time": 1763795217, "elecPercent": 83, "enduranceMileage": 410}


def _client() -> BydClient:
    client = BydClient(BydConfig(username="u@example.com", password="secret", mqtt_enabled=False))
    client._session = MagicMock(spec=Session)
    client._session.is_expired = False
    client._transport = MagicMock()
    return client


async def _poll(client: BydClient, responses: list[dict], *, require_ready: bool) -> VehicleRealtimeData:
    fetch_fn = AsyncMock(side_effect=[(r, "ser-1") for r in responses])
    with patch("pybyd.client.asyncio.sleep", new=AsyncMock()):
        return await client._trigger_and_poll(
            vin="TESTVIN",
            trigger_endpoint="/vehicleInfo/vehicle/vehicleRealTimeRequest",
            poll_endpoint="/vehicleInfo/vehicle/vehicleRealTimeResult",
            fetch_fn=fetch_fn,
            is_ready=VehicleRealtimeData.is_ready_raw,
            model_cls=VehicleRealtimeData,
            label="Realtime",
            poll_attempts=2,
            poll_interval=0.0,
            require_ready=require_ready,
        )


@pytest.mark.asyncio
async def test_placeholder_after_exhausted_polls_raises() -> None:
    with pytest.raises(BydDataUnavailableError) as exc:
        await _poll(_client(), [PLACEHOLDER] * 3, require_ready=True)
    assert exc.value.code == "not_ready"


@pytest.mark.asyncio
async def test_ready_payload_still_returned() -> None:
    model = await _poll(_client(), [PLACEHOLDER, PLACEHOLDER, READY], require_ready=True)
    assert model.elec_percent == 83


@pytest.mark.asyncio
async def test_without_require_ready_keeps_old_behaviour() -> None:
    model = await _poll(_client(), [PLACEHOLDER] * 3, require_ready=False)
    assert model.elec_percent == 0
