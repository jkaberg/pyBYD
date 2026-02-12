# BYD API Field Mapping Reference

This file documents the API fields that pyBYD currently parses and exposes.

How to update this file:

1. Use `scripts/dump_all.py` to capture vehicle state in different scenarios (parked, driving, charging, A/C on, doors open).
2. Compare the JSON outputs to identify which fields change and what the values mean.
3. Update the tables below and (if needed) extend the parsers/models.

Last updated: 2026-02-12

Base URL: https://dilinkappoversea-eu.byd.auto

---

## Endpoints used by this library

Only endpoints that pyBYD interfaces with are listed here.

URL base: https://dilinkappoversea-eu.byd.auto

| Endpoint (path) | Purpose | Implementation |
|---|---|---|
| `/app/account/login` | Authentication | `src/pybyd/_api/login.py` |
| `/app/account/getAllListByUserId` | Vehicle list | `src/pybyd/_api/vehicles.py` |
| `/vehicleInfo/vehicle/vehicleRealTimeRequest` | Realtime trigger | `src/pybyd/_api/realtime.py` |
| `/vehicleInfo/vehicle/vehicleRealTimeResult` | Realtime poll | `src/pybyd/_api/realtime.py` |
| `/control/getStatusNow` | HVAC status | `src/pybyd/_api/hvac.py` |
| `/control/getGpsInfo` | GPS trigger | `src/pybyd/_api/gps.py` |
| `/control/getGpsInfoResult` | GPS poll | `src/pybyd/_api/gps.py` |
| `/control/smartCharge/homePage` | Charging status | `src/pybyd/_api/charging.py` |
| `/vehicleInfo/vehicle/getEnergyConsumption` | Energy consumption | `src/pybyd/_api/energy.py` |
| `/control/remoteControl` | Remote control trigger | `src/pybyd/_api/control.py` |
| `/control/remoteControlResult` | Remote control poll | `src/pybyd/_api/control.py` |

---

## Status labels used below

- confirmed: verified by live captures
- unconfirmed: plausible but not verified yet
- conflicting: observed data contradicts the assumed meaning

## Parsing rules (current code)

The tables below describe field names and observed meanings, but pyBYD also normalizes values while parsing:

- Numeric parsing: most numeric fields accept `int`, `float`, or numeric strings; `None` and `""` become `None`. NaN becomes `None`.
- Enum parsing: fields parsed via the parser helper `_to_enum(...)` return the matching enum member when known; if the integer code is unknown, the raw integer is kept.
- Placeholders: some string fields may be reported as `"--"` by the API. For fields parsed as `str` in pyBYD, the placeholder is kept as-is.
- Vehicle list strings: the vehicle list parser normalizes missing string fields to the empty string (`""`) rather than `None`.

---

## Realtime data

URL (trigger): https://dilinkappoversea-eu.byd.auto/vehicleInfo/vehicle/vehicleRealTimeRequest

URL (poll): https://dilinkappoversea-eu.byd.auto/vehicleInfo/vehicle/vehicleRealTimeResult

Model: `VehicleRealtimeData`

Parser: `src/pybyd/_api/realtime.py`

| Group | API field | Python field | Parsed as | Values / notes |
|---|---|---|---|---|
| State | `onlineState` | `online_state` | `OnlineState` | 0=unknown (unconfirmed), 1=online (confirmed), 2=offline (unconfirmed) |
| State | `connectState` | `connect_state` | `ConnectState` | -1=unknown (conflicting: seen while driving and online), 0=disconnected (unconfirmed), 1=connected (unconfirmed) |
| State | `vehicleState` | `vehicle_state` | `VehicleState` | 0=standby (conflicting: seen while driving at 22 km/h), 1=active (unconfirmed) |
| State | `requestSerial` | `request_serial` | `str | None` | poll serial token |
| Battery | `elecPercent` | `elec_percent` | `float | None` | SOC 0-100 (confirmed) |
| Battery | `powerBattery` | `power_battery` | `float | None` | alternative SOC field (unconfirmed) |
| Range | `enduranceMileage` | `endurance_mileage` | `float | None` | estimated range in km (confirmed) |
| Range | `evEndurance` | `ev_endurance` | `float | None` | alternative range field (unconfirmed) |
| Range | `enduranceMileageV2` | `endurance_mileage_v2` | `float | None` | range v2 (unconfirmed) |
| Range | `enduranceMileageV2Unit` | `endurance_mileage_v2_unit` | `str | None` | "km" or "--" when unavailable (confirmed) |
| Odometer | `totalMileage` | `total_mileage` | `float | None` | km (confirmed) |
| Odometer | `totalMileageV2` | `total_mileage_v2` | `float | None` | km (unconfirmed) |
| Odometer | `totalMileageV2Unit` | `total_mileage_v2_unit` | `str | None` | "km" (confirmed) |
| Driving | `speed` | `speed` | `float | None` | km/h (confirmed) |
| Driving | `powerGear` | `power_gear` | `PowerGear | None` | 1=parked (confirmed), 3=drive (confirmed) |
| Climate | `tempInCar` | `temp_in_car` | `float | None` | interior temp in C; -129 means unavailable (confirmed) |
| Climate | `mainSettingTemp` | `main_setting_temp` | `int | None` | cabin set temperature, integer (confirmed) |
| Climate | `mainSettingTempNew` | `main_setting_temp_new` | `float | None` | cabin set temperature, precise C (unconfirmed) |
| Climate | `airRunState` | `air_run_state` | `AirCirculationMode | None` | 0=external (confirmed), 1=internal recirculation (confirmed) |
| Seats | `mainSeatHeatState` | `main_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed); value 1 observed and kept as raw int |
| Seats | `mainSeatVentilationState` | `main_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatHeatState` | `copilot_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatVentilationState` | `copilot_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `steeringWheelHeatState` | `steering_wheel_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed), 1 observed (unconfirmed), 2/3 possible depending on vehicle |
| Seats | `lrSeatHeatState` | `lr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `lrSeatVentilationState` | `lr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `rrSeatHeatState` | `rr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `rrSeatVentilationState` | `rr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Charging | `chargingState` | `charging_state` | `ChargingState | int` | -1=disconnected (confirmed), 0=not charging (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `chargeState` | `charge_state` | `ChargingState | int | None` | -1=disconnected (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `waitStatus` | `wait_status` | `int | None` | charge wait status (unconfirmed) |
| Charging | `fullHour` | `full_hour` | `int | None` | hours to full; -1 means not available (confirmed) |
| Charging | `fullMinute` | `full_minute` | `int | None` | minutes to full; -1 means not available (confirmed) |
| Charging | `remainingHours` | `charge_remaining_hours` | `int | None` | remaining hours; -1 means not available (confirmed) |
| Charging | `remainingMinutes` | `charge_remaining_minutes` | `int | None` | remaining minutes; -1 means not available (confirmed) |
| Charging | `bookingChargeState` | `booking_charge_state` | `int | None` | scheduled charging state; 0=off (confirmed) |
| Charging | `bookingChargingHour` | `booking_charging_hour` | `int | None` | scheduled charge start hour (unconfirmed) |
| Charging | `bookingChargingMinute` | `booking_charging_minute` | `int | None` | scheduled charge start minute (unconfirmed) |
| Doors | `leftFrontDoor` | `left_front_door` | `DoorOpenState | None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `rightFrontDoor` | `right_front_door` | `DoorOpenState | None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `leftRearDoor` | `left_rear_door` | `DoorOpenState | None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `rightRearDoor` | `right_rear_door` | `DoorOpenState | None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `trunkLid` / `backCover` | `trunk_lid` | `DoorOpenState | None` | uses `trunkLid` first, falls back to `backCover`; 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `slidingDoor` | `sliding_door` | `DoorOpenState | None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `forehold` | `forehold` | `DoorOpenState | None` | frunk; 0=closed (confirmed), 1=open (unconfirmed) |
| Locks | `leftFrontDoorLock` | `left_front_door_lock` | `LockState | None` | 2=locked (confirmed), 1=unlocked (confirmed) |
| Locks | `rightFrontDoorLock` | `right_front_door_lock` | `LockState | None` | 2=locked (confirmed), 1=unlocked (confirmed) |
| Locks | `leftRearDoorLock` | `left_rear_door_lock` | `LockState | None` | 2=locked (confirmed), 1=unlocked (confirmed) |
| Locks | `rightRearDoorLock` | `right_rear_door_lock` | `LockState | None` | 2=locked (confirmed), 1=unlocked (confirmed) |
| Locks | `slidingDoorLock` | `sliding_door_lock` | `LockState | None` | 2=locked (confirmed), 1=unlocked (confirmed) |
| Windows | `leftFrontWindow` | `left_front_window` | `WindowState | None` | 1=closed (confirmed), 2=open (unconfirmed) |
| Windows | `rightFrontWindow` | `right_front_window` | `WindowState | None` | 1=closed (confirmed), 2=open (unconfirmed) |
| Windows | `leftRearWindow` | `left_rear_window` | `WindowState | None` | 1=closed (confirmed), 2=open (unconfirmed) |
| Windows | `rightRearWindow` | `right_rear_window` | `WindowState | None` | 1=closed (confirmed), 2=open (unconfirmed) |
| Windows | `skylight` | `skylight` | `WindowState | None` | 1=closed (confirmed), 2=open (unconfirmed) |
| Tires | `leftFrontTirepressure` | `left_front_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightFrontTirepressure` | `right_front_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftRearTirepressure` | `left_rear_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightRearTirepressure` | `right_rear_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftFrontTireStatus` | `left_front_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `rightFrontTireStatus` | `right_front_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `leftRearTireStatus` | `left_rear_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `rightRearTireStatus` | `right_rear_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `tirePressUnit` | `tire_press_unit` | `TirePressureUnit | None` | 1=bar (confirmed), 2=psi (unconfirmed), 3=kPa (unconfirmed) |
| Tires | `tirepressureSystem` | `tirepressure_system` | `int | None` | TPMS system state (unconfirmed) |
| Tires | `rapidTireLeak` | `rapid_tire_leak` | `int | None` | 0=no leak (confirmed) |
| Energy | `totalPower` | `total_power` | `float | None` | total power (unconfirmed) |
| Energy | `totalEnergy` | `total_energy` | `str | None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumption` | `nearest_energy_consumption` | `str | None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumptionUnit` | `nearest_energy_consumption_unit` | `str | None` | unit string (unconfirmed) |
| Energy | `recent50kmEnergy` | `recent_50km_energy` | `str | None` | string; "--" when unavailable (confirmed) |
| Fuel | `oilEndurance` | `oil_endurance` | `float | None` | -1 means not applicable for EV (confirmed) |
| Fuel | `oilPercent` | `oil_percent` | `float | None` | 0 for EV (confirmed) |
| Fuel | `totalOil` | `total_oil` | `float | None` | 0 for EV (confirmed) |
| Warnings | `powerSystem` | `power_system` | `int | None` | 0=normal (confirmed) |
| Warnings | `engineStatus` | `engine_status` | `int | None` | 0=off (confirmed) |
| Warnings | `epb` | `epb` | `int | None` | 0=released (confirmed) |
| Warnings | `eps` | `eps` | `int | None` | 0=normal (confirmed) |
| Warnings | `esp` | `esp` | `int | None` | 0=normal (confirmed) |
| Warnings | `abs` | `abs_warning` | `int | None` | 0=normal (confirmed) |
| Warnings | `svs` | `svs` | `int | None` | 0=normal (confirmed) |
| Warnings | `srs` | `srs` | `int | None` | 0=normal (confirmed) |
| Warnings | `ect` | `ect` | `int | None` | 0=normal (confirmed) |
| Warnings | `ectValue` | `ect_value` | `int | None` | -1 means not available (confirmed) |
| Warnings | `pwr` | `pwr` | `int | None` | 2 observed (unconfirmed) |
| Features | `sentryStatus` | `sentry_status` | `int | None` | 0=off (unconfirmed), 1=on (unconfirmed), 2 observed (unconfirmed) |
| Features | `batteryHeatState` | `battery_heat_state` | `int | None` | 0=off (confirmed) |
| Features | `chargeHeatState` | `charge_heat_state` | `int | None` | 0=off (confirmed) |
| Features | `upgradeStatus` | `upgrade_status` | `int | None` | 0=none (confirmed) |
| Metadata | `time` | `timestamp` | `int | None` | epoch seconds (confirmed) |

---

## HVAC / climate status

URL: https://dilinkappoversea-eu.byd.auto/control/getStatusNow

Model: `HvacStatus`

Parser: `src/pybyd/_api/hvac.py`

Response wraps data under the `statusNow` key.

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `acSwitch` | `ac_switch` | `int | None` | 0=off, 1=on (unconfirmed) |
| `status` | `status` | `int | None` | overall HVAC status (unconfirmed) |
| `airConditioningMode` | `air_conditioning_mode` | `int | None` | mode (unconfirmed) |
| `windMode` | `wind_mode` | `int | None` | fan mode (unconfirmed) |
| `windPosition` | `wind_position` | `int | None` | airflow direction (unconfirmed) |
| `cycleChoice` | `cycle_choice` | `int | None` | 1=external circulation (confirmed) |
| `mainSettingTemp` | `main_setting_temp` | `int | None` | set temp integer (unconfirmed) |
| `mainSettingTempNew` | `main_setting_temp_new` | `float | None` | set temp C (unconfirmed) |
| `copilotSettingTemp` | `copilot_setting_temp` | `int | None` | passenger set temp (unconfirmed) |
| `copilotSettingTempNew` | `copilot_setting_temp_new` | `float | None` | passenger set temp C (unconfirmed) |
| `tempInCar` | `temp_in_car` | `float | None` | interior C; -129 means unavailable (confirmed) |
| `tempOutCar` | `temp_out_car` | `float | None` | exterior C (unconfirmed) |
| `whetherSupportAdjustTemp` | `whether_support_adjust_temp` | `int | None` | 1=supported (confirmed) |
| `frontDefrostStatus` | `front_defrost_status` | `int | None` | 0=off (unconfirmed) |
| `electricDefrostStatus` | `electric_defrost_status` | `int | None` | 0=off (unconfirmed) |
| `wiperHeatStatus` | `wiper_heat_status` | `int | None` | 0=off (unconfirmed) |
| `mainSeatHeatState` | `main_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| `mainSeatVentilationState` | `main_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| `copilotSeatHeatState` | `copilot_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| `copilotSeatVentilationState` | `copilot_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| `steeringWheelHeatState` | `steering_wheel_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed), 1 observed (unconfirmed) |
| `lrSeatHeatState` | `lr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| `lrSeatVentilationState` | `lr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| `rrSeatHeatState` | `rr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| `rrSeatVentilationState` | `rr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| `rapidIncreaseTempState` | `rapid_increase_temp_state` | `int | None` | 0=off (confirmed) |
| `rapidDecreaseTempState` | `rapid_decrease_temp_state` | `int | None` | 0=off (confirmed) |
| `refrigeratorState` | `refrigerator_state` | `int | None` | 0=off (confirmed) |
| `refrigeratorDoorState` | `refrigerator_door_state` | `int | None` | 0=closed (confirmed) |
| `pm` | `pm` | `int | None` | PM2.5 value (unconfirmed) |
| `pm25StateOutCar` | `pm25_state_out_car` | `int | None` | outside PM2.5 state (unconfirmed) |

---

## Charging status

URL: https://dilinkappoversea-eu.byd.auto/control/smartCharge/homePage

Model: `ChargingStatus`

Parser: `src/pybyd/_api/charging.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | VIN |
| `soc` | `soc` | `int | None` | SOC 0-100 (confirmed) |
| `chargingState` | `charging_state` | `int | None` | 15 means not charging (confirmed); other active charging values not captured yet |
| `connectState` | `connect_state` | `int | None` | 0=not connected (confirmed), 1=connected (confirmed) |
| `waitStatus` | `wait_status` | `int | None` | 0 (confirmed) |
| `fullHour` | `full_hour` | `int | None` | -1 means not available (confirmed) |
| `fullMinute` | `full_minute` | `int | None` | -1 means not available (confirmed) |
| `updateTime` | `update_time` | `int | None` | epoch seconds (confirmed) |

---

## GPS / location

URL (trigger): https://dilinkappoversea-eu.byd.auto/control/getGpsInfo

URL (poll): https://dilinkappoversea-eu.byd.auto/control/getGpsInfoResult

Model: `GpsInfo`

Parser: `src/pybyd/_api/gps.py`

Note: the parser accepts multiple aliases for some fields.

| API field (aliases) | Python field | Type | Values / notes |
|---|---|---|---|
| `latitude` / `lat` / `gpsLatitude` | `latitude` | `float | None` | degrees (confirmed) |
| `longitude` / `lng` / `lon` / `gpsLongitude` | `longitude` | `float | None` | degrees (confirmed) |
| `speed` / `gpsSpeed` | `speed` | `float | None` | km/h (unconfirmed) |
| `direction` / `heading` / `course` | `direction` | `float | None` | degrees 0-360 (confirmed) |
| `gpsTimeStamp` / `gpsTimestamp` / `gpsTime` / `time` / `uploadTime` | `gps_timestamp` | `int | None` | epoch seconds (confirmed) |
| `requestSerial` | `request_serial` | `str | None` | poll serial token (unconfirmed) |

---

## Energy consumption

URL: https://dilinkappoversea-eu.byd.auto/vehicleInfo/vehicle/getEnergyConsumption

Model: `EnergyConsumption`

Parser: `src/pybyd/_api/energy.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | VIN |
| `totalEnergy` | `total_energy` | `float | None` | string "--" maps to None (confirmed) |
| `avgEnergyConsumption` | `avg_energy_consumption` | `float | None` | unconfirmed |
| `electricityConsumption` | `electricity_consumption` | `float | None` | unconfirmed |
| `fuelConsumption` | `fuel_consumption` | `float | None` | unconfirmed |

Note: when the API returns error `1001`, the parser can synthesise partial data from the realtime cache.

---

## Vehicle list

URL: https://dilinkappoversea-eu.byd.auto/app/account/getAllListByUserId

Model: `Vehicle`

Parser: `src/pybyd/_api/vehicles.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | VIN |
| `modelName` | `model_name` | `str` | model name (confirmed) |
| `brandName` | `brand_name` | `str` | brand name (confirmed) |
| `energyType` | `energy_type` | `str` | "0" for EV (confirmed) |
| `autoAlias` | `auto_alias` | `str` | empty string when missing (unconfirmed) |
| `autoPlate` | `auto_plate` | `str` | empty string when missing (unconfirmed) |
| `picMainUrl` (or `cfPic.picMainUrl`) | `pic_main_url` | `str` | empty string when missing (unconfirmed) |
| `picSetUrl` (or `cfPic.picSetUrl`) | `pic_set_url` | `str` | empty string when missing (unconfirmed) |
| `outModelType` | `out_model_type` | `str` | empty string when missing (unconfirmed) |
| `totalMileage` | `total_mileage` | `float | None` | odometer (unconfirmed) |
| `modelId` | `model_id` | `int | None` | internal model id (unconfirmed) |
| `carType` | `car_type` | `int | None` | internal car type id (unconfirmed) |
| `defaultCar` | `default_car` | `bool` | 1 maps to True (confirmed) |
| `empowerType` | `empower_type` | `int | None` | 2=owner (confirmed), -1=shared user (confirmed) |
| `permissionStatus` | `permission_status` | `int | None` | 2 observed for full permissions (confirmed) |
| `tboxVersion` | `tbox_version` | `str` | empty string when missing; e.g. "3" (unconfirmed) |
| `vehicleState` | `vehicle_state` | `str` | empty string when missing; e.g. "1" (unconfirmed) |
| `autoBoughtTime` | `auto_bought_time` | `int | None` | epoch ms (unconfirmed) |
| `yunActiveTime` | `yun_active_time` | `int | None` | epoch ms (unconfirmed) |
| `empowerId` | `empower_id` | `int | None` | empower relationship id (confirmed) |
| `rangeDetailList` | `range_detail_list` | `list[EmpowerRange]` | permission scopes (confirmed) |

---

## Remote control

URL (trigger): https://dilinkappoversea-eu.byd.auto/control/remoteControl

URL (poll): https://dilinkappoversea-eu.byd.auto/control/remoteControlResult

Model: `RemoteControlResult`

Parser: `src/pybyd/_api/control.py`

### Command types

| Python enum (`RemoteCommand`) | API value (`commandType`) | Description |
|---|---|---|
| `LOCK` | `LOCKDOOR` | lock all doors |
| `UNLOCK` | `OPENDOOR` | unlock all doors |
| `START_CLIMATE` | `OPENAIR` | start A/C |
| `STOP_CLIMATE` | `CLOSEAIR` | stop A/C |
| `SCHEDULE_CLIMATE` | `BOOKINGAIR` | schedule A/C |
| `FIND_CAR` | `FINDCAR` | find my car |
| `FLASH_LIGHTS` | `FLASHLIGHTNOWHISTLE` | flash lights |
| `CLOSE_WINDOWS` | `CLOSEWINDOW` | close windows |
| `SEAT_CLIMATE` | `VENTILATIONHEATING` | seat heat/vent |
| `BATTERY_HEAT` | `BATTERYHEAT` | battery heat |

### Result fields

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `controlState` | `control_state` | `ControlState` | 0=pending, 1=success, 2=failure (unconfirmed). If `res` is present instead, pyBYD maps `res==2` to success. |
| `requestSerial` | `request_serial` | `str | None` | poll serial token (unconfirmed) |
| `res` | (immediate) | `int` | 2 observed as success (unconfirmed) |

---

## Enum mappings (shared)

These reflect the enums currently implemented in `src/pybyd/models/realtime.py`.

| Enum | Value | Name | Status | Notes |
|---|---:|---|---|---|
| `OnlineState` | 0 | `UNKNOWN` | unconfirmed | |
| `OnlineState` | 1 | `ONLINE` | confirmed | |
| `OnlineState` | 2 | `OFFLINE` | unconfirmed | |
| `ConnectState` | -1 | `UNKNOWN` | conflicting | observed while driving and online |
| `ConnectState` | 0 | `DISCONNECTED` | unconfirmed | |
| `ConnectState` | 1 | `CONNECTED` | unconfirmed | |
| `VehicleState` | 0 | `STANDBY` | conflicting | observed while driving at 22 km/h |
| `VehicleState` | 1 | `ACTIVE` | unconfirmed | |
| `ChargingState` | -1 | `DISCONNECTED` | confirmed | |
| `ChargingState` | 0 | `NOT_CHARGING` | confirmed | |
| `ChargingState` | 15 | `GUN_CONNECTED` | confirmed | gun plugged in, charging not active |
| `PowerGear` | 1 | `PARKED` | confirmed | |
| `PowerGear` | 3 | `DRIVE` | confirmed | |
| `SeatHeatVentState` | 0 | `OFF` | confirmed | |
| `SeatHeatVentState` | 2 | `LOW` | confirmed | |
| `SeatHeatVentState` | 3 | `HIGH` | confirmed | |
| `SeatHeatVentState` | 1 | (not a member) | confirmed | observed; parser keeps raw int |
| `AirCirculationMode` | 0 | `EXTERNAL` | confirmed | |
| `AirCirculationMode` | 1 | `INTERNAL` | confirmed | |
| `TirePressureUnit` | 1 | `BAR` | confirmed | |
| `TirePressureUnit` | 2 | `PSI` | unconfirmed | |
| `TirePressureUnit` | 3 | `KPA` | unconfirmed | |
| `WindowState` | 0 | `OPEN` | unconfirmed | |
| `WindowState` | 1 | `CLOSED` | confirmed | |
| `DoorOpenState` | 0 | `CLOSED` | confirmed | |
| `DoorOpenState` | 1 | `OPEN` | unconfirmed | |
| `LockState` | 0 | `LOCKED` | unconfirmed | |
| `LockState` | 1 | `UNLOCKED` | confirmed | |
| `ControlState` | 0 | `PENDING` | unconfirmed | |
| `ControlState` | 1 | `SUCCESS` | unconfirmed | |
| `ControlState` | 2 | `FAILURE` | unconfirmed | |

---

## Unparsed fields

Some endpoints contain additional fields that pyBYD currently keeps only in `raw`.
If you discover value meanings, add them here and consider mapping them into models.

Realtime examples (not exhaustively maintained):

| API field | Observed value | Notes |
|---|---|---|
| `rate` | `-999` | possibly charging rate (unconfirmed) |
| `lessOneMin` | `false` | possibly time-to-full flag (unconfirmed) |
| `gl` | `double` | unknown (from AutoStatusChargeDataBean) |
| `ins` | `int` | unknown (from AutoStatusChargeDataBean) |
| `okLight` | `int` | OK light indicator (from AutoStatusChargeDataBean) |
| `safeCharge` | `boolean` | safe charge flag (from AutoStatusChargeDataBean) |
| `smartChargeState` | `int` | smart charge state (from AutoStatusChargeDataBean) |
| `smartJourneyState` | `int` | smart journey state (from AutoStatusChargeDataBean) |
| `brakingSystem` | `int` | braking system warning (from AutoStatusChargeDataBean) |
| `chargingSystem` | `int` | charging system warning (from AutoStatusChargeDataBean) |
| `steeringSystem` | `int` | steering system warning (from AutoStatusChargeDataBean) |
| `oilPressureSystem` | `int` | oil pressure system warning (from AutoStatusChargeDataBean) |
| `powerBatteryConnection` | `int` | power battery connection status (from AutoStatusChargeDataBean) |
| `type` | `string` | data type identifier (from AutoStatusChargeDataBean) |
| `totalConsumptionEn` | `string` | total consumption English text (from AutoStatusChargeDataBean) |

---

## Additional fields from decompiled classes

The following fields were discovered by analyzing decompiled Java classes from the BYD app (`.docu/class-jadx/`).

### AutoStatusChargeDataBean (Realtime additional fields)

| API field | Type | Notes |
|---|---|---|
| `bookingDepartureHour` | `int` | scheduled departure hour |
| `bookingDepartureMinute` | `int` | scheduled departure minute |
| `bookingDepartureState` | `int` | scheduled departure state (0=off) |
| `chargeStartTimeHour` | `int` | charge start time hour |
| `chargeStartTimeMin` | `int` | charge start time minute |
| `journeyStartTimeHour` | `int` | journey start time hour |
| `journeyStartTimeMin` | `int` | journey start time minute |
| `energyConsumption` | `string` | energy consumption value |
| `copilotSettingTemp` | `int` | copilot setting temperature (also in realtime) |

### AirStatusNow.StatusNowBean (HVAC additional fields)

| API field | Type | Notes |
|---|---|---|
| `airTempLevel` | `int` | air temperature level |
| `firstWarm` | `int` | first row heating |
| `firstWind` | `int` | first row fan |
| `secondWarm` | `int` | second row heating |
| `secondWind` | `int` | second row fan |
| `frontAirSumPattern` | `int` | front air sum pattern |
| `temp` | `int` | temperature value |
| `timeChoice` | `int` | time choice setting |

### AirStatusNow.BookingInfoBean (HVAC booking fields)

| API field | Type | Notes |
|---|---|---|
| `bookingId` | `long` | booking ID |
| `bookingTime` | `int` | booking time |
| `timeSpan` | `int` | time span duration |
| `windLevel` | `int` | fan level |

### ControlParamsMap (Remote control request fields)

These are parameters used for climate/control requests:

| Field | Type | Notes |
|---|---|---|
| `remoteMode` | `int` | remote mode |
| `acSwitch` | `int` | A/C switch (0=off, 1=on) |
| `mainSettingTemp` | `int` | main temperature setting |
| `copilotSettingTemp` | `int` | copilot temperature setting |
| `cycleMode` | `int` | air circulation mode |
| `windLevel` | `int` | fan level |
| `windMode` | `int` | fan mode |
| `timeSpan` | `int` | duration in minutes |
| `bookingId` | `long` | booking ID |
| `bookingTime` | `long` | booking time |
| `mainHeat` | `int` | main seat heating |
| `mainVentilation` | `int` | main seat ventilation |
| `copilotHeat` | `int` | copilot seat heating |
| `copilotVentilation` | `int` | copilot seat ventilation |

### SeatAndWheelControlParamsMap (Seat/wheel control fields)

| Field | Type | Notes |
|---|---|---|
| `chairType` | `string` | chair type identifier |
| `steeringWheelHeatState` | `int` | steering wheel heating |

### BatteryControlParamsMap (Battery heat control)

| Field | Type | Notes |
|---|---|---|
| `batteryHeat` | `int` | battery heat on/off (0/1) |

### ReservationInfoResponse (Charging reservation fields)

| API field | Type | Notes |
|---|---|---|
| `smartChargeDto` | `object` | smart charge configuration |
| `smartChargeTips` | `string` | smart charge tips text |
| `smartJourneyDto` | `object` | smart journey configuration |
| `timeZone` | `string` | timezone string |
| `vehicleTimeZone` | `string` | vehicle timezone |

### ReservationCharge (Smart charge settings)

| API field | Type | Default | Notes |
|---|---|---|---|
| `startChargeTime` | `string` | `"23:00"` | charge start time |
| `endChargeTime` | `string` | `"full"` | charge end time or "full" |
| `chargeWay` | `string` | `"0,1,2,6"` | charge days (comma-separated weekdays, 0=Mon?) |
| `status` | `int` | `0` | status |

### ReservationDeparture (Smart journey settings)

| API field | Type | Default | Notes |
|---|---|---|---|
| `useVehicleTime` | `string` | `"08:00"` | departure time |
| `chargeWay` | `string` | `"0"` | charge days |
| `startDiscountPrice` | `string` | | start discount price time |
| `endDiscountPrice` | `string` | `"07:00"` | end discount price time |
| `batteryHeatStatus` | `int` | `0` | battery heating for departure |
| `airStats` | `int` | `0` | A/C for departure |

### RemoteControlResultResponse (Control result fields)

| API field | Type | Notes |
|---|---|---|
| `res` | `int` | result code (2=success observed) |
| `message` | `string` | result message |

### VehicleLocationInfo (GPS additional)

| API field | Type | Notes |
|---|---|---|
| `gpsTimeStamp` | `string` | GPS timestamp as string |

---

## App routing paths (from RouterURLS.java)

Internal app paths that may indicate feature availability:

| Path | Feature |
|---|---|
| `/car/airConditioner` | A/C control |
| `/car/AirBootReservation` | A/C boot reservation |
| `/car/AirMoreSetting` | A/C more settings |
| `/car/DoorAndWindowStatus` | Door and window status |
| `/car/SeatStatus2` | Seat status |
| `/car/TirePressureStatus` | Tire pressure status |
| `/car/BatteryHeating` | Battery heating |
| `/car/BatteryPreheatHelp` | Battery preheat help |
| `/nfc/digitalKey` | Digital key |
| `/nfc/detail` | NFC detail |
| `/nfc/all/keys` | All NFC keys |
| `/nfc/activate` | NFC activate |
| `/reservation/Reservation` | Reservations |
| `/reservation/editing` | Edit reservation |
| `/reservation/editing/departure` | Edit departure |
| `/reservation/editing/low` | Edit low-price charging |
| `/widget/setting` | Widget settings |
