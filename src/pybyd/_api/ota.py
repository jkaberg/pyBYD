"""OTA firmware version and night auto-upgrade endpoints.

Endpoints:
  - ``/control/otaUpgrade/getOtaVersion``     (read current/available OTA version and settings)
  - ``/control/otaUpgrade/syncAutoUpgrade``   (enable/disable night auto-upgrade and time)
"""

from __future__ import annotations

import re

from pybyd._api._common import ENDPOINT_NOT_SUPPORTED_CODES, build_inner_base, post_token_json
from pybyd._transport import Transport
from pybyd.config import BydConfig
from pybyd.models.control import CommandAck
from pybyd.models.ota import OtaUpgradeInfo
from pybyd.session import Session

_GET_OTA_VERSION_ENDPOINT = "/control/otaUpgrade/getOtaVersion"
_SYNC_AUTO_UPGRADE_ENDPOINT = "/control/otaUpgrade/syncAutoUpgrade"

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def normalize_ota_upgrade_time(value: str) -> str:
    """Validate and normalize an OTA night-upgrade time to ``H:mm`` (e.g. ``"2:00"``)."""
    match = _TIME_RE.match(value.strip())
    if match is None:
        raise ValueError(f"Invalid auto_upgrade_time {value!r}; expected H:mm (e.g. '2:00')")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid auto_upgrade_time {value!r}; hour must be 0..23 and minute 0..59")
    return f"{hour}:{minute:02d}"


async def fetch_ota_version(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
) -> OtaUpgradeInfo:
    """Fetch OTA firmware version, release notes, and night auto-upgrade settings."""
    inner = build_inner_base(config, vin=vin)
    # The cloud rejects getOtaVersion with code=1010 (WRONG_FORMAT) when language is omitted.
    inner["language"] = config.language
    decoded = await post_token_json(
        endpoint=_GET_OTA_VERSION_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return OtaUpgradeInfo.model_validate({"vin": vin, **raw, "raw": raw})


async def sync_auto_upgrade(
    config: BydConfig,
    session: Session,
    transport: Transport,
    vin: str,
    *,
    enabled: bool,
    auto_upgrade_time: str = "2:00",
    command_pwd: str,
) -> CommandAck:
    """Update night automatic OTA upgrade switch and scheduled hour."""
    normalized_time = normalize_ota_upgrade_time(auto_upgrade_time)
    inner = build_inner_base(config, vin=vin)
    inner["autoUpgradeSwitch"] = "1" if enabled else "0"
    inner["autoUpgradeTime"] = normalized_time
    inner["commandPwd"] = command_pwd
    decoded = await post_token_json(
        endpoint=_SYNC_AUTO_UPGRADE_ENDPOINT,
        config=config,
        session=session,
        transport=transport,
        inner=inner,
        vin=vin,
        not_supported_codes=ENDPOINT_NOT_SUPPORTED_CODES,
    )
    raw = decoded if isinstance(decoded, dict) else {}
    return CommandAck.model_validate({"vin": vin, **raw, "raw": raw})
