"""Data models for BYD API responses."""

from pybyd._constants import DEFAULT_TEMP_OFFSET_C, VALID_CLIMATE_DURATIONS, minutes_to_time_span
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
from pybyd.models.maintenance import MaintenanceHistory, MaintenanceRecord
from pybyd.models.message_center import (
    NotificationCategory,
    VehicleMsgItemInfo,
    VehicleMsgListResponse,
)
from pybyd.models.ota import OtaStatus, OtaUpgradeInfo, OtaUpgradeResult
from pybyd.models.push_notification import PushNotificationState
from pybyd.models.realtime import (
    AirCirculationMode,
    AirRunState,
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
from pybyd.models.smart_charging import AcCurrentLimit, SmartChargingSchedule
from pybyd.models.standby import (
    PlanClimate,
    PlanSeats,
    StandbyPlan,
    StandbyPlanCommand,
    StandbyPlanList,
    StandbyPlanRunResult,
    StandbySchedule,
)
from pybyd.models.token import AuthToken
from pybyd.models.vehicle import EmpowerDetail, EmpowerRange, EnergyType, Vehicle

__all__ = [
    "AcCurrentLimit",
    "AirCirculationMode",
    "AirRunState",
    "AuthToken",
    "BatteryHeatParams",
    "BydBaseModel",
    "BydEnum",
    "BydTimestamp",
    "ChargingState",
    "BookingList",
    "ChargingStatus",
    "ClimateBooking",
    "PlanClimate",
    "PlanSeats",
    "StandbyPlan",
    "StandbyPlanCommand",
    "StandbyPlanList",
    "StandbyPlanRunResult",
    "StandbySchedule",
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
    "EmpowerDetail",
    "EmpowerRange",
    "EnergyConsumption",
    "EnergyType",
    "GpsInfo",
    "HvacStatus",
    "LatestConfigFunction",
    "LockState",
    "MaintenanceHistory",
    "MaintenanceRecord",
    "OnlineState",
    "OtaStatus",
    "OtaUpgradeInfo",
    "OtaUpgradeResult",
    "PowerGear",
    "PushNotificationState",
    "RemoteCommand",
    "RemoteControlResult",
    "SeatClimateParams",
    "SeatHeatVentState",
    "SmartChargingSchedule",
    "StearingWheelHeat",
    "TirePressureUnit",
    "DEFAULT_TEMP_OFFSET_C",
    "VALID_CLIMATE_DURATIONS",
    "VehicleCapabilities",
    "Vehicle",
    "VehicleLatestConfig",
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
