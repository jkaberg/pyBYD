"""Charging status model."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from pybyd.models._base import BydBaseModel, BydTimestamp, is_negative


class ChargingStatus(BydBaseModel):
    """Smart charging status for a vehicle."""

    # BYD sends the same value under different keys depending on the endpoint.
    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "elecPercent": "soc",
        "time": "updateTime",
    }

    _SENTINEL_RULES: ClassVar[dict[str, Callable[..., bool]]] = {
        "full_hour": is_negative,
        "full_minute": is_negative,
    }

    vin: str = ""
    soc: int | None = None
    """State of charge (0-100 percent)."""
    charging_state: int | None = None
    connect_state: int | None = None
    wait_status: int | None = None
    full_hour: int | None = None
    full_minute: int | None = None
    charge_power: float | None = None
    """Charging power while charging, in :attr:`charge_power_unit`.
    ``None`` when not charging."""
    charge_power_unit: str | None = None
    """Unit for :attr:`charge_power` (``"kW"`` observed)."""
    update_time: BydTimestamp = None
    """Last data update timestamp (parsed to UTC datetime)."""

    @property
    def is_connected(self) -> bool:
        return self.connect_state is not None and self.connect_state != 0

    @property
    def is_charging(self) -> bool:
        return self.charging_state == 1

    @property
    def time_to_full_available(self) -> bool:
        return self.full_hour is not None and self.full_minute is not None

    @property
    def time_to_full_minutes(self) -> int | None:
        """Total estimated minutes until fully charged.

        Combines ``full_hour`` and ``full_minute`` into a single value.
        Returns ``None`` when either component is unavailable.
        """
        if self.full_hour is None or self.full_minute is None:
            return None
        return self.full_hour * 60 + self.full_minute
