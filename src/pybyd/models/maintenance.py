"""Vehicle maintenance recommendation and service history models."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from pybyd.models._base import BydBaseModel, BydTimestamp


class MaintenanceRecord(BydBaseModel):
    """Single maintenance/service record from ``App.AppMaintenance.getInfoFromOthers``."""

    vin: str = ""
    order_no: str = ""
    mileage: int | None = None
    maintenance_date: BydTimestamp = None
    shop_name: str = ""


class MaintenanceHistory(BydBaseModel):
    """Service history list returned by ``App.AppMaintenance.getInfoFromOthers``."""

    vin: str = ""
    records: list[MaintenanceRecord] = Field(default_factory=list)


class MaintenanceRecommendation(BydBaseModel):
    """Next maintenance recommendation returned by ``App.AppMaintenance.getRecInfo``."""

    vin: str = ""
    is_book: str = ""
    next_maintenance_mileage: int | None = None
    next_maintenance_date: BydTimestamp = None
    next_maintenance_date_str: str = ""

    @field_validator("next_maintenance_mileage", mode="before")
    @classmethod
    def _empty_mileage_to_none(cls, value: Any) -> int | None:
        if value in (None, "", -1):
            return None
        return int(value)

    @property
    def booked(self) -> bool:
        """Whether a maintenance appointment is currently booked (``is_book == "1"``)."""
        return self.is_book == "1"
