"""Tests for the AC current limit endpoint and model."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import pybyd._api.smart_charging as sc
from pybyd import AcCurrentLimit, BydClient, BydConfig
from pybyd._api.smart_charging import _GET_AC_LIMIT_ENDPOINT, fetch_ac_current_limit
from pybyd.models import EnergyType

_VIN = "LGXCE6CB0P0000001"
_LIVE_EU_PAYLOAD: dict[str, Any] = {
    "currentLimitList": ["6A", "8A", "10A", "16A", "MAX"],
    "currentEvCurrentLimit": "MAX",
}


def test_endpoint_path() -> None:
    assert _GET_AC_LIMIT_ENDPOINT == "/control/smartCharge/getAcCurrentLimit"


def test_model_parses_live_eu_payload() -> None:
    limit = AcCurrentLimit.model_validate({"vin": _VIN, **_LIVE_EU_PAYLOAD})
    assert limit.vin == _VIN
    assert limit.current_ev_current_limit == "MAX"
    assert limit.current_limit_list == ["6A", "8A", "10A", "16A", "MAX"]
    assert limit.raw["currentEvCurrentLimit"] == "MAX"


def test_model_coerces_defensively() -> None:
    limit = AcCurrentLimit.model_validate(
        {
            "vin": _VIN,
            "currentLimitList": [6, " 10A ", None, ""],
            "currentEvCurrentLimit": 16,
        }
    )
    assert limit.current_ev_current_limit == "16"
    assert limit.current_limit_list == ["6", "10A"]

    empty = AcCurrentLimit.model_validate({"vin": _VIN, "currentLimitList": None, "currentEvCurrentLimit": "--"})
    assert empty.current_ev_current_limit is None
    assert empty.current_limit_list == []


@pytest.mark.asyncio
async def test_fetch_ac_current_limit_sends_energy_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "build_inner_base", lambda *a, **k: dict(k))
    seen: dict[str, Any] = {}

    async def _fake_post(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _LIVE_EU_PAYLOAD

    monkeypatch.setattr(sc, "post_token_json", _fake_post)
    resp = await fetch_ac_current_limit(None, None, None, _VIN, energy_type=EnergyType.HYBRID)  # type: ignore[arg-type]
    assert seen["endpoint"] == _GET_AC_LIMIT_ENDPOINT
    assert seen["inner"] == {"vin": _VIN, "energyType": "2"}
    assert resp.current_ev_current_limit == "MAX"
    assert resp.current_limit_list == ["6A", "8A", "10A", "16A", "MAX"]


@pytest.mark.asyncio
async def test_client_get_ac_current_limit_resolves_energy_type() -> None:
    client = BydClient(BydConfig(username="user@example.invalid", password="unused"))
    with (
        patch.object(client, "_energy_type_for", AsyncMock(return_value=EnergyType.EV)) as energy_mock,
        patch.object(
            client,
            "_authed_call",
            AsyncMock(return_value=AcCurrentLimit.model_validate({"vin": _VIN, **_LIVE_EU_PAYLOAD})),
        ) as authed_mock,
    ):
        result = await client.get_ac_current_limit(_VIN)

    energy_mock.assert_awaited_once_with(_VIN)
    authed_mock.assert_awaited_once_with(sc.fetch_ac_current_limit, _VIN, energy_type=EnergyType.EV)
    assert result.current_ev_current_limit == "MAX"
