"""Remote control models, parameter builders, and command responses.

Consolidates enums, result models, typed ``controlParamsMap`` payloads,
and lightweight acknowledgement wrappers.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from pydantic.alias_generators import to_camel

from pybyd._constants import DEFAULT_TEMP_OFFSET_C, celsius_to_scale
from pybyd.models._base import BydBaseModel

if TYPE_CHECKING:
    from pybyd.models.hvac import HvacStatus  # noqa: F401
    from pybyd.models.realtime import VehicleRealtimeData  # noqa: F401

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class RemoteCommand(enum.StrEnum):
    """Remote control ``commandType`` values."""

    LOCK = "LOCKDOOR"
    UNLOCK = "OPENDOOR"
    START_CLIMATE = "OPENAIR"
    STOP_CLIMATE = "CLOSEAIR"
    SCHEDULE_CLIMATE = "BOOKINGAIR"
    FIND_CAR = "FINDCAR"
    FLASH_LIGHTS = "FLASHLIGHTNOWHISTLE"
    CLOSE_WINDOWS = "CLOSEWINDOW"
    # Counterpart to CLOSEWINDOW.  Live-verified on Sealion 7: opens
    # the windows to a ~10 % vent crack (not full-drop).  BYD's native
    # ventilation flow; no remote full-drop command is known.
    OPEN_WINDOWS = "OPENWINDOW"
    SEAT_CLIMATE = "VENTILATIONHEATING"
    BATTERY_HEAT = "BATTERYHEAT"
    OPEN_TRUNK = "OPENTRUNK"
    CLOSE_TRUNK = "CLOSETRUNK"
    # `START_CHARGE` / `STOP_CHARGE` are synthetic values (never sent on the
    # wire as a `commandType`).  The on-the-wire smart-charging toggle goes via
    # `/control/smartCharge/changeChargeStatue` with ``status: "1"`` (start) or
    # ``status: "0"`` (stop) rather than `/control/remoteControl`.  We add the
    # enum members so the gating table can express the `functionNo` requirement
    # (``1012``) for callers that introspect command availability.
    START_CHARGE = "SMARTCHARGESTART"
    STOP_CHARGE = "SMARTCHARGESTOP"


class ControlState(enum.IntEnum):
    """Control command execution state."""

    PENDING = 0
    SUCCESS = 1
    FAILURE = 2


# ------------------------------------------------------------------
# Command result
# ------------------------------------------------------------------


class RemoteControlResult(BydBaseModel):
    """Result of a remote control command."""

    control_state: ControlState = Field(default=ControlState.PENDING, validation_alias="controlState")
    success: bool = False
    request_serial: str | None = Field(default=None, validation_alias="requestSerial")

    @model_validator(mode="before")
    @classmethod
    def _normalize_shapes(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        merged = dict(values)

        # BYD ``res`` field (from /remoteControlResult and MQTT acks):
        #   1 = command received / in progress (keep polling)
        #   2 = success (terminal)
        #   other = failure (terminal)
        if "res" in merged and "controlState" not in merged and "control_state" not in merged:
            res_val = int(merged["res"])
            if res_val == 1:
                state = ControlState.PENDING
            elif res_val == 2:
                state = ControlState.SUCCESS
            else:
                state = ControlState.FAILURE
            merged["controlState"] = int(state)
            merged.setdefault("success", state == ControlState.SUCCESS)

        # Standard polled format: {"controlState": 0/1/2, ...}
        if "controlState" in merged and "success" not in merged:
            try:
                state = ControlState(int(merged["controlState"]))
            except ValueError:
                state = ControlState.PENDING
            merged["controlState"] = int(state)
            merged["success"] = state == ControlState.SUCCESS

        return merged


# ------------------------------------------------------------------
# Command acknowledgement responses
# ------------------------------------------------------------------


class CommandAck(BydBaseModel):
    """Generic acknowledgement response for write/toggle endpoints."""

    vin: str = ""
    result: str | None = None
    request_serial: str | None = Field(default=None, validation_alias="requestSerial")

    @model_validator(mode="before")
    @classmethod
    def _coerce_result(cls, values: Any) -> Any:
        """Ensure ``result`` is a string or ``None``."""
        if not isinstance(values, dict):
            return values
        r = values.get("result")
        if r is not None and not isinstance(r, str):
            values = {**values, "result": None}
        return values


class ChargeChangeResult(BydBaseModel):
    """Terminal result of ``/control/smartCharge/changeChargeStatue``.

    Returned by :meth:`BydClient.start_charging` once the change has settled
    — either via the MQTT ``smartCharge`` push that BYD's cloud sends back,
    or via the ``/control/smartCharge/changeResult`` HTTP poll fallback.

    See ``references/changeResult.md`` for the captured payload shape.
    """

    vin: str = ""
    res: int | None = None
    """BYD ``res`` code: ``1`` = pending (still in progress, keep polling),
    ``2`` = success (terminal), other values (e.g. ``3``) = failure."""
    message: str | None = None
    """Locale-dependent human-readable message (e.g. ``"Operation
    successful"``).  Use :attr:`res` for branching, not this string."""
    request_serial: str | None = Field(default=None, validation_alias="requestSerial")
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _capture_raw(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "raw" not in values:
            values = {**values, "raw": {k: v for k, v in values.items() if k != "raw"}}
        return values


class CommandAckEvent(BydBaseModel):
    """Structured MQTT remote-control acknowledgement event.

    ``request_serial`` is the only deterministic correlation key and may be
    ``None`` for diagnostics-only (uncorrelated) MQTT events.
    """

    vin: str = ""
    request_serial: str | None = Field(default=None, validation_alias="requestSerial")
    raw_uuid: str | None = None
    result: str | None = None
    success: bool = False
    timestamp: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_correlated(self) -> bool:
        """Return ``True`` when this event can be matched deterministically."""
        return bool(self.vin and self.request_serial)

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        merged = dict(values)
        serial = merged.get("requestSerial") or merged.get("request_serial")
        if serial is not None and not isinstance(serial, str):
            serial = None

        raw = merged.get("raw")
        if not isinstance(raw, dict):
            raw = {}

        raw_uuid = merged.get("raw_uuid")
        if raw_uuid is not None and not isinstance(raw_uuid, str):
            raw_uuid = None

        timestamp = merged.get("timestamp")
        if timestamp is not None and not isinstance(timestamp, int):
            try:
                timestamp = int(timestamp)
            except (TypeError, ValueError):
                timestamp = None

        result = merged.get("result")
        if result is not None and not isinstance(result, str):
            result = str(result)

        success = merged.get("success")
        if not isinstance(success, bool):
            success = False

        merged["requestSerial"] = serial
        merged["raw_uuid"] = raw_uuid
        merged["timestamp"] = timestamp
        merged["result"] = result
        merged["success"] = success
        merged["raw"] = raw
        return merged


class CommandLifecycleStatus(enum.StrEnum):
    """Lifecycle state for deterministic command ACK correlation."""

    REGISTERED = "registered"
    MATCHED = "matched"
    EXPIRED = "expired"
    UNCORRELATED = "uncorrelated"


class CommandLifecycleEvent(BydBaseModel):
    """Lifecycle event emitted by the client command ACK registry."""

    status: CommandLifecycleStatus
    vin: str = ""
    request_serial: str | None = Field(default=None, validation_alias="requestSerial")
    command: str | None = None
    timestamp: int
    ack: CommandAckEvent | None = None
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_shape(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        merged = dict(values)
        serial = merged.get("requestSerial") or merged.get("request_serial")
        if serial is not None and not isinstance(serial, str):
            serial = None

        command = merged.get("command")
        if command is not None and not isinstance(command, str):
            command = str(command)

        timestamp = merged.get("timestamp")
        if timestamp is None:
            timestamp = 0
        elif not isinstance(timestamp, int):
            try:
                timestamp = int(timestamp)
            except (TypeError, ValueError):
                timestamp = 0

        reason = merged.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)

        merged["requestSerial"] = serial
        merged["command"] = command
        merged["timestamp"] = timestamp
        merged["reason"] = reason
        return merged


class CommandAckDiagnostics(BydBaseModel):
    """Snapshot counters for command ACK registry diagnostics."""

    pending: int = 0
    matched: int = 0
    expired: int = 0
    uncorrelated: int = 0
    pending_by_vin: dict[str, int] = Field(default_factory=dict)


class VerifyControlPasswordResponse(BydBaseModel):
    """Response from the control password verification endpoint."""

    vin: str = ""
    ok: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_ok(cls, values: Any) -> Any:
        """Ensure ``ok`` is a bool or ``None``."""
        if not isinstance(values, dict):
            return values
        v = values.get("ok")
        if v is not None and not isinstance(v, bool):
            values = {**values, "ok": None}
        return values


# ------------------------------------------------------------------
# Control parameter payloads (serialised to ``controlParamsMap``)
# ------------------------------------------------------------------


class ControlParams(BaseModel):
    """Base class for control-parameter models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        str_strip_whitespace=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    def to_control_params_map(self) -> dict[str, Any]:
        """Return a dict that can be JSON-encoded into ``controlParamsMap``."""
        return self.model_dump(by_alias=True, exclude_none=True)


class ClimateStartParams(ControlParams):
    """Parameters for starting HVAC immediately (commandType ``OPENAIR``).

    Temperatures are specified in °C (15-31 by default) and automatically
    converted to BYD's internal scale (1-17) on serialisation.  Set
    ``temp_offset`` for vehicles with a shifted range (see
    :func:`pybyd._constants.temp_offset_from_range`).

    Sensible defaults are provided for all API-required fields so that
    callers only need to supply ``temperature`` and ``time_span``.
    """

    temperature: float | None = None
    """Driver temperature setpoint in °C (15-31 by default)."""

    copilot_temperature: float | None = None
    """Passenger temperature setpoint in °C.  Defaults to *temperature*."""

    temp_offset: float = Field(default=DEFAULT_TEMP_OFFSET_C, exclude=True)
    """°C offset of the vehicle's 1-17 scale (14 → 15-31 °C, 16 → 17-33 °C)."""

    cycle_mode: int = Field(default=2, ge=0)
    """Air recirculation: 1=external (fresh), 2=internal (recirculate)."""

    time_span: int | None = Field(default=None, ge=1, le=5)
    """Run duration code (1=10min, 2=15min, 3=20min, 4=25min, 5=30min)."""

    remote_mode: int = Field(default=4, ge=0)
    """Command mode: 4=immediate start."""

    air_accuracy: int = Field(default=1, ge=0)
    air_conditioning_mode: int = Field(default=1, ge=0)
    wind_level: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_temp_range(self) -> ClimateStartParams:
        for value in (self.temperature, self.copilot_temperature):
            if value is not None:
                celsius_to_scale(value, offset=self.temp_offset)
        return self

    @field_serializer("temperature", "copilot_temperature")
    def _serialize_temp(self, value: float | None) -> int | None:
        return celsius_to_scale(value, offset=self.temp_offset) if value is not None else None

    def to_control_params_map(self) -> dict[str, Any]:
        """Return a dict that can be JSON-encoded into ``controlParamsMap``."""
        data = self.model_dump(by_alias=True, exclude_none=True)
        # Map temperature fields to BYD's expected keys.
        if "temperature" in data:
            data["mainSettingTemp"] = data.pop("temperature")
        if "copilotTemperature" in data:
            data["copilotSettingTemp"] = data.pop("copilotTemperature")
        # Mirror driver temp to copilot when copilot was not explicitly set.
        if "copilotSettingTemp" not in data and "mainSettingTemp" in data:
            data["copilotSettingTemp"] = data["mainSettingTemp"]
        # Immediate mode requires airSet: null in the payload.
        if self.remote_mode == 4:
            data["airSet"] = None
        return data


class ClimateScheduleParams(ClimateStartParams):
    """Parameters for scheduling HVAC (commandType ``BOOKINGAIR``).

    ``remote_mode`` should be:
    - ``1`` = create new schedule
    - ``2`` = modify existing schedule (requires ``booking_id``)
    - ``3`` = remove schedule (requires ``booking_id``)
    """

    remote_mode: int = Field(default=1, ge=1, le=3)
    """Schedule mode: 1=create, 2=modify, 3=remove."""

    booking_id: int | None = Field(default=None, ge=1)
    """Schedule booking ID (required for modify/remove)."""

    booking_time: int | None = Field(default=None, ge=1)
    """Schedule time as epoch seconds."""

    ac_switch: int = Field(default=0, ge=0, le=1)
    """A/C switch (0=off, 1=on).  Usually 0 for schedule creation."""

    wind_mode: int | None = Field(default=None, ge=0)
    """Fan mode (only included on create)."""


class SeatClimateParams(ControlParams):
    """Parameters for seat heating/ventilation (commandType ``VENTILATIONHEATING``).

    Command values use an **inverted** scale compared to status readings::

        1 = high (most powerful)
        2 = low  (least powerful)
        3 = off

    A value of ``0`` means "not applicable / feature absent".

    The ``chair_type`` field indicates which seat/feature the command
    targets:  ``"1"`` = driver, ``"2"`` = copilot, ``"5"`` = steering
    wheel.  ``remote_mode`` is always ``1`` for seat/steering commands.
    """

    # --- Target identification ---
    chair_type: str | None = Field(default=None)
    """Which seat the command targets: "1"=driver, "2"=copilot, "5"=steering wheel."""

    remote_mode: int = Field(default=1, ge=1, le=4)
    """Always ``1`` for seat/steering wheel commands."""

    # --- Driver ---
    main_heat: int = Field(default=3, ge=0, le=3)
    main_ventilation: int = Field(default=0, ge=0, le=3)

    # --- Copilot ---
    copilot_heat: int = Field(default=3, ge=0, le=3)
    copilot_ventilation: int = Field(default=0, ge=0, le=3)

    # --- Rear left ---
    lr_seat_heat_state: int = Field(default=0, ge=0, le=3)
    lr_seat_ventilation_state: int = Field(default=0, ge=0, le=3)
    lr_third_heat_state: int = Field(default=0, ge=0, le=3)
    lr_third_ventilation_state: int = Field(default=0, ge=0, le=3)

    # --- Rear right ---
    rr_seat_heat_state: int = Field(default=0, ge=0, le=3)
    rr_seat_ventilation_state: int = Field(default=0, ge=0, le=3)
    rr_third_heat_state: int = Field(default=0, ge=0, le=3)
    rr_third_ventilation_state: int = Field(default=0, ge=0, le=3)

    # --- Steering wheel ---
    steering_wheel_heat_state: int = Field(default=3, ge=0, le=3)
    """Steering wheel heat: 1=on, 3=off."""

    # chairType → which seat changed.
    _CHAIR_TYPE_FOR_PARAM: ClassVar[dict[str, str]] = {
        "main_heat": "1",
        "main_ventilation": "1",
        "copilot_heat": "2",
        "copilot_ventilation": "2",
        "steering_wheel_heat_state": "5",
    }

    # Mapping from HVAC/realtime status attr → constructor keyword.
    _SEAT_ATTR_TO_PARAM: ClassVar[dict[str, str]] = {
        "main_seat_heat_state": "main_heat",
        "main_seat_ventilation_state": "main_ventilation",
        "copilot_seat_heat_state": "copilot_heat",
        "copilot_seat_ventilation_state": "copilot_ventilation",
        "lr_seat_heat_state": "lr_seat_heat_state",
        "lr_seat_ventilation_state": "lr_seat_ventilation_state",
        "rr_seat_heat_state": "rr_seat_heat_state",
        "rr_seat_ventilation_state": "rr_seat_ventilation_state",
    }

    @classmethod
    def from_current_state(
        cls,
        hvac: HvacStatus | None = None,
        realtime: VehicleRealtimeData | None = None,
    ) -> SeatClimateParams:
        """Build params from current vehicle state.

        The BYD API requires *all* seat climate values to be sent with
        every command.  This factory reads the current state from the
        HVAC status (preferred) with realtime data as fallback, and
        converts each :class:`SeatHeatVentState` to the command scale
        (inverted: HIGH→1, LOW→2, OFF→3).

        Steering wheel heat uses ``1`` = on, ``3`` = off.
        """
        from pybyd.models.realtime import SeatHeatVentState, StearingWheelHeat

        kwargs: dict[str, Any] = {}

        for attr, param in cls._SEAT_ATTR_TO_PARAM.items():
            val = None
            if hvac is not None:
                val = getattr(hvac, attr, None)
            if val is None and realtime is not None:
                val = getattr(realtime, attr, None)
            if isinstance(val, SeatHeatVentState):
                kwargs[param] = val.to_command_level()
            else:
                kwargs[param] = 0

        # Steering wheel heat
        sw_val = None
        if hvac is not None:
            sw_val = getattr(hvac, "steering_wheel_heat_state", None)
        if sw_val is None and realtime is not None:
            sw_val = getattr(realtime, "steering_wheel_heat_state", None)
        if isinstance(sw_val, StearingWheelHeat):
            kwargs["steering_wheel_heat_state"] = sw_val.to_command_level()
        else:
            kwargs["steering_wheel_heat_state"] = 3  # default off

        return cls(**kwargs)

    def with_change(self, param_key: str, value: int) -> SeatClimateParams:
        """Return a copy with *param_key* changed and ``chair_type`` set.

        Automatically determines the correct ``chairType`` from the
        parameter being changed.
        """
        chair = self._CHAIR_TYPE_FOR_PARAM.get(param_key)
        return self.model_copy(update={param_key: value, "chair_type": chair})


class BatteryHeatParams(ControlParams):
    """Parameters for battery heating (commandType ``BATTERYHEAT``)."""

    on: bool = Field(..., serialization_alias="batteryHeatSwitch")

    @field_serializer("on")
    def _serialize_on(self, value: bool) -> int:
        return 1 if value else 0
