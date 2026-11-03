"""Unavailable realtime statuses and their original diagnostic values."""

import pytest

from pybyd.models.realtime import VehicleRealtimeData

FIELDS = (("epb", "epb"), ("powerBatteryConnection", "power_battery_connection"))


@pytest.mark.parametrize(("raw_key", "field_name"), FIELDS)
@pytest.mark.parametrize("value", [-1, -2, "-1", "-2"])
def test_negative_status_is_unavailable(raw_key: str, field_name: str, value: int | str) -> None:
    payload = {raw_key: value}
    original = dict(payload)

    status = VehicleRealtimeData.model_validate(payload)

    assert getattr(status, field_name) is None
    assert status.raw == original
    assert payload == original


@pytest.mark.parametrize(("raw_key", "field_name"), FIELDS)
@pytest.mark.parametrize(("value", "expected"), [(0, 0), (1, 1), (2, 2), ("0", 0), ("1", 1)])
def test_nonnegative_status_is_preserved(raw_key: str, field_name: str, value: int | str, expected: int) -> None:
    status = VehicleRealtimeData.model_validate({raw_key: value})

    assert getattr(status, field_name) == expected
    assert status.raw == {raw_key: value}


@pytest.mark.parametrize(("raw_key", "field_name"), FIELDS)
@pytest.mark.parametrize("value", [None, "", "--"])
def test_empty_status_remains_unavailable(raw_key: str, field_name: str, value: str | None) -> None:
    status = VehicleRealtimeData.model_validate({raw_key: value})

    assert getattr(status, field_name) is None
    assert status.raw == {raw_key: value}


@pytest.mark.parametrize(("epb", "connection"), [(-1, 1), (1, -1)])
def test_status_fields_are_normalised_independently(epb: int, connection: int) -> None:
    payload = {"epb": epb, "powerBatteryConnection": connection}
    status = VehicleRealtimeData.model_validate(payload)

    assert status.epb == (None if epb < 0 else epb)
    assert status.power_battery_connection == (None if connection < 0 else connection)
    assert status.raw == payload


def test_snake_case_connection_input_is_normalised() -> None:
    status = VehicleRealtimeData(power_battery_connection=-1)

    assert status.power_battery_connection is None
    assert status.raw == {"power_battery_connection": -1}


def test_missing_statuses_remain_unavailable() -> None:
    status = VehicleRealtimeData.model_validate({})

    assert status.epb is None
    assert status.power_battery_connection is None
    assert status.raw == {}
