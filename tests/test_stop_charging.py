"""Tests for stop_charging and continue_charging.

Both go through dedicated endpoints and are polled via ``changeResult``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.models.command_gating import evaluate_command_gate
from pybyd.models.control import RemoteCommand
from pybyd.models.latest_config import VehicleCapabilities


def _caps_with(function_nos: list[str]) -> VehicleCapabilities:
    return VehicleCapabilities.model_validate(
        {
            "vin": "TEST" + "X" * 13,
            "source": "test_fixture",
            "functionNos": function_nos,
        }
    )


def test_stop_charge_enum_value() -> None:
    """Synthetic enum member carries the BYD commandType string."""
    assert RemoteCommand.STOP_CHARGE == "SMARTCHARGESTOP"


def test_stop_charge_gate_present_and_mirrors_start() -> None:
    """The stop_charge gate exists and gates on the same capability as start."""
    caps = _caps_with(["1012"])
    assert evaluate_command_gate(RemoteCommand.STOP_CHARGE, caps).supported is True
    assert evaluate_command_gate(RemoteCommand.START_CHARGE, caps).supported is True

    caps_no = _caps_with([])
    assert evaluate_command_gate(RemoteCommand.STOP_CHARGE, caps_no).supported is False


@pytest.mark.parametrize(
    ("method", "endpoint"),
    [
        ("stop_charging", "/control/smartCharge/stopChargeControl"),
        ("continue_charging", "/control/smartCharge/continueChargeControl"),
    ],
)
async def test_charge_control_posts_to_dedicated_endpoint(
    monkeypatch: pytest.MonkeyPatch, method: str, endpoint: str
) -> None:
    client = BydClient(BydConfig(username="test@example.com", password="unused"))
    monkeypatch.setattr(client, "get_vehicle_capabilities", AsyncMock(return_value=_caps_with(["1012"])))
    monkeypatch.setattr(client, "_vehicle_fun_learn_info_for", AsyncMock(return_value={"bookingCharge": 5}))
    monkeypatch.setattr(client, "ensure_session", AsyncMock(return_value=object()))
    monkeypatch.setattr(client, "_require_transport", MagicMock(return_value=object()))
    post = AsyncMock(return_value={"res": 2})
    monkeypatch.setattr("pybyd._api.smart_charging.post_token_json", post)

    result = await getattr(client, method)("TEST" + "X" * 13, mqtt_timeout=0, poll_attempts=1, poll_interval=0)

    assert result.res == 2
    assert post.await_args_list[0].kwargs["endpoint"] == endpoint
