# BYD API Field Mapping Reference

This file documents every API field observed in live captures and whether pyBYD parses it into a typed model field or keeps it only in `raw`.

How to update this file:

1. Use `scripts/dump_all.py` to capture vehicle state in different scenarios (parked, driving, charging, A/C on, doors open).
2. Compare the JSON outputs to identify which fields change and what the values mean.
3. Update the tables below and (if needed) extend the parsers/models.

Last updated: 2026-02-24

Base URL: https://dilinkappoversea-eu.byd.auto

---

## Endpoints used by this library

Only endpoints that pyBYD interfaces with are listed here.

URL base: https://dilinkappoversea-eu.byd.auto

| Endpoint (path) | Purpose | Implementation |
|---|---|---|
| `/app/account/login` | Authentication | `src/pybyd/_api/login.py` |
| `/app/account/getAllListByUserId` | Vehicle list | `src/pybyd/_api/_common.py` |
| `/vehicleInfo/vehicle/vehicleRealTimeRequest` | Realtime trigger | `src/pybyd/_api/realtime.py` |
| `/vehicleInfo/vehicle/vehicleRealTimeResult` | Realtime poll | `src/pybyd/_api/realtime.py` |
| `/control/getStatusNow` | HVAC status | `src/pybyd/_api/hvac.py` |
| `/control/getGpsInfo` | GPS trigger | `src/pybyd/_api/gps.py` |
| `/control/getGpsInfoResult` | GPS poll | `src/pybyd/_api/gps.py` |
| `/control/smartCharge/homePage` | Charging status | `src/pybyd/_api/charging.py` |
| `/vehicleInfo/vehicle/getEnergyConsumption` | Energy consumption | `src/pybyd/_api/energy.py` |
| `/vehicle/vehicleswitch/verifyControlPassword` | Verify remote-control PIN/password | `src/pybyd/_api/control.py` |
| `/control/remoteControl` | Remote control trigger | `src/pybyd/_api/control.py` |
| `/control/remoteControlResult` | Remote control poll | `src/pybyd/_api/control.py` |

---

## Status labels used below

- confirmed: verified by live captures
- unconfirmed: plausible but not verified yet
- conflicting: observed data contradicts the assumed meaning

Each endpoint section has a main table listing **parsed** fields (extracted into typed Python model attributes)
and, where applicable, a separate "Unparsed fields (raw only)" table listing fields kept only in the `raw` dict.

---

## MQTT events

pyBYD consumes decrypted MQTT events from `oversea/res/<userId>`.

- `vehicleInfo`
	- Source: `payload.data.respondData`
	- Contains full realtime telemetry (same camelCase dict as HTTP poll).
	- Delivered after `vehicleRealTimeRequest` trigger and periodically.
	- Parsed as `VehicleRealtimeData` and forwarded via the `on_vehicle_info` callback.
- `remoteControl`
	- Source: `payload.data.respondData`
	- Parsed using the same immediate/polled rules as HTTP control parser.
	- Used for MQTT-first command completion with HTTP polling fallback.

Unknown/unanticipated MQTT envelopes are ignored and logged at debug level.

## Parsing rules (current code)

The tables below describe field names and observed meanings, but pyBYD also normalizes values while parsing:

- **Alias convention**: all models use `alias_generator=to_camel` from Pydantic. The Python field name is always the snake_case version of the API camelCase key (e.g. `elecPercent` → `elec_percent`). Where the API key doesn't follow `to_camel(field_name)`, a `_KEY_ALIASES` dict normalises incoming keys in a before-validator (e.g. `abs` → `abs_warning`, `time` → `timestamp`, `leftFrontTirepressure` → `left_front_tire_pressure`, `backCover` → `trunk_lid`). The "Python field" column in the tables below is kept for reference but can be derived mechanically from the API field name.
- Numeric parsing: most numeric fields accept `int`, `float`, or numeric strings; `None` and `""` become `None`. NaN becomes `None`. Coercion is handled via `CoercedFloat` / `CoercedInt` annotated types.
- Enum parsing: enum fields use `BeforeValidator(partial(coerce_enum, ...))`. If the integer code is unknown, the raw integer is kept.
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
<<<<<<< HEAD
| State | `vehicleState` | `vehicle_state` | `VehicleState \| int` | 0=on (confirmed), 1=unknown_1 (unconfirmed), 2=off (confirmed) |
| State | `requestSerial` | `request_serial` | `str \| None` | poll serial token |
| Battery | `elecPercent` | `elec_percent` | `float \| None` | SOC 0-100 (confirmed) |
| Battery | `powerBattery` | `power_battery` | `float \| None` | alternative SOC field (unconfirmed) |
| Range | `enduranceMileage` | `endurance_mileage` | `float \| None` | estimated range in km (confirmed) |
| Range | `evEndurance` | `ev_endurance` | `float \| None` | alternative range field (unconfirmed) |
| Range | `enduranceMileageV2` | `endurance_mileage_v2` | `float \| None` | range v2 (unconfirmed) |
| Range | `enduranceMileageV2Unit` | `endurance_mileage_v2_unit` | `str \| None` | "km" or "--" when unavailable (confirmed) |
| Odometer | `totalMileage` | `total_mileage` | `float \| None` | km (confirmed) |
| Odometer | `totalMileageV2` | `total_mileage_v2` | `float \| None` | km (unconfirmed) |
| Odometer | `totalMileageV2Unit` | `total_mileage_v2_unit` | `str \| None` | "km" (confirmed) |
| Driving | `speed` | `speed` | `float \| None` | km/h (confirmed) |
| Driving | `powerGear` | `power_gear` | `PowerGear \| int \| None` | 1=parked (confirmed), 3=drive (confirmed), value `0` observed in stale/unready snapshots |
| Climate | `tempInCar` | `temp_in_car` | `float \| None` | interior temp in C; -129 means unavailable (confirmed) |
| Climate | `mainSettingTemp` | `main_setting_temp` | `int \| None` | cabin set temperature, integer (confirmed) |
| Climate | `mainSettingTempNew` | `main_setting_temp_new` | `float \| None` | cabin set temperature, precise C (unconfirmed) |
| Climate | `airRunState` | `air_run_state` | `AirCirculationMode \| int \| None` | 0=external (legacy mapping), 1=internal recirculation (confirmed), 2=outside air / fresh (confirmed) |
| Seats | `mainSeatHeatState` | `main_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off, 1=inactive (confirmed), 2=low, 3=high (confirmed) |
| Seats | `mainSeatVentilationState` | `main_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatHeatState` | `copilot_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatVentilationState` | `copilot_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `steeringWheelHeatState` | `steering_wheel_heat_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed), 1 observed (unconfirmed), 2/3 possible depending on vehicle |
| Seats | `lrSeatHeatState` | `lr_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| Seats | `lrSeatVentilationState` | `lr_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| Seats | `rrSeatHeatState` | `rr_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| Seats | `rrSeatVentilationState` | `rr_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| Charging | `chargingState` | `charging_state` | `ChargingState \| int` | -1=disconnected (confirmed), 0=not charging (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `chargeState` | `charge_state` | `ChargingState \| int \| None` | -1=disconnected (confirmed), 1=charging (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `waitStatus` | `wait_status` | `int \| None` | charge wait status (unconfirmed) |
| Charging | `fullHour` | `full_hour` | `int \| None` | hours to full; -1 means not available (confirmed) |
| Charging | `fullMinute` | `full_minute` | `int \| None` | minutes to full; -1 means not available (confirmed) |
| Charging | `remainingHours` | `remaining_hours` | `int \| None` | remaining hours; -1 means not available (confirmed) |
| Charging | `remainingMinutes` | `remaining_minutes` | `int \| None` | remaining minutes; -1 means not available (confirmed) |
| Charging | `bookingChargeState` | `booking_charge_state` | `int \| None` | scheduled charging state; 0=off (confirmed) |
| Charging | `bookingChargingHour` | `booking_charging_hour` | `int \| None` | scheduled charge start hour (unconfirmed) |
| Charging | `bookingChargingMinute` | `booking_charging_minute` | `int \| None` | scheduled charge start minute (unconfirmed) |
| Doors | `leftFrontDoor` | `left_front_door` | `DoorOpenState \| None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `rightFrontDoor` | `right_front_door` | `DoorOpenState \| None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `leftRearDoor` | `left_rear_door` | `DoorOpenState \| None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `rightRearDoor` | `right_rear_door` | `DoorOpenState \| None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `trunkLid` / `backCover` | `trunk_lid` | `DoorOpenState \| None` | uses `trunkLid` first, falls back to `backCover`; 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `slidingDoor` | `sliding_door` | `DoorOpenState \| None` | 0=closed (confirmed), 1=open (unconfirmed) |
| Doors | `forehold` | `forehold` | `DoorOpenState \| None` | frunk; 0=closed (confirmed), 1=open (unconfirmed) |
| Locks | `leftFrontDoorLock` | `left_front_door_lock` | `LockState \| int \| None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `rightFrontDoorLock` | `right_front_door_lock` | `LockState \| int \| None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `leftRearDoorLock` | `left_rear_door_lock` | `LockState \| int \| None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `rightRearDoorLock` | `right_rear_door_lock` | `LockState \| int \| None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `slidingDoorLock` | `sliding_door_lock` | `LockState \| int \| None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Windows | `leftFrontWindow` | `left_front_window` | `WindowState \| int \| None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `rightFrontWindow` | `right_front_window` | `WindowState \| int \| None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `leftRearWindow` | `left_rear_window` | `WindowState \| int \| None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `rightRearWindow` | `right_rear_window` | `WindowState \| int \| None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `skylight` | `skylight` | `WindowState \| int \| None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Tires | `leftFrontTirepressure` | `left_front_tire_pressure` | `float \| None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightFrontTirepressure` | `right_front_tire_pressure` | `float \| None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftRearTirepressure` | `left_rear_tire_pressure` | `float \| None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightRearTirepressure` | `right_rear_tire_pressure` | `float \| None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftFrontTireStatus` | `left_front_tire_status` | `int \| None` | 0=normal (confirmed) |
| Tires | `rightFrontTireStatus` | `right_front_tire_status` | `int \| None` | 0=normal (confirmed) |
| Tires | `leftRearTireStatus` | `left_rear_tire_status` | `int \| None` | 0=normal (confirmed) |
| Tires | `rightRearTireStatus` | `right_rear_tire_status` | `int \| None` | 0=normal (confirmed) |
| Tires | `tirePressUnit` | `tire_press_unit` | `TirePressureUnit \| None` | 1=bar (confirmed), 2=psi (unconfirmed), 3=kPa (unconfirmed; seen in stale/unready snapshot) |
| Tires | `tirepressureSystem` | `tirepressure_system` | `int \| None` | TPMS system state (unconfirmed) |
| Tires | `rapidTireLeak` | `rapid_tire_leak` | `int \| None` | 0=no leak (confirmed) |
| Energy | `totalPower` | `total_power` | `float \| None` | total power (unconfirmed) |
| Energy | `totalEnergy` | `total_energy` | `str \| None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumption` | `nearest_energy_consumption` | `str \| None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumptionUnit` | `nearest_energy_consumption_unit` | `str \| None` | unit string (unconfirmed) |
| Energy | `recent50kmEnergy` | `recent_50km_energy` | `str \| None` | string; "--" when unavailable (confirmed) |
| Fuel | `oilEndurance` | `oil_endurance` | `float \| None` | on EV captures: `0` appears in stale/not-ready snapshots (`onlineState=0`, `time=0`), `-1` appears once realtime is ready (likely N/A) |
| Fuel | `oilPercent` | `oil_percent` | `float \| None` | on EV captures: `0` appears in stale/not-ready snapshots (`onlineState=0`, `time=0`), `-1` appears once realtime is ready (likely N/A) |
| Fuel | `totalOil` | `total_oil` | `float \| None` | 0 for EV (confirmed) |
| Warnings | `powerSystem` | `power_system` | `int \| None` | 0=normal (confirmed) |
| Warnings | `engineStatus` | `engine_status` | `int \| None` | 0=off (confirmed) |
| Warnings | `epb` | `epb` | `int \| None` | 0=released (confirmed) |
| Warnings | `eps` | `eps` | `int \| None` | 0=normal (confirmed) |
| Warnings | `esp` | `esp` | `int \| None` | 0=normal (confirmed) |
| Warnings | `abs` | `abs_warning` | `int \| None` | 0=normal (confirmed) |
| Warnings | `svs` | `svs` | `int \| None` | 0=normal (confirmed) |
| Warnings | `srs` | `srs` | `int \| None` | 0=normal (confirmed) |
| Warnings | `ect` | `ect` | `int \| None` | 0=normal (confirmed) |
| Warnings | `ectValue` | `ect_value` | `int \| None` | -1 means not available (confirmed) |
| Warnings | `pwr` | `pwr` | `int \| None` | 2 observed (unconfirmed) |
| Features | `sentryStatus` | `sentry_status` | `int \| None` | 0=off (unconfirmed), 1=on (unconfirmed), 2 observed (unconfirmed) |
| Features | `batteryHeatState` | `battery_heat_state` | `int \| None` | 0=off (confirmed) |
| Features | `chargeHeatState` | `charge_heat_state` | `int \| None` | 0=off (confirmed) |
| Features | `upgradeStatus` | `upgrade_status` | `int \| None` | 0=none (confirmed) |
| Metadata | `time` | `timestamp` | `int \| None` | epoch seconds (confirmed) |
=======
| State | `vehicleState` | `vehicle_state` | `VehicleState | int` | 0=on (confirmed), 1=unknown_1 (unconfirmed), 2=off (confirmed) |
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
| Driving | `powerGear` | `power_gear` | `PowerGear | int | None` | 1=parked (confirmed), 3=drive (confirmed), value `0` observed in stale/unready snapshots |
| Climate | `tempInCar` | `temp_in_car` | `float | None` | interior temp in C; -129 means unavailable (confirmed) |
| Climate | `mainSettingTemp` | `main_setting_temp` | `int | None` | cabin set temperature, integer (confirmed) |
| Climate | `mainSettingTempNew` | `main_setting_temp_new` | `float | None` | cabin set temperature, precise C (unconfirmed) |
| Climate | `airRunState` | `air_run_state` | `AirCirculationMode | int | None` | 0=external (legacy mapping), 1=internal recirculation (confirmed), 2=outside air / fresh (confirmed) |
| Seats | `mainSeatHeatState` | `main_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 1=inactive (confirmed), 2=low, 3=high (confirmed) |
| Seats | `mainSeatVentilationState` | `main_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatHeatState` | `copilot_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `copilotSeatVentilationState` | `copilot_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off, 2=low, 3=high (confirmed) |
| Seats | `steeringWheelHeatState` | `steering_wheel_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed), 1 observed (unconfirmed), 2/3 possible depending on vehicle |
| Seats | `lrSeatHeatState` | `lr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `lrSeatVentilationState` | `lr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `rrSeatHeatState` | `rr_seat_heat_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Seats | `rrSeatVentilationState` | `rr_seat_ventilation_state` | `SeatHeatVentState | int | None` | 0=off (confirmed) |
| Charging | `chargingState` | `charging_state` | `ChargingState | int` | -1=disconnected (confirmed), 0=not charging (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `chargeState` | `charge_state` | `ChargingState | int | None` | -1=disconnected (confirmed), 1=charging (confirmed), 15=gun plugged in not charging (confirmed) |
| Charging | `waitStatus` | `wait_status` | `int | None` | charge wait status (unconfirmed) |
| Charging | `fullHour` | `full_hour` | `int | None` | hours to full; -1 means not available (confirmed) |
| Charging | `fullMinute` | `full_minute` | `int | None` | minutes to full; -1 means not available (confirmed) |
| Charging | `remainingHours` | `remaining_hours` | `int | None` | remaining hours; -1 means not available (confirmed) |
| Charging | `remainingMinutes` | `remaining_minutes` | `int | None` | remaining minutes; -1 means not available (confirmed) |
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
| Locks | `leftFrontDoorLock` | `left_front_door_lock` | `LockState | int | None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `rightFrontDoorLock` | `right_front_door_lock` | `LockState | int | None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `leftRearDoorLock` | `left_rear_door_lock` | `LockState | int | None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `rightRearDoorLock` | `right_rear_door_lock` | `LockState | int | None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Locks | `slidingDoorLock` | `sliding_door_lock` | `LockState | int | None` | 2=locked (confirmed), 1=unlocked (confirmed), value `0` seen in stale/unready snapshots |
| Windows | `leftFrontWindow` | `left_front_window` | `WindowState | int | None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `rightFrontWindow` | `right_front_window` | `WindowState | int | None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `leftRearWindow` | `left_rear_window` | `WindowState | int | None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `rightRearWindow` | `right_rear_window` | `WindowState | int | None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Windows | `skylight` | `skylight` | `WindowState | int | None` | 1=closed (confirmed), 2=open (unconfirmed), value `0` seen in stale/unready snapshots |
| Tires | `leftFrontTirepressure` | `left_front_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightFrontTirepressure` | `right_front_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftRearTirepressure` | `left_rear_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `rightRearTirepressure` | `right_rear_tire_pressure` | `float | None` | pressure value in unit given by `tirePressUnit` (confirmed) |
| Tires | `leftFrontTireStatus` | `left_front_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `rightFrontTireStatus` | `right_front_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `leftRearTireStatus` | `left_rear_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `rightRearTireStatus` | `right_rear_tire_status` | `int | None` | 0=normal (confirmed) |
| Tires | `tirePressUnit` | `tire_press_unit` | `TirePressureUnit | None` | 1=bar (confirmed), 2=psi (unconfirmed), 3=kPa (unconfirmed; seen in stale/unready snapshot) |
| Tires | `tirepressureSystem` | `tirepressure_system` | `int | None` | TPMS system state (unconfirmed) |
| Tires | `rapidTireLeak` | `rapid_tire_leak` | `int | None` | 0=no leak (confirmed) |
| Energy | `totalPower` | `total_power` | `float | None` | total power (unconfirmed) |
| Energy | `gl` | `gl` | `float | None` | instantaneous battery power flow in W; positive=charging, negative=discharging (confirmed) |
| Energy | `totalEnergy` | `total_energy` | `str | None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumption` | `nearest_energy_consumption` | `str | None` | string; "--" when unavailable (confirmed) |
| Energy | `nearestEnergyConsumptionUnit` | `nearest_energy_consumption_unit` | `str | None` | unit string (unconfirmed) |
| Energy | `recent50kmEnergy` | `recent_50km_energy` | `str | None` | string; "--" when unavailable (confirmed) |
| Fuel | `oilEndurance` | `oil_endurance` | `float | None` | on EV captures: `0` appears in stale/not-ready snapshots (`onlineState=0`, `time=0`), `-1` appears once realtime is ready (likely N/A) |
| Fuel | `oilPercent` | `oil_percent` | `float | None` | on EV captures: `0` appears in stale/not-ready snapshots (`onlineState=0`, `time=0`), `-1` appears once realtime is ready (likely N/A) |
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
>>>>>>> 633a77b (downstream fixes)

### Unparsed fields (raw only)

These fields are present in the API response but not extracted into `VehicleRealtimeData` model attributes.
They are accessible via the `raw` dict.

| Group | API field | Observed value | Notes |
|---|---|---|---|
| Seats | `lrThirdHeatState` | `0` | third-row left seat heat (unconfirmed) |
| Seats | `lrThirdVentilationState` | `0` | third-row left seat ventilation (unconfirmed) |
| Seats | `rrThirdHeatState` | `0` | third-row right seat heat (unconfirmed) |
| Seats | `rrThirdVentilationState` | `0` | third-row right seat ventilation (unconfirmed) |
<<<<<<< HEAD
| Charging | `rate` | `-999`, `-9`, `0` | charging current in ampere (unconfirmed), -999 if not charging (unconfirmed) |
=======
| Charging | `rate` | `-999`, `-9`, `0` | possibly `gl / 1000` in kW with inverted sign; sentinel values suggest unreliable when data not ready (unconfirmed) |
>>>>>>> 633a77b (downstream fixes)
| Charging | `lessOneMin` | `false` | possibly time-to-full flag (unconfirmed) |
| Energy | `energyConsumption` | `"15.0"` | unknown consumption field, different from `nearestEnergyConsumption` (unconfirmed) |
| Energy | `totalConsumption` | `"16.6度/百公里"` | Chinese-locale total consumption label (confirmed) |
| Energy | `totalConsumptionEn` | `"16.6kW·h/100km"` | English-locale total consumption label (unconfirmed) |
| Fuel | `oilPressureSystem` | `0` | oil pressure system warning (unconfirmed) |
| Warnings | `brakingSystem` | `0` | braking system warning (unconfirmed) |
| Warnings | `chargingSystem` | `0` | charging system warning (unconfirmed) |
| Warnings | `steeringSystem` | `0` | steering system warning (unconfirmed) |
| Warnings | `okLight` | `0` | OK/ready indicator light (unconfirmed) |
| Features | `repairModeSwitch` | `"0"` | repair/service mode flag (unconfirmed) |
| Metadata | `vehicleTimeZone` | `"Europe/Rome"` | vehicle configured timezone (unconfirmed) |
| Other | `powerBatteryConnection` | `-1`, `0` | battery connectivity indicator (unconfirmed) |
<<<<<<< HEAD
| Other | `gl` | `-29.8`, `9788.8`, `0` | battery power in watts (unconfirmed), positive for charging, negative for discharing |
=======
>>>>>>> 633a77b (downstream fixes)
| Other | `ins` | `-1` | unknown (unconfirmed) |

---

## HVAC / climate status

URL: https://dilinkappoversea-eu.byd.auto/control/getStatusNow

Model: `HvacStatus`

Parser: `src/pybyd/_api/hvac.py`

Response wraps data under the `statusNow` key.

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `acSwitch` | `ac_switch` | `AcSwitch \| int \| None` | 0=off, 1=on (confirmed) |
| `status` | `status` | `HvacOverallStatus \| int \| None` | overall HVAC status; `2` observed while A/C active (confirmed) |
| `airConditioningMode` | `air_conditioning_mode` | `AirConditioningMode \| int \| None` | mode code; `1` observed (confirmed) |
| `windMode` | `wind_mode` | `HvacWindMode \| int \| None` | fan mode code; `3` observed (confirmed) |
| `windPosition` | `wind_position` | `HvacWindPosition \| int \| None` | airflow direction (unconfirmed) |
| `cycleChoice` | `cycle_choice` | `AirCirculationMode \| int \| None` | `2` observed in live capture (confirmed); exact mapping still unconfirmed |
| `mainSettingTemp` | `main_setting_temp` | `float \| None` | set temp integer on BYD scale (confirmed) |
| `mainSettingTempNew` | `main_setting_temp_new` | `float \| None` | set temp C (confirmed) |
| `copilotSettingTemp` | `copilot_setting_temp` | `float \| None` | passenger set temp (confirmed) |
| `copilotSettingTempNew` | `copilot_setting_temp_new` | `float \| None` | passenger set temp C (confirmed) |
| `tempInCar` | `temp_in_car` | `float \| None` | interior C; -129 means unavailable (confirmed) |
| `tempOutCar` | `temp_out_car` | `float \| None` | exterior C (confirmed) |
| `whetherSupportAdjustTemp` | `whether_support_adjust_temp` | `int \| None` | 1=supported (confirmed) |
| `frontDefrostStatus` | `front_defrost_status` | `int \| None` | `1` observed (confirmed), likely on |
| `electricDefrostStatus` | `electric_defrost_status` | `int \| None` | `0` observed (confirmed) |
| `wiperHeatStatus` | `wiper_heat_status` | `int \| None` | `0` observed (confirmed) |
| `mainSeatHeatState` | `main_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off, 1=inactive (confirmed), 2=low, 3=high (confirmed) |
| `mainSeatVentilationState` | `main_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off, 2=low, 3=high (confirmed) |
| `copilotSeatHeatState` | `copilot_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off, 1=inactive (confirmed), 2=low, 3=high (confirmed) |
| `copilotSeatVentilationState` | `copilot_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off, 2=low, 3=high (confirmed) |
| `steeringWheelHeatState` | `steering_wheel_heat_state` | `StearingWheelHeat \| int \| None` | 0=off (confirmed), 1 observed (confirmed) |
| `lrSeatHeatState` | `lr_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| `lrSeatVentilationState` | `lr_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| `rrSeatHeatState` | `rr_seat_heat_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| `rrSeatVentilationState` | `rr_seat_ventilation_state` | `SeatHeatVentState \| int \| None` | 0=off (confirmed) |
| `rapidIncreaseTempState` | `rapid_increase_temp_state` | `int \| None` | 0=off (confirmed) |
| `rapidDecreaseTempState` | `rapid_decrease_temp_state` | `int \| None` | 0=off (confirmed) |
| `refrigeratorState` | `refrigerator_state` | `int \| None` | 0=off (confirmed) |
| `refrigeratorDoorState` | `refrigerator_door_state` | `int \| None` | 0=closed (confirmed); `-1` also observed (likely unsupported on some vehicles) |
| `refrigeratorTemp` | `refrigerator_temp` | `float \| None` | refrigerator temperature; `-1` when unsupported (unconfirmed) |
| `lrThirdHeatState` | `lr_third_heat_state` | `SeatHeatVentState \| None` | third-row left seat heat (unconfirmed) |
| `lrThirdVentilationState` | `lr_third_ventilation_state` | `SeatHeatVentState \| None` | third-row left seat ventilation (unconfirmed) |
| `rrThirdHeatState` | `rr_third_heat_state` | `SeatHeatVentState \| None` | third-row right seat heat (unconfirmed) |
| `rrThirdVentilationState` | `rr_third_ventilation_state` | `SeatHeatVentState \| None` | third-row right seat ventilation (unconfirmed) |
| `airTempLevel` | `air_temp_level` | `int \| None` | air temperature level code (unconfirmed) |
| `airConditionTempRange` | `air_condition_temp_range` | `int \| None` | A/C temperature range setting (unconfirmed) |
| `pm` | `pm` | `float \| None` | PM2.5 value; `0` observed (confirmed) |
| `pm25StateOutCar` | `pm25_state_out_car` | `float \| None` | outside PM2.5 state; `0` observed (confirmed) |

### Unparsed fields (raw only)

These fields are present in the API response but not extracted into `HvacStatus` model attributes.
They are accessible via the `raw` dict.

| API field | Observed value | Notes |
|---|---|---|
| `frontAirSumPattern` | `0` | front air distribution pattern (unconfirmed) |
| `temp` | `0` | unknown temperature field (unconfirmed) |
| `firstWind` | `0` | fan speed level 1 (unconfirmed) |
| `secondWind` | `0` | fan speed level 2 (unconfirmed) |
| `firstWarm` | `0` | heating level 1 (unconfirmed) |
| `secondWarm` | `0` | heating level 2 (unconfirmed) |
| `timeChoice` | `1` | timer/duration selection (unconfirmed) |

---

## Charging status

URL: https://dilinkappoversea-eu.byd.auto/control/smartCharge/homePage

Model: `ChargingStatus`

Parser: `src/pybyd/_api/charging.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | VIN |
| `soc` | `soc` | `int \| None` | SOC 0-100 (confirmed) |
| `chargingState` | `charging_state` | `int \| None` | 15 means not charging (confirmed); other active charging values not captured yet |
| `connectState` | `connect_state` | `int \| None` | 0=not connected (confirmed), 1=connected (confirmed) |
| `waitStatus` | `wait_status` | `int \| None` | 0 (confirmed) |
| `fullHour` | `full_hour` | `int \| None` | -1 means not available (confirmed) |
| `fullMinute` | `full_minute` | `int \| None` | -1 means not available (confirmed) |
| `updateTime` | `update_time` | `int \| None` | epoch seconds (confirmed) |

### Unparsed fields (raw only)

These fields are present in the API response but not extracted into `ChargingStatus` model attributes.
They are accessible via the `raw` dict.

| API field | Observed value | Notes |
|---|---|---|
| `vehicleTimeZone` | `"Europe/Rome"` | vehicle configured timezone (unconfirmed) |
| `smartJourneyDto` | nested object | journey scheduling fields: `useVehicleTime`, `chargeWay`, `startDiscountPrice`, `endDiscountPrice`, `status`, etc. (unconfirmed) |
| `smartChargeDto` | nested object | smart charge schedule fields: `startChargeTime`, `endChargeTime`, `chargeWay`, `exeTime`, `status`, etc. (unconfirmed) |

---

## GPS / location

URL (trigger): https://dilinkappoversea-eu.byd.auto/control/getGpsInfo

URL (poll): https://dilinkappoversea-eu.byd.auto/control/getGpsInfoResult

Model: `GpsInfo`

Parser: `src/pybyd/_api/gps.py`

| API field (aliases) | Python field | Type | Values / notes |
|---|---|---|---|
| `latitude` / `lat` / `gpsLatitude` | `latitude` | `float \| None` | degrees (confirmed) |
| `longitude` / `lng` / `lon` / `gpsLongitude` | `longitude` | `float \| None` | degrees (confirmed) |
| `speed` / `gpsSpeed` | `speed` | `float \| None` | km/h (unconfirmed) |
| `direction` / `heading` / `course` | `direction` | `float \| None` | degrees 0-360 (confirmed) |
| `gpsTimeStamp` / `gpsTimestamp` / `gpsTime` / `time` / `uploadTime` | `gps_timestamp` | `int \| None` | epoch seconds (confirmed) |
| `requestSerial` | `request_serial` | `str \| None` | poll serial token (confirmed) |

### Unparsed fields (raw only)

These fields are present in the API response but not extracted into `GpsInfo` model attributes.
They are accessible via the `raw` dict.

| API field | Observed value | Notes |
|---|---|---|
| `res` | `2` | response status code (unconfirmed) |

---

## Energy consumption

URL: https://dilinkappoversea-eu.byd.auto/vehicleInfo/vehicle/getEnergyConsumption

Model: `EnergyConsumption`

Parser: `src/pybyd/_api/energy.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | VIN |
| `totalEnergy` | `total_energy` | `float \| None` | string "--" maps to None (confirmed) |
| `avgEnergyConsumption` | `avg_energy_consumption` | `float \| None` | unconfirmed |
| `electricityConsumption` | `electricity_consumption` | `float \| None` | unconfirmed |
| `fuelConsumption` | `fuel_consumption` | `float \| None` | unconfirmed |

Note: when the API returns error `1001`, the client synthesises partial data from the realtime cache.

---

## Vehicle list

URL: https://dilinkappoversea-eu.byd.auto/app/account/getAllListByUserId

Model: `Vehicle`

Parser: `src/pybyd/_api/_common.py`

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `vin` | `vin` | `str` | vehicle identification number (VIN) (confirmed) |
| `modelName` | `model_name` | `str` | vehicle model display name (confirmed) |
| `brandName` | `brand_name` | `str` | brand/manufacturer display name (confirmed) |
| `energyType` | `energy_type` | `str` | propulsion type code (`"0"` = EV) (confirmed) |
| `autoAlias` | `auto_alias` | `str` | user-facing vehicle alias; empty string when missing (confirmed) |
| `autoPlate` | `auto_plate` | `str` | plate/registration label; empty string when missing (confirmed) |
| `picMainUrl` (or `cfPic.picMainUrl`) | `pic_main_url` | `str` | main vehicle image URL; empty string when missing (confirmed) |
| `picSetUrl` (or `cfPic.picSetUrl`) | `pic_set_url` | `str` | alternate/gallery image URL; empty string when missing (confirmed) |
| `outModelType` | `out_model_type` | `str` | marketing model label; empty string when missing (confirmed) |
| `totalMileage` | `total_mileage` | `float \| None` | odometer value (km) (confirmed) |
| `modelId` | `model_id` | `int \| None` | internal model code (confirmed) |
| `carType` | `car_type` | `int \| None` | internal car type/class code (confirmed) |
| `defaultCar` | `default_car` | `bool` | default vehicle flag (`1` -> `True`) (confirmed) |
| `empowerType` | `empower_type` | `int \| None` | user-vehicle relationship type (`2` owner, `-1` shared/delegated user) (confirmed) |
| `permissionStatus` | `permission_status` | `int \| None` | permission level/status code; check `rangeDetailList` for actual granted scopes (confirmed; semantics unconfirmed) |
| `tboxVersion` | `tbox_version` | `str` | telematics box version string; empty string when missing (confirmed) |
| `vehicleState` | `vehicle_state` | `str` | list-level vehicle state code (confirmed; semantics unconfirmed) |
| `autoBoughtTime` | `auto_bought_time` | `int \| None` | purchase timestamp in epoch milliseconds (confirmed) |
| `yunActiveTime` | `yun_active_time` | `int \| None` | cloud activation timestamp in epoch milliseconds (confirmed) |
| `empowerId` | `empower_id` | `int \| None` | account-vehicle empower relationship id (confirmed) |
| `rangeDetailList` | `range_detail_list` | `list[EmpowerRange]` | hierarchical permission scope tree (capability modules/functions) (confirmed) |

### Unparsed fields (raw only)

These fields are present in the API response but not extracted into `Vehicle` model attributes.
They are accessible via the `raw` dict.

| API field | Observed value | Notes |
|---|---|---|
| `bluetoothInfo` | `null` | Bluetooth pairing info (unconfirmed) |
| `brandId` | `0` | internal brand identifier (unconfirmed) |
| `cfPic` | nested object | contains `clrCode`, `flag`, `picDoorZipUrl`, `picMainUrl`, `picSetUrl`, `picTireUrl`; `picMainUrl`/`picSetUrl` used as fallback for top-level pic fields |
| `cloudServiceStatue` | `""` | cloud service status (note: API typo "Statue") (unconfirmed) |
| `crmModelId` | `""` | CRM model identifier (unconfirmed) |
| `crmStyleId` | `""` | CRM style identifier (unconfirmed) |
| `dealerRegionCode` | `""` | dealer region code (unconfirmed) |
| `openCloudServiceStatue` | `false` | cloud service activation flag (unconfirmed) |
| `resetPwdState` | `0` | password reset state (unconfirmed) |
| `userManualUrl` | `""` | user manual URL (unconfirmed) |
| `vehicleFunLearnInfo` | nested object | feature capability flags (e.g. `airAccuracy`, `batteryHeating`, `bookingCharge`, `otaUpgrade`, `trunkLearnInfo`, etc.); maps feature availability per vehicle (unconfirmed) |
| `vehicleTimeZone` | `"Europe/Rome"` | vehicle configured timezone (unconfirmed) |
| `vehicleType` | `""` | vehicle type code (unconfirmed) |

---

## Remote control

URL (PIN verify): https://dilinkappoversea-eu.byd.auto/vehicle/vehicleswitch/verifyControlPassword

URL (trigger): https://dilinkappoversea-eu.byd.auto/control/remoteControl

URL (poll): https://dilinkappoversea-eu.byd.auto/control/remoteControlResult

Model: `RemoteControlResult`

Parser: `src/pybyd/_api/control.py`

### PIN verify endpoint (`/vehicle/vehicleswitch/verifyControlPassword`)

Observed request inner payload keys:

| API field | Type | Values / notes |
|---|---|---|
| `commandPwd` | `str` | MD5 uppercase hex of 6-digit control PIN (confirmed) |
| `deviceType` | `str` | e.g. `"0"` |
| `functionType` | `str` | `"remoteControl"` |
| `imeiMD5` | `str` | device identifier hash |
| `networkType` | `str` | e.g. `"wifi"` |
| `random` | `str` | random token |
| `timeStamp` | `str` | epoch milliseconds |
| `version` | `str` | app inner version |
| `vin` | `str` | vehicle VIN |

Observed behavior in pyBYD:

- pyBYD sends remote-control commands directly with `commandPwd` and relies on API responses for success/failure.
- `verify_control_password(...)` is available as an explicit helper call but is not required before issuing commands.

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

### Control params (`controlParamsMap`)

Some remote commands accept a command-specific parameter object sent as the
``controlParamsMap`` field in the *inner* payload. The API expects
``controlParamsMap`` to be a **JSON-encoded string** (not a nested object).

pyBYD provides typed builders for these maps:

- `ClimateStartParams` (for `OPENAIR`)
- `ClimateScheduleParams` (for `BOOKINGAIR`)
- `SeatClimateParams` (for `VENTILATIONHEATING`, mapping still being verified)
- `BatteryHeatParams` (for `BATTERYHEAT`, key name still being verified)

Known/used HVAC keys for `OPENAIR`/`BOOKINGAIR` (confirmed by integration usage
and live captures; value meanings are still partly unconfirmed):

| BYD key | Python arg/model field | Notes |
|---|---|---|
| `mainSettingTemp` | `temperature` / `temperature_c` | temperature is BYD scale 1-17 (15-31°C); pyBYD can convert from °C |
| `copilotSettingTemp` | `copilot_temperature` / `copilot_temperature_c` | passenger temperature scale |
| `timeSpan` | `time_span` | duration code: 1=10m, 2=15m, 3=20m, 4=25m, 5=30m (pyBYD accepts either code or 10/15/20/25/30 minutes and normalizes) |
| `cycleMode` | `cycle_mode` | recirculation code (unconfirmed) |
| `windLevel` | `wind_level` | fan speed code (unconfirmed) |
| `remoteMode` | `remote_mode` | remote mode code (unconfirmed) |
| `airAccuracy` | `air_accuracy` | air quality/accuracy code (unconfirmed) |
| `airConditioningMode` | `air_conditioning_mode` | A/C mode code (unconfirmed) |
| `bookingId` | `booking_id` | schedule only |
| `bookingTime` | `booking_time` | schedule only (epoch seconds) |

### Result fields

| API field | Python field | Type | Values / notes |
|---|---|---|---|
| `controlState` | `control_state` | `ControlState` | 0=pending, 1=success, 2=failure (unconfirmed). If `res` is present instead, pyBYD maps `res==2` to success. |
| `requestSerial` | `request_serial` | `str \| None` | poll serial token (unconfirmed) |
| `res` | (immediate) | `int` | 2 observed as success (unconfirmed); mapped to `control_state` via `_normalize_shapes` |

### Control error codes observed

| API code | Meaning | Status | Notes |
|---:|---|---|---|
| `5005` | wrong operation password | confirmed | server reports remaining attempts for the day |
| `5006` | operation password locked for today | confirmed | cloud control locked after repeated wrong PIN attempts |
| `6024` | previous command in progress / rate-limited | confirmed | pyBYD retries trigger request; can recur for unsupported/stuck commands |
| `1001` | command/endpoint not supported (service exception) | confirmed | pyBYD now classifies as endpoint not supported |

### BATTERY_HEAT support notes

- Shared-account permission set observed: `Keys and control > Basic control` (codes `2` + `21`) only.
- For this permission profile, `BATTERYHEAT` repeatedly returned `6024` and never produced a successful control result.
- pyBYD now treats this as unsupported for shared `Basic control` profiles and raises endpoint-not-supported early for `set_battery_heat(...)`.

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
| `VehicleState` | 2 | `UNKNOWN_2` | confirmed | observed in realtime payloads |
| `ChargingState` | -1 | `DISCONNECTED` | confirmed | |
| `ChargingState` | 0 | `NOT_CHARGING` | confirmed | |
| `ChargingState` | 15 | `CONNECTED` | confirmed | gun plugged in, charging not active |
| `ChargingState` | 1 | `CHARGING` | confirmed | charging; observed on `chargeState` |
| `PowerGear` | 0 | `UNKNOWN` | confirmed | observed in stale/unready snapshot |
| `PowerGear` | 1 | `PARKED` | confirmed | |
| `PowerGear` | 3 | `DRIVE` | confirmed | |
| `SeatHeatVentState` | 0 | `OFF` | confirmed | |
| `SeatHeatVentState` | 2 | `LOW` | confirmed | |
| `SeatHeatVentState` | 3 | `HIGH` | confirmed | |
| `SeatHeatVentState` | 1 | `INACTIVE_1` | confirmed | observed inactive/available state |
| `AirCirculationMode` | 0 | `EXTERNAL` | confirmed | |
| `AirCirculationMode` | 1 | `INTERNAL` | confirmed | |
| `AirCirculationMode` | 2 | `OUTSIDE_FRESH_2` | confirmed | outside air / fresh |
| `TirePressureUnit` | 1 | `BAR` | confirmed | |
| `TirePressureUnit` | 2 | `PSI` | unconfirmed | |
| `TirePressureUnit` | 3 | `KPA` | unconfirmed | |
| `WindowState` | 1 | `CLOSED` | confirmed | |
| `WindowState` | 2 | `OPEN` | unconfirmed | |
| `WindowState` | 0 | `UNKNOWN` | confirmed | observed in stale/unready snapshot |
| `DoorOpenState` | 0 | `CLOSED` | confirmed | |
| `DoorOpenState` | 1 | `OPEN` | unconfirmed | |
| `LockState` | 2 | `LOCKED` | confirmed | |
| `LockState` | 1 | `UNLOCKED` | confirmed | |
| `LockState` | 0 | `UNKNOWN` | confirmed | observed in stale/unready snapshot |
| `ControlState` | 0 | `PENDING` | unconfirmed | |
| `ControlState` | 1 | `SUCCESS` | unconfirmed | |
| `ControlState` | 2 | `FAILURE` | unconfirmed | |