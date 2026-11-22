"""Tests for vehicle maintenance endpoints and models (BR-25)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pybyd._api.maintenance import (
    MAINTENANCE_HISTORY_ENDPOINT,
    MAINTENANCE_REC_ENDPOINT,
    fetch_maintenance_history,
    fetch_maintenance_recommendation,
)
from pybyd._crypto.aes import aes_encrypt_hex
from pybyd.config import BydConfig
from pybyd.exceptions import BydApiError
from pybyd.session import Session

VIN = "TESTVIN0000000001"


def _config() -> BydConfig:
    return BydConfig(username="user@example.com", password="pw")


def _session() -> Session:
    return Session(user_id="42", sign_token="sign", encry_token="encry")


class _FakeMaintenanceTransport:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads
        self.endpoints: list[str] = []

    async def post_secure(self, endpoint: str, outer: dict[str, Any]) -> dict[str, Any]:
        self.endpoints.append(endpoint)
        session = _session()
        body = json.dumps(self._payloads[endpoint])
        return {"code": "0", "respondData": aes_encrypt_hex(body, session.content_key())}


async def test_maintenance_recommendation_empty_eu_payload() -> None:
    transport = _FakeMaintenanceTransport(
        {
            MAINTENANCE_REC_ENDPOINT: {
                "ret": 200,
                "data": {
                    "is_book": "0",
                    "next_maintenance_mileage": "",
                    "next_maintenance_date": 0,
                    "next_maintenance_date_str": "",
                },
                "msg": "操作成功",
                "logId": "ea235ab4-3b83-44de-a053-51838814d856",
            }
        }
    )
    rec = await fetch_maintenance_recommendation(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert transport.endpoints == [MAINTENANCE_REC_ENDPOINT]
    assert rec.booked is False
    assert rec.next_maintenance_mileage is None
    assert rec.next_maintenance_date is None
    assert rec.next_maintenance_date_str == ""


async def test_maintenance_recommendation_populated_payload() -> None:
    transport = _FakeMaintenanceTransport(
        {
            MAINTENANCE_REC_ENDPOINT: {
                "ret": 200,
                "data": {
                    "is_book": "1",
                    "next_maintenance_mileage": "20000",
                    "next_maintenance_date": 1795300000,
                    "next_maintenance_date_str": "2026-11-21",
                },
                "msg": "操作成功",
            }
        }
    )
    rec = await fetch_maintenance_recommendation(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert rec.booked is True
    assert rec.next_maintenance_mileage == 20000
    assert rec.next_maintenance_date is not None
    assert rec.next_maintenance_date_str == "2026-11-21"


async def test_maintenance_history_empty_and_populated() -> None:
    transport = _FakeMaintenanceTransport(
        {
            MAINTENANCE_HISTORY_ENDPOINT: {
                "ret": 200,
                "data": {"list": []},
                "msg": "操作成功",
            }
        }
    )
    empty = await fetch_maintenance_history(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert empty.records == []

    transport_pop = _FakeMaintenanceTransport(
        {
            MAINTENANCE_HISTORY_ENDPOINT: {
                "ret": 200,
                "data": {
                    "list": [
                        {
                            "orderNo": "ORD-1",
                            "mileage": 15000,
                            "shopName": "BYD Palma",
                        }
                    ]
                },
                "msg": "操作成功",
            }
        }
    )
    pop = await fetch_maintenance_history(_config(), _session(), transport_pop, VIN)  # type: ignore[arg-type]
    assert len(pop.records) == 1
    assert pop.records[0].order_no == "ORD-1"
    assert pop.records[0].mileage == 15000
    assert pop.records[0].shop_name == "BYD Palma"


async def test_maintenance_non_200_ret_raises() -> None:
    transport = _FakeMaintenanceTransport(
        {
            MAINTENANCE_REC_ENDPOINT: {
                "ret": 400,
                "data": {},
                "msg": "invalid",
            }
        }
    )
    with pytest.raises(BydApiError) as exc_info:
        await fetch_maintenance_recommendation(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert exc_info.value.code == "400"
