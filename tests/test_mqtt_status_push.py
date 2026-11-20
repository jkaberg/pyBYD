"""Tests for MQTT vehicleStatusPush, remoteControlAck, and res=4 handling."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from pybyd._mqtt import MqttEvent, VehicleStatusPushDto, flatten_vehicle_status_push
from pybyd._state_engine import ProjectionSpec, VehicleStateEngine
from pybyd.client import BydClient
from pybyd.config import BydConfig
from pybyd.models.charging import ChargingStatus
from pybyd.models.control import ControlState, RemoteControlResult
from pybyd.models.hvac import HvacOverallStatus, HvacStatus
from pybyd.models.realtime import AirCirculationMode, DoorOpenState, LockState, VehicleRealtimeData
from pybyd.models.vehicle import Vehicle


def test_remote_control_result_treats_res_4_as_success() -> None:
    """Both res=2 and res=4 represent terminal success."""
    result = RemoteControlResult.model_validate({"res": 4, "requestSerial": "abc"})
    assert result.control_state == ControlState.SUCCESS
    assert result.success is True
    assert result.request_serial == "abc"


def test_flatten_vehicle_status_push_extracts_known_dtos() -> None:
    payload = {
        "doorStatusDto": {"leftFrontDoor": 1, "leftFrontDoorLock": 1},
        "can4A505Dto": {"elecPercent": 82},
        "unknownDto": {"foo": "bar"},
        "time": 1795006707,
    }
    flat = flatten_vehicle_status_push(payload)
    assert flat == {
        "leftFrontDoor": 1,
        "leftFrontDoorLock": 1,
        "elecPercent": 82,
        "time": 1795006707,
    }


def test_flatten_vehicle_status_push_empty_when_no_dtos() -> None:
    assert flatten_vehicle_status_push({"time": 1795006707}) == {}


@pytest.mark.asyncio
async def test_state_engine_patch_realtime_preserves_existing_fields_and_merges_raw() -> None:
    engine = VehicleStateEngine("VIN123", Vehicle.model_validate({"vin": "VIN123"}))
    base = VehicleRealtimeData.model_validate(
        {
            "elecPercent": 75,
            "enduranceMileage": 400,
            "tempInCar": 22.5,
            "leftFrontDoor": 0,
            "leftFrontDoorLock": 2,
        }
    )
    await engine.update_realtime(base)

    # Register a projection that expects door unlock (1)
    engine.register_projections(
        [ProjectionSpec(section="realtime", field_name="left_front_door_lock", expected_value=LockState.UNLOCKED)]
    )

    # Include a sentinel (-129 tempInCar and -1 rightFrontDoorLock) to verify it does NOT erase base values
    partial = VehicleRealtimeData.model_validate(
        {
            "leftFrontDoor": 1,
            "leftFrontDoorLock": 1,
            "tempInCar": -129,
            "rightFrontDoorLock": -1,
        }
    )
    await engine.patch_realtime(partial)

    snap = engine.snapshot
    assert snap.realtime is not None
    # Updated fields from partial push
    assert snap.realtime.left_front_door == DoorOpenState.OPEN
    assert snap.realtime.left_front_door_lock == LockState.UNLOCKED
    # Preserved fields from base realtime (not wiped by sentinels or missing keys)
    assert snap.realtime.elec_percent == 75
    assert snap.realtime.endurance_mileage == 400
    assert snap.realtime.temp_in_car == 22.5
    # Raw dictionary merged rather than replaced
    assert snap.realtime.raw["enduranceMileage"] == 400
    assert snap.realtime.raw["leftFrontDoor"] == 1


@pytest.mark.asyncio
async def test_state_engine_patch_skips_uninitialized_base_and_patches_hvac_charging() -> None:
    callbacks: list[int] = []
    engine = VehicleStateEngine(
        "VIN123",
        Vehicle.model_validate({"vin": "VIN123"}),
        on_state_changed=lambda _vin, _snap: callbacks.append(1),
    )

    # Partial push before base is initialized must be ignored
    await engine.patch_realtime(VehicleRealtimeData.model_validate({"elecPercent": 80}))
    assert engine.snapshot.realtime is None
    assert callbacks == []

    # Initialize base sections
    await engine.update_realtime(
        VehicleRealtimeData.model_validate({"elecPercent": 70, "chargeState": 1, "chargePower": 5.1})
    )
    await engine.update_hvac(HvacStatus.model_validate({"status": 2, "mainSettingTempNew": 21.0}))
    await engine.update_charging(
        ChargingStatus.model_validate({"soc": 70, "chargingState": 1, "connectState": 1, "chargePower": 5.1})
    )
    callbacks.clear()

    push = {
        "airStatusDto": {"cycleModel": 2, "airRunState": 1, "mainSettingTempNew": 23.0},
        "chargeStatusDto": {"chargingState": 15, "connectState": 0, "chargeState": 15},
        "can4A505Dto": {"elecPercent": 85},
    }
    flat = flatten_vehicle_status_push(push)
    air = flatten_vehicle_status_push(push, (VehicleStatusPushDto.AIR_STATUS,))
    charge = flatten_vehicle_status_push(push, (VehicleStatusPushDto.CHARGE_STATUS,))
    await engine.patch_status_push(
        realtime=VehicleRealtimeData.model_validate(flat),
        hvac=HvacStatus.model_validate(air),
        charging=ChargingStatus.model_validate(charge),
    )

    # Atomic single rebuild
    assert len(callbacks) == 1
    snap = engine.snapshot
    assert snap.realtime is not None
    assert snap.realtime.elec_percent == 85
    assert snap.realtime.charge_power is None
    assert snap.hvac is not None
    assert snap.hvac.status == HvacOverallStatus.OFF
    assert "elecPercent" not in snap.hvac.raw
    assert snap.hvac.cycle_choice == AirCirculationMode.INTERNAL
    assert snap.hvac.air_run_state == 1
    assert snap.hvac.main_setting_temp_new == 23.0
    assert snap.charging is not None
    assert snap.charging.soc == 70
    assert "cycleModel" not in snap.charging.raw
    assert snap.charging.connect_state == 0
    assert snap.charging.charge_power is None


@pytest.mark.asyncio
async def test_client_on_mqtt_event_routes_vehicle_status_push_and_ack() -> None:
    config = BydConfig(username="user@example.com", password="secret")
    acks: list[str] = []
    client = BydClient(config, on_command_ack=lambda ev: acks.append(ev.event if hasattr(ev, "event") else "ack"))

    car = MagicMock()
    client._cars["VIN123"] = car

    push_event = MqttEvent(
        event="vehicleStatusPush",
        vin="VIN123",
        topic="oversea/res/1",
        payload={
            "event": "vehicleStatusPush",
            "vin": "VIN123",
            "data": {
                "respondData": {
                    "doorStatusDto": {"leftFrontDoor": 1},
                    "can4A505Dto": {"elecPercent": 91},
                    "airStatusDto": {"cycleModel": 2},
                }
            },
        },
    )
    client._on_mqtt_event(push_event)
    car.handle_mqtt_status_push.assert_called_once()
    call = car.handle_mqtt_status_push.call_args
    pushed_rt = call.args[0]
    pushed_hvac = call.kwargs["hvac"]
    pushed_charging = call.kwargs["charging"]
    assert pushed_rt.left_front_door == DoorOpenState.OPEN
    assert pushed_rt.elec_percent == 91
    assert pushed_hvac.cycle_choice == AirCirculationMode.INTERNAL
    assert "leftFrontDoor" not in pushed_hvac.raw
    assert pushed_charging is None

    ack_event = MqttEvent(
        event="remoteControlAck",
        vin="VIN123",
        topic="oversea/res/1",
        payload={
            "event": "remoteControlAck",
            "vin": "VIN123",
            "data": {"uuid": "ser-1", "respondData": {"res": 1, "ackStatus": 1}},
        },
    )
    client._on_mqtt_event(ack_event)
    assert acks == []
    await asyncio.sleep(0)


def test_client_door_only_status_push_leaves_hvac_and_charging_alone() -> None:
    client = BydClient(BydConfig(username="user@example.com", password="secret"))
    car = MagicMock()
    client._cars["VIN123"] = car

    client._on_mqtt_event(
        MqttEvent(
            event="vehicleStatusPush",
            vin="VIN123",
            topic="oversea/res/1",
            payload={
                "event": "vehicleStatusPush",
                "vin": "VIN123",
                "data": {"respondData": {"time": 1700000000, "doorStatusDto": {"leftFrontDoor": 1}}},
            },
        )
    )

    call = car.handle_mqtt_status_push.call_args
    assert call.args[0].left_front_door == DoorOpenState.OPEN
    assert call.kwargs["hvac"] is None
    assert call.kwargs["charging"] is None
