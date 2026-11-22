"""Tests for the vehicle service history endpoint and models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from pybyd._api.maintenance import MAINTENANCE_HISTORY_ENDPOINT, fetch_maintenance_history
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
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.endpoints: list[str] = []

    async def post_secure(self, endpoint: str, outer: dict[str, Any]) -> dict[str, Any]:
        self.endpoints.append(endpoint)
        body = json.dumps(self._payload)
        return {"code": "0", "respondData": aes_encrypt_hex(body, _session().content_key())}


def _record(ro_no: str, mileage: int, balance_ms: int) -> dict[str, Any]:
    # shape from a real BR-server record (jkaberg/pyBYD#114), values made up
    return {
        "id": 12345,
        "status": 1,
        "ro_no": ro_no,
        "dealer_code": "D001",
        "repair_type_code": "JSXM",
        "total_mileage": mileage,
        "last_balance_date": balance_ms,
        "last_balance_date_str": "2026-10-12 10:18:32",
        "next_maintenance_mileage": "",
        "next_maintenance_date": 0,
        "next_maintenance_date_str": "",
        "is_secstore": 10041002,
        "shop_name": "BYD Test",
        "shop_address": "Somewhere 1",
        "shop_phone": "+00 000",
        "latitude": "1.0",
        "longitude": "2.0",
        "customer_email": "owner@example.com",
    }


async def test_history_empty() -> None:
    transport = _FakeMaintenanceTransport({"ret": 200, "data": {"list": []}, "msg": "操作成功"})
    history = await fetch_maintenance_history(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert transport.endpoints == [MAINTENANCE_HISTORY_ENDPOINT]
    assert history.records == []
    assert history.latest is None


async def test_history_real_record_shape() -> None:
    transport = _FakeMaintenanceTransport(
        {
            "ret": 200,
            "data": {
                "list": [
                    _record("RO-OLD", 50210, 1760000000000),
                    _record("RO-NEW", 99678, 1791771512000),
                ]
            },
            "msg": "操作成功",
        }
    )
    history = await fetch_maintenance_history(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert len(history.records) == 2

    latest = history.latest
    assert latest is not None
    assert latest.vin == VIN
    assert latest.order_no == "RO-NEW"
    assert latest.mileage == 99678
    assert latest.service_date == datetime.fromtimestamp(1791771512, tz=UTC)
    assert latest.shop_name == "BYD Test"
    assert latest.repair_type_code == "JSXM"


async def test_history_raw_drops_personal_data() -> None:
    transport = _FakeMaintenanceTransport({"ret": 200, "data": {"list": [_record("RO-1", 1000, 1791771512000)]}})
    history = await fetch_maintenance_history(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    dumped = json.dumps([history.raw, history.records[0].raw])
    for key in ("customer_email", "shop_address", "shop_phone", "latitude", "longitude"):
        assert key not in dumped
    assert "owner@example.com" not in dumped


async def test_history_non_200_raises() -> None:
    transport = _FakeMaintenanceTransport({"ret": 500, "data": None, "msg": "Internal error"})
    with pytest.raises(BydApiError) as exc_info:
        await fetch_maintenance_history(_config(), _session(), transport, VIN)  # type: ignore[arg-type]
    assert exc_info.value.code == "500"
    assert exc_info.value.endpoint == MAINTENANCE_HISTORY_ENDPOINT
