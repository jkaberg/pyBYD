"""Energy consumption data model.

The ``getEnergyConsumption`` endpoint returns a four-section nested
object: two 7-day rolling graphs (``selfGraph``, ``autoModelGraph``)
plus per-leg breakouts for lifetime totals (``cumulativeEnergyConsumption``)
and the last 50km (``nearestEnergyConsumption``). For hybrids
each leg is exposed in its own field — no string splitting required.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BeforeValidator, Field

from pybyd.models._base import BydBaseModel, BydTimestamp


def _to_float(value: Any) -> float | None:
    """Coerce BYD numeric strings to ``float``; sentinels → ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("", "--", "nan", "NaN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    """Coerce BYD numeric strings to ``int``; sentinels → ``None``."""
    f = _to_float(value)
    return int(f) if f is not None else None


def _to_float_list(value: Any) -> list[float]:
    """Coerce a list of numeric strings to ``list[float]``; drop sentinels."""
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        f = _to_float(item)
        if f is not None:
            out.append(f)
    return out


def _strip_middot(value: Any) -> Any:
    """Drop the ``·`` (middle-dot) the BYD cloud injects into unit strings.

    HA-friendly unit strings drop the middot — ``kW·h/100km`` becomes
    ``kWh/100km``. Applied to every unit field on this endpoint so the
    realtime and energy models surface a single normalized form.
    """
    if isinstance(value, str):
        return value.replace("·", "")
    return value


_BydFloat = Annotated[float | None, BeforeValidator(_to_float)]
_BydInt = Annotated[int | None, BeforeValidator(_to_int)]
_BydFloatList = Annotated[list[float], BeforeValidator(_to_float_list)]
_BydUnit = Annotated[str, BeforeValidator(_strip_middot)]


class EnergyConsumptionGraph(BydBaseModel):
    """7-day rolling consumption series.

    Used for both ``selfGraph`` (this vehicle) and ``autoModelGraph``
    (model-average comparison). The unit reflects whichever leg matches
    the request's ``powerType`` — ``kW·h/100km`` for ``"0"``, ``L/100km``
    for ``"1"``/``"2"``.
    """

    energy_consumption: _BydFloatList = Field(default_factory=list)
    energy_consumption_unit: _BydUnit = ""


class CumulativeEnergyConsumption(BydBaseModel):
    """Lifetime AVERAGE consumption rate, per leg.

    Named ``cumulativeEnergyConsumption`` by the API, but the fields it
    carries are consumption rates (kWh/100km, L/100km) averaged over
    the vehicle's lifetime — not an absolute cumulative energy total.

    Both legs are populated for hybrids when ``powerType="2"``; pure-EV
    or pure-ICE responses leave the unused leg empty.
    """

    total_mileage: _BydFloat = None
    mileage_unit: _BydUnit = ""
    avg_ev_consumption: _BydFloat = None
    """Average EV consumption (kWh/100km)."""
    ev_unit: _BydUnit = ""
    avg_oil_consumption: _BydFloat = None
    """Average petrol consumption (L/100km)."""
    oil_unit: _BydUnit = ""


class NearestEnergyConsumption(BydBaseModel):
    """Last-50km breakdown, per leg + driving-mode distribution."""

    avg_ev_consumption: _BydFloat = None
    """Average EV consumption over the last 50km (kWh/100km)."""
    ev_consumption: _BydFloat = None
    """Total EV energy used over the last 50km (kWh)."""
    ev_unit: _BydUnit = ""
    ev_value_unit: _BydUnit = ""

    avg_oil_consumption: _BydFloat = None
    """Average petrol consumption over the last 50km (L/100km)."""
    oil_consumption: _BydFloat = None
    """Total petrol used over the last 50km (L)."""
    oil_unit: _BydUnit = ""
    oil_value_unit: _BydUnit = ""

    avg_eq_oil_consumption: _BydFloat = None
    """Equivalent petrol consumption — EV usage expressed in L/100km."""

    drive_distribution: _BydInt = None
    """Percentage of the last 50km in normal drive mode."""
    elect_distribution: _BydInt = None
    """Percentage of the last 50km on electric power."""
    air_distribution: _BydInt = None
    """Percentage of the last 50km with HVAC active."""
    other_distribution: _BydInt = None
    """Percentage of the last 50km in other modes."""


class EnergyConsumption(BydBaseModel):
    """Energy consumption data for a vehicle.

    Mirrors the four-section response of
    ``/vehicleInfo/vehicle/getEnergyConsumption``.
    """

    _KEY_ALIASES: ClassVar[dict[str, str]] = {
        "time": "timestamp",
    }

    self_graph: EnergyConsumptionGraph | None = None
    cumulative_energy_consumption: CumulativeEnergyConsumption | None = None
    nearest_energy_consumption: NearestEnergyConsumption | None = None
    auto_model_graph: EnergyConsumptionGraph | None = None
    timestamp: BydTimestamp = None
