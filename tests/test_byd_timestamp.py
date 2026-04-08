"""Tests for parse_byd_timestamp / CN date strings on Vehicle."""

from __future__ import annotations

from datetime import UTC, datetime

from pybyd.models._base import parse_byd_timestamp
from pybyd.models.vehicle import Vehicle


def test_parse_java_style_cst() -> None:
    dt = parse_byd_timestamp("Sat Nov 22 00:00:00 CST 2025")
    assert dt is not None
    assert dt.tzinfo == UTC
    # 2025-11-22 00:00 CST = 2025-11-21 16:00 UTC
    assert dt.year == 2025
    assert dt.month == 11
    assert dt.day == 21
    assert dt.hour == 16


def test_vehicle_auto_bought_time_cn_string() -> None:
    v = Vehicle.model_validate(
        {
            "vin": "LGXCF6CD0R0123456",
            "autoBoughtTime": "Sat Nov 22 00:00:00 CST 2025",
        }
    )
    assert v.auto_bought_time is not None
    assert v.auto_bought_time.tzinfo == UTC


def test_parse_epoch_ms_unchanged() -> None:
    dt = parse_byd_timestamp(1_700_000_000_000)
    assert dt is not None
    assert dt == datetime.fromtimestamp(1_700_000_000, tz=UTC)
