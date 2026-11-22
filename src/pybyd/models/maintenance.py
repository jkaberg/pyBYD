"""Vehicle service history models."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from pybyd.models._base import BydBaseModel, BydTimestamp


class MaintenanceRecord(BydBaseModel):
    """Single workshop visit from ``App.AppMaintenance.getInfoFromOthers``."""

    vin: str = ""
    order_no: str = Field(default="", validation_alias="ro_no")
    mileage: int | None = Field(default=None, validation_alias="total_mileage")
    service_date: BydTimestamp = Field(default=None, validation_alias="last_balance_date")
    shop_name: str = ""
    dealer_code: str = ""
    repair_type_code: str = ""


class MaintenanceHistory(BydBaseModel):
    """Service history list returned by ``App.AppMaintenance.getInfoFromOthers``."""

    vin: str = ""
    records: list[MaintenanceRecord] = Field(default_factory=list)

    @property
    def latest(self) -> MaintenanceRecord | None:
        """Most recent record by service date, ``None`` when there are none."""
        if not self.records:
            return None
        oldest = datetime.min.replace(tzinfo=UTC)
        return max(self.records, key=lambda r: r.service_date or oldest)
