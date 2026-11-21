"""OTA firmware status and version models."""

from __future__ import annotations

from pybyd.models._base import BydBaseModel, BydEnum, BydTimestamp


class OtaStatus(BydEnum):
    """OTA firmware upgrade status reported by ``/control/otaUpgrade/getOtaVersion``."""

    UNKNOWN = -1
    IDLE = 0
    UPGRADE_AVAILABLE = 1
    UPGRADE_BOOKED = 2
    UPGRADING = 3
    UPGRADE_READY = 4
    BOOKING = 5
    CANCEL_BOOKING = 6
    NIGHT_SYNC = 100


class OtaUpgradeResult(BydBaseModel):
    """Nested upgrade result block inside :class:`OtaUpgradeInfo`."""

    status: str = ""
    status_code: str = ""
    message: str = ""


class OtaUpgradeInfo(BydBaseModel):
    """Vehicle OTA firmware version, release notes, and night auto-upgrade settings."""

    vin: str = ""
    current_version: str = ""
    update_version: str = ""
    upgrade_status: OtaStatus = OtaStatus.UNKNOWN
    auto_upgrade_switch: str = ""
    auto_upgrade_time: str = ""
    booking_time_stamp: BydTimestamp = None
    current_update_time: BydTimestamp = None
    estimate_upgrade_time: int | None = None
    pad_time_zone: str = ""
    function_addition: str = ""
    function_optimization: str = ""
    function_description: str = ""
    acknowledgements: str = ""
    upgrade_result: OtaUpgradeResult | None = None

    @property
    def auto_upgrade_enabled(self) -> bool:
        """Whether automatic night OTA upgrade is enabled (``autoUpgradeSwitch == "1"``)."""
        return self.auto_upgrade_switch == "1"

    @property
    def has_update(self) -> bool:
        """Whether a newer OTA version string is advertised."""
        return bool(self.update_version.strip())
