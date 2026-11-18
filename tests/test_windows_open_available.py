"""``windows.open_available`` follows the command gate (#20)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pybyd.car import BydCar
from pybyd.models.latest_config import VehicleCapabilities
from pybyd.models.vehicle import Vehicle

VIN = "TEST" + "X" * 13


def _car(learn_info: dict[str, int]) -> BydCar:
    caps = VehicleCapabilities.model_validate(
        {"vin": VIN, "source": "test_fixture", "functionNos": ["1026"], "openWindows": True, "closeWindows": True}
    )
    vehicle = Vehicle.model_validate({"vin": VIN, "vehicleFunLearnInfo": learn_info})
    return BydCar(MagicMock(), VIN, vehicle, capabilities=caps)


@pytest.mark.parametrize(
    ("learn_info", "expected"),
    [
        ({"openWindowLearnInfo": 0, "openWindow499LearnInfo": 1}, False),
        ({"openWindowLearnInfo": 1, "openWindow499LearnInfo": 1}, True),
        ({}, True),
    ],
)
def test_open_available_follows_gate(learn_info: dict[str, int], expected: bool) -> None:
    car = _car(learn_info)
    assert car.windows.open_available is expected
    assert car.windows.close_available is True


def test_open_available_without_function_no() -> None:
    caps = VehicleCapabilities.model_validate({"vin": VIN, "source": "test_fixture", "functionNos": []})
    car = BydCar(MagicMock(), VIN, Vehicle(vin=VIN), capabilities=caps)
    assert car.windows.open_available is False
