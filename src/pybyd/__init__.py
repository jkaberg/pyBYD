"""pybyd - Async Python client for BYD vehicle telemetry API."""

from importlib.metadata import PackageNotFoundError, version

from pybyd._capabilities.seat import SeatLevel, SeatPosition
from pybyd._state_engine import VehicleSnapshot
from pybyd.car import BydCar
from pybyd.client import BydClient
from pybyd.config import BydConfig, DeviceProfile
from pybyd.exceptions import (
    BydApiError,
    BydAuthenticationError,
    BydControlPasswordError,
    BydDataUnavailableError,
    BydEndpointNotSupportedError,
    BydError,
    BydRateLimitError,
    BydRemoteControlError,
    BydServiceBusyError,
    BydSessionExpiredError,
    BydTransportError,
)
from pybyd.models import (
    VALID_CLIMATE_DURATIONS,
    ChargingStatus,
    CommandAckEvent,
    CommandLifecycleEvent,
    DoorOpenState,
    EnergyConsumption,
    GpsInfo,
    HvacStatus,
    MessageGroup,
    NotificationCategory,
    SeatHeatVentState,
    TirePressureUnit,
    Vehicle,
    VehicleCapabilities,
    VehicleLatestConfig,
    VehicleMsgItemInfo,
    VehicleMsgListResponse,
    VehicleRealtimeData,
    WindowState,
    minutes_to_time_span,
)

try:
    __version__ = version("pybyd")
except PackageNotFoundError:
    __version__ = "0+local"

__all__ = [
    "__version__",
    "BydApiError",
    "BydAuthenticationError",
    "BydCar",
    "ChargingStatus",
    "BydClient",
    "BydConfig",
    "BydControlPasswordError",
    "BydDataUnavailableError",
    "BydEndpointNotSupportedError",
    "BydError",
    "BydRateLimitError",
    "BydRemoteControlError",
    "BydServiceBusyError",
    "BydSessionExpiredError",
    "BydTransportError",
    "CommandAckEvent",
    "CommandLifecycleEvent",
    "DeviceProfile",
    "DoorOpenState",
    "EnergyConsumption",
    "GpsInfo",
    "HvacStatus",
    "SeatHeatVentState",
    "SeatLevel",
    "SeatPosition",
    "TirePressureUnit",
    "VALID_CLIMATE_DURATIONS",
    "VehicleCapabilities",
    "Vehicle",
    "VehicleLatestConfig",
    "MessageGroup",
    "NotificationCategory",
    "VehicleMsgItemInfo",
    "VehicleMsgListResponse",
    "VehicleRealtimeData",
    "VehicleSnapshot",
    "WindowState",
    "minutes_to_time_span",
]
