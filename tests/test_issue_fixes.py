"""Steering wheel heat mapping, debug-log redaction and login identifierType."""

from __future__ import annotations

import json
import logging

import pytest

from pybyd._api._common import decode_respond_data
from pybyd._api.login import build_login_request
from pybyd._crypto.aes import aes_encrypt_hex
from pybyd._redact import redact_for_log
from pybyd.config import BydConfig
from pybyd.models.hvac import HvacStatus
from pybyd.models.realtime import StearingWheelHeat, VehicleRealtimeData


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-1, StearingWheelHeat.ON),
        (1, StearingWheelHeat.OFF),
        (0, StearingWheelHeat.NO_DATA),
        (3, StearingWheelHeat.NO_DATA),
    ],
)
def test_steering_wheel_heat_enum(raw: int, expected: StearingWheelHeat) -> None:
    assert StearingWheelHeat(raw) is expected


@pytest.mark.parametrize(("raw", "expected"), [(-1, True), (1, False), (0, None)])
def test_is_steering_wheel_heating(raw: int, expected: bool | None) -> None:
    assert VehicleRealtimeData.model_validate({"steeringWheelHeatState": raw}).is_steering_wheel_heating is expected
    assert HvacStatus.model_validate({"steeringWheelHeatState": raw}).is_steering_wheel_heating is expected


def test_redact_masks_login_tokens() -> None:
    inner = {"token": {"userId": "1", "signToken": "s", "encryToken": "e"}, "other": 1}
    assert redact_for_log(inner) == {"token": "<redacted>", "other": 1}


def test_redact_masks_encrypted_blob_but_walks_decrypted_payload() -> None:
    assert redact_for_log({"respondData": "ABCDEF"}) == {"respondData": "<redacted>"}
    payload = {"data": {"respondData": {"elecPercent": 80, "signToken": "x"}}}
    assert redact_for_log(payload) == {"data": {"respondData": {"elecPercent": 80, "signToken": "<redacted>"}}}


def test_decoded_http_debug_log_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    key = "00" * 16
    body = json.dumps({"encryToken": "secret-value", "elecPercent": 80})
    response = {"respondData": aes_encrypt_hex(body, key)}
    with caplog.at_level(logging.DEBUG, logger="pybyd._api._common"):
        decoded = decode_respond_data(endpoint="/test", response=response, content_key=key)
    assert decoded["encryToken"] == "secret-value"
    assert "secret-value" not in caplog.text
    assert "elecPercent" in caplog.text


@pytest.mark.parametrize(("kwargs", "expected"), [({}, "0"), ({"identifier_type": "1"}, "1")])
def test_login_identifier_type(kwargs: dict[str, str], expected: str) -> None:
    config = BydConfig(username="user@example.com", password="pw", **kwargs)
    assert build_login_request(config, 0)["identifierType"] == expected


def test_identifier_type_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BYD_USERNAME", "u")
    monkeypatch.setenv("BYD_PASSWORD", "p")
    monkeypatch.setenv("BYD_IDENTIFIER_TYPE", "1")
    config = BydConfig.from_env()
    assert config.identifier_type == "1"
