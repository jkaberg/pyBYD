"""Data models for BYD API responses."""

from pybyd._constants import VALID_CLIMATE_DURATIONS, minutes_to_time_span
from pybyd.models._base import BydBaseModel, BydEnum, BydTimestamp, parse_byd_timestamp
from pybyd.models.booking import BookingList, ClimateBooking
from pybyd.models.charging import ChargingStatus
from pybyd.models.command_gating import CommandGateRule, CommandGateVerdict
from pybyd.models.control import (
    BatteryHeatParams,
    ClimateScheduleParams,
    ClimateStartParams,
    CommandAck,
    CommandAckDiagnostics,
    CommandAckEvent,
    CommandLifecycleEvent,
    CommandLifecycleStatus,
    ControlState,
    RemoteCommand,
    RemoteControlResult,
    SeatClimateParams,
    VerifyControlPasswordResponse,
)
from pybyd.models.energy import EnergyConsumption
from pybyd.models.gps import GpsInfo
from pybyd.models.hvac import HvacStatus, celsius_to_scale
from pybyd.models.latest_config import LatestConfigFunction, VehicleCapabilities, VehicleLatestConfig
from pybyd.models.message_center import (
    MessageGroup,
    NotificationCategory,
    VehicleMsgItemInfo,
    VehicleMsgListResponse,
)
from pybyd.models.push_notification import PushNotificationState
from pybyd.models.realtime import (
    AirCirculationMode,
    ChargingState,
    ConnectState,
    DoorOpenState,
    LockState,
    OnlineState,
    PowerGear,
    SeatHeatVentState,
    StearingWheelHeat,
    TirePressureUnit,
    VehicleRealtimeData,
    VehicleState,
    WindowState,
)
from pybyd.models.smart_charging import SmartChargingSchedule
from pybyd.models.token import AuthToken
from pybyd.models.vehicle import EmpowerRange, EnergyType, Vehicle

__all__ = [
    "AirCirculationMode",
    "AuthToken",
    "BatteryHeatParams",
    "BydBaseModel",
    "BydEnum",
    "BydTimestamp",
    "ChargingState",
    "BookingList",
    "ChargingStatus",
    "ClimateBooking",
    "CommandGateRule",
    "CommandGateVerdict",
    "ClimateScheduleParams",
    "ClimateStartParams",
    "CommandAck",
    "CommandAckDiagnostics",
    "CommandAckEvent",
    "CommandLifecycleEvent",
    "CommandLifecycleStatus",
    "ConnectState",
    "ControlState",
    "DoorOpenState",
    "EmpowerRange",
    "EnergyConsumption",
    "EnergyType",
    "GpsInfo",
    "HvacStatus",
    "LatestConfigFunction",
    "LockState",
    "OnlineState",
    "PowerGear",
    "PushNotificationState",
    "RemoteCommand",
    "RemoteControlResult",
    "SeatClimateParams",
    "SeatHeatVentState",
    "SmartChargingSchedule",
    "StearingWheelHeat",
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
    "VehicleState",
    "VerifyControlPasswordResponse",
    "WindowState",
    "celsius_to_scale",
    "minutes_to_time_span",
    "parse_byd_timestamp",
]
