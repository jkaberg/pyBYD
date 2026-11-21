"""Tests for OTA firmware version and night auto-upgrade models and endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pybyd import BydClient, BydConfig, BydControlPasswordError, OtaStatus, OtaUpgradeInfo
from pybyd._api.ota import fetch_ota_version, normalize_ota_upgrade_time, sync_auto_upgrade
from pybyd.models.vehicle import Vehicle
from pybyd.session import Session

_CAPTURED_IDLE = {
    "padTimeZone": "Europe/Madrid",
    "updateVersion": "",
    "currentUpdateTime": 1795276224815,
    "autoUpgradeTime": "3:00",
    "autoUpgradeSwitch": "1",
    "functionOptimization": "1. Optimize UI.",
    "acknowledgements": "",
    "currentVersion": "V1.3.0",
    "upgradeStatus": 0,
    "estimateUpgradeTime": 7200,
    "bookingTimeStamp": 0,
    "functionDescription": "",
    "functionAddition": "1. Add AC charging current limit feature.",
}


def test_parses_captured_idle_payload() -> None:
    info = OtaUpgradeInfo.model_validate({"vin": "TESTVIN", **_CAPTURED_IDLE})
    assert info.vin == "TESTVIN"
    assert info.current_version == "V1.3.0"
    assert info.update_version == ""
    assert info.upgrade_status == OtaStatus.IDLE
    assert info.auto_upgrade_enabled is True
    assert info.has_update is False
    assert info.auto_upgrade_time == "3:00"
    assert info.estimate_upgrade_time == 7200
    assert info.booking_time_stamp is None
    assert info.current_update_time is not None
    assert info.pad_time_zone == "Europe/Madrid"
    assert info.upgrade_result is None


def test_parses_available_update_and_nested_result() -> None:
    info = OtaUpgradeInfo.model_validate(
        {
            "vin": "TESTVIN",
            **_CAPTURED_IDLE,
            "updateVersion": "V1.4.0",
            "upgradeStatus": 1,
            "bookingTimeStamp": 1795280000000,
            "upgradeResult": {"status": "0", "statusCode": "200", "message": "ok"},
        }
    )
    assert info.upgrade_status == OtaStatus.UPGRADE_AVAILABLE
    assert info.has_update is True
    assert info.booking_time_stamp is not None
    assert info.upgrade_result is not None
    assert info.upgrade_result.status_code == "200"
    assert info.upgrade_result.message == "ok"


def test_unknown_ota_status_falls_back_to_unknown() -> None:
    info = OtaUpgradeInfo.model_validate({"vin": "TESTVIN", "upgradeStatus": 999})
    assert info.upgrade_status == OtaStatus.UNKNOWN


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2:00", "2:00"),
        ("02:00", "2:00"),
        ("00:05", "0:05"),
        ("23:59", "23:59"),
    ],
)
def test_normalize_ota_upgrade_time_valid(raw: str, expected: str) -> None:
    assert normalize_ota_upgrade_time(raw) == expected


@pytest.mark.parametrize("bad", ["", "24:00", "12:60", "2am", "1230", "1:2"])
def test_normalize_ota_upgrade_time_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="auto_upgrade_time"):
        normalize_ota_upgrade_time(bad)


def test_vehicle_ota_upgrade_supported() -> None:
    supported = Vehicle.model_validate({"vin": "TESTVIN", "vehicleFunLearnInfo": {"otaUpgrade": 1}})
    unsupported = Vehicle.model_validate({"vin": "TESTVIN", "vehicleFunLearnInfo": {"otaUpgrade": 0}})
    missing = Vehicle.model_validate({"vin": "TESTVIN"})
    assert supported.ota_upgrade_supported is True
    assert unsupported.ota_upgrade_supported is False
    assert missing.ota_upgrade_supported is False


@pytest.mark.asyncio
async def test_fetch_ota_version_sends_language() -> None:
    cfg = BydConfig(username="u@example.com", password="secret", language="es")
    session = MagicMock(spec=Session)
    transport = MagicMock()

    with patch("pybyd._api.ota.post_token_json", new=AsyncMock(return_value=_CAPTURED_IDLE)) as mock_post:
        info = await fetch_ota_version(cfg, session, transport, "TESTVIN")

    assert info.current_version == "V1.3.0"
    assert info.upgrade_status == OtaStatus.IDLE
    call_kwargs = mock_post.await_args.kwargs
    assert call_kwargs["endpoint"] == "/control/otaUpgrade/getOtaVersion"
    assert call_kwargs["inner"]["vin"] == "TESTVIN"
    assert call_kwargs["inner"]["language"] == "es"


@pytest.mark.asyncio
async def test_sync_auto_upgrade_sends_expected_fields() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    session = MagicMock(spec=Session)
    transport = MagicMock()

    with patch("pybyd._api.ota.post_token_json", new=AsyncMock(return_value="")) as mock_post:
        ack = await sync_auto_upgrade(
            cfg,
            session,
            transport,
            "TESTVIN",
            enabled=True,
            auto_upgrade_time="03:00",
            command_pwd="0123456789abcdef0123456789abcdef",
        )

    assert ack.vin == "TESTVIN"
    call_kwargs = mock_post.await_args.kwargs
    assert call_kwargs["endpoint"] == "/control/otaUpgrade/syncAutoUpgrade"
    assert call_kwargs["inner"]["vin"] == "TESTVIN"
    assert call_kwargs["inner"]["autoUpgradeSwitch"] == "1"
    assert call_kwargs["inner"]["autoUpgradeTime"] == "3:00"
    assert call_kwargs["inner"]["commandPwd"] == "0123456789abcdef0123456789abcdef"


@pytest.mark.asyncio
async def test_client_set_ota_auto_upgrade_requires_pin() -> None:
    cfg = BydConfig(username="u@example.com", password="secret")
    client = BydClient(cfg)
    with pytest.raises(BydControlPasswordError):
        await client.set_ota_auto_upgrade("TESTVIN", enabled=True)
