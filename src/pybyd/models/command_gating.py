"""Command gating rules and verdicts for remote-control preflight checks.

Mapping contract:
- This module is the command-level source of truth: `RemoteCommand` -> required `functionNo` values.
- Gating is strict and functionNo-only: if any required value for a gate is present, it is supported.

How to add support:
1) Add/extend a rule in `_COMMAND_GATE_RULES` for the command/gate variant.
2) If it is a seat variant, ensure `_SEAT_GATE_BY_CHAIR_TYPE` routes the right `chairType`.
3) Add/update tests in `tests/test_command_gating.py` and `tests/test_client_command_gating.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import Field

from pybyd.models._base import BydBaseModel
from pybyd.models.control import RemoteCommand

if TYPE_CHECKING:
    from pybyd.models.latest_config import VehicleCapabilities


class CommandGateRule(BydBaseModel):
    """Canonical command gate definition.

    ``required_function_nos`` is the coarse gate from ``cfFixedList``.
    ``required_learn_info_keys`` is the optional finer gate from
    ``getVehicles.vehicleFunLearnInfo``: when set, at least one listed
    key must resolve to a positive value (typically ``1``) for the rule
    to be considered supported.  Rules without ``required_learn_info_keys``
    skip the second check, preserving the current behaviour.
    """

    gate_id: str
    command: RemoteCommand
    required_function_nos: list[str] = Field(default_factory=list)
    required_learn_info_keys: list[str] = Field(default_factory=list)


class CommandGateVerdict(BydBaseModel):
    """Evaluated support verdict for a command gate."""

    command: RemoteCommand
    gate_id: str
    supported: bool
    reason: str

    required_function_nos: list[str] = Field(default_factory=list)
    matched_function_nos: list[str] = Field(default_factory=list)
    counterpart_function_nos: list[str] = Field(default_factory=list)

    required_learn_info_keys: list[str] = Field(default_factory=list)
    matched_learn_info_keys: list[str] = Field(default_factory=list)


# Canonical command -> functionNo mapping used by both client preflight and reporting.
_COMMAND_GATE_RULES: tuple[CommandGateRule, ...] = (
    CommandGateRule.model_validate(
        {
            "gateId": "lock",
            "command": RemoteCommand.LOCK,
            "requiredFunctionNos": ["1005"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "unlock",
            "command": RemoteCommand.UNLOCK,
            "requiredFunctionNos": ["1006"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "climate",
            "command": RemoteCommand.START_CLIMATE,
            "requiredFunctionNos": ["1001", "10300001", "1015"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "climate",
            "command": RemoteCommand.STOP_CLIMATE,
            "requiredFunctionNos": ["1001", "10300001", "1015"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "climate_schedule",
            "command": RemoteCommand.SCHEDULE_CLIMATE,
            "requiredFunctionNos": ["1001", "10300001", "1015"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "find_car",
            "command": RemoteCommand.FIND_CAR,
            "requiredFunctionNos": ["1007"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "flash_lights",
            "command": RemoteCommand.FLASH_LIGHTS,
            "requiredFunctionNos": ["1008"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "close_windows",
            "command": RemoteCommand.CLOSE_WINDOWS,
            "requiredFunctionNos": ["1026"],
        }
    ),
    # Open windows: ``1026`` from cfFixedList is present on cars that
    # support either direction, but only a subset can actually OPEN
    # remotely (others only support CLOSE).  ``openWindowLearnInfo`` /
    # ``openWindow499LearnInfo`` on ``vehicleFunLearnInfo`` flip to ``1``
    # only on the open-capable VINs — confirmed live on a Sealion 7 EU
    # 2024 where both are ``1`` and OPENWINDOW cracks the windows to
    # ~10 % vent, vs. jkaberg's car where 1026 is present but the
    # command had no physical effect (Issue #47).
    CommandGateRule.model_validate(
        {
            "gateId": "open_windows",
            "command": RemoteCommand.OPEN_WINDOWS,
            "requiredFunctionNos": ["1026"],
            "requiredLearnInfoKeys": ["openWindowLearnInfo", "openWindow499LearnInfo"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "open_trunk",
            "command": RemoteCommand.OPEN_TRUNK,
            "requiredFunctionNos": ["1020"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "close_trunk",
            "command": RemoteCommand.CLOSE_TRUNK,
            "requiredFunctionNos": ["1021"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "seat_driver",
            "command": RemoteCommand.SEAT_CLIMATE,
            "requiredFunctionNos": ["10030001", "10030002", "10300003"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "seat_passenger",
            "command": RemoteCommand.SEAT_CLIMATE,
            "requiredFunctionNos": ["10030004", "10030005", "10300003"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "steering_wheel_heat",
            "command": RemoteCommand.SEAT_CLIMATE,
            "requiredFunctionNos": ["10030010", "10300004"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "battery_heat",
            "command": RemoteCommand.BATTERY_HEAT,
            "requiredFunctionNos": ["10300002"],
        }
    ),
    CommandGateRule.model_validate(
        {
            # `1012` is the BYD app's `RESERVATIONCHARGING` capability flag —
            # the same gate that controls the smart-charging schedule UI.
            # Cars without it can't accept ``/control/smartCharge/*`` calls.
            "gateId": "start_charge",
            "command": RemoteCommand.START_CHARGE,
            "requiredFunctionNos": ["1012"],
        }
    ),
    CommandGateRule.model_validate(
        {
            # Stopping requires `RESERVATIONCHARGING` (`1012`) and learned
            # support signaled by ``bookingCharge`` in ``vehicleFunLearnInfo``
            # (5 on BEVs, 4 on PHEVs per official app CarConfigRepository.M0).
            "gateId": "stop_charge",
            "command": RemoteCommand.STOP_CHARGE,
            "requiredFunctionNos": ["1012"],
            "requiredLearnInfoKeys": ["bookingCharge"],
        }
    ),
)

# Seat command requires explicit target selection via control_params.chairType.
_SEAT_GATE_BY_CHAIR_TYPE: dict[str, str] = {
    "1": "seat_driver",
    "2": "seat_passenger",
    "5": "steering_wheel_heat",
}


def _require(function_nos: set[str], required_function_nos: list[str]) -> bool:
    return any(function_no in function_nos for function_no in required_function_nos)


def _learn_info_match(
    learn_info: Mapping[str, int] | None,
    required_keys: list[str],
) -> list[str]:
    """Return the subset of ``required_keys`` that resolve to a positive value.

    A missing key, ``0``, or ``-1`` (BYD's "not applicable") count as
    not-present.  Only ``> 0`` is treated as confirmed.
    """
    if not required_keys or learn_info is None:
        return []
    return sorted(key for key in required_keys if (learn_info.get(key) or 0) > 0)


def command_gate_rules() -> tuple[CommandGateRule, ...]:
    """Return canonical command gate rules."""
    return _COMMAND_GATE_RULES


def known_command_function_nos() -> frozenset[str]:
    """Return all functionNos referenced by command rules.

    latest_config uses this set as part of its registered functionNo registry,
    so adding a new command rule automatically participates in coverage checks.
    """
    return frozenset(
        function_no for rule in _COMMAND_GATE_RULES for function_no in rule.required_function_nos if function_no
    )


def _evaluate_rule(
    rule: CommandGateRule,
    capabilities: VehicleCapabilities,
    *,
    function_nos: set[str],
    learn_info: Mapping[str, int] | None = None,
) -> CommandGateVerdict:
    matched_function_nos = sorted([fn for fn in rule.required_function_nos if fn in function_nos])
    counterpart_function_nos = sorted(
        function_no for function_no in rule.required_function_nos if function_no not in matched_function_nos
    )

    fn_supported = _require(function_nos, rule.required_function_nos)

    # Fine gate: only enforced when the rule declares it AND the caller
    # actually provided a ``learn_info`` mapping.  Rules without
    # ``required_learn_info_keys`` keep the original function_no-only
    # contract, so this stays additive.
    matched_learn_info_keys = _learn_info_match(learn_info, rule.required_learn_info_keys)
    learn_info_supported = not rule.required_learn_info_keys or learn_info is None or bool(matched_learn_info_keys)

    supported = fn_supported and learn_info_supported
    if not fn_supported:
        reason = "function_no_missing"
    elif not learn_info_supported:
        reason = "learn_info_missing"
    else:
        reason = "supported"

    return CommandGateVerdict.model_validate(
        {
            "command": rule.command,
            "gateId": rule.gate_id,
            "supported": supported,
            "reason": reason,
            "requiredFunctionNos": rule.required_function_nos,
            "matchedFunctionNos": matched_function_nos,
            "counterpartFunctionNos": counterpart_function_nos,
            "requiredLearnInfoKeys": rule.required_learn_info_keys,
            "matchedLearnInfoKeys": matched_learn_info_keys,
        }
    )


def evaluate_all_command_gates(
    capabilities: VehicleCapabilities,
    *,
    learn_info: Mapping[str, int] | None = None,
) -> list[CommandGateVerdict]:
    """Evaluate all canonical gates for reporting and diagnostics."""
    function_nos = set(capabilities.function_nos)
    return [
        _evaluate_rule(rule, capabilities, function_nos=function_nos, learn_info=learn_info)
        for rule in _COMMAND_GATE_RULES
    ]


def evaluate_command_gate(
    command: RemoteCommand,
    capabilities: VehicleCapabilities,
    *,
    control_params: Mapping[str, object] | None = None,
    learn_info: Mapping[str, int] | None = None,
) -> CommandGateVerdict:
    """Evaluate preflight support for a command against capabilities/functionNos.

    ``learn_info`` is the ``vehicleFunLearnInfo`` dict from ``getVehicles``
    (available on :attr:`pybyd.models.vehicle.Vehicle.vehicle_fun_learn_info`).
    Optional — rules without ``required_learn_info_keys`` ignore it, so
    existing callers keep working unchanged.
    """
    function_nos = set(capabilities.function_nos)
    command_rules = [rule for rule in _COMMAND_GATE_RULES if rule.command == command]

    if command == RemoteCommand.SEAT_CLIMATE:
        chair_type: str | None = None
        if control_params is not None:
            raw_chair_type = control_params.get("chairType")
            if isinstance(raw_chair_type, str):
                chair_type = raw_chair_type
            elif raw_chair_type is not None:
                chair_type = str(raw_chair_type)

        gate_id = _SEAT_GATE_BY_CHAIR_TYPE.get(chair_type or "")
        if gate_id is None:
            all_seat_rules = [rule for rule in command_rules if rule.gate_id in set(_SEAT_GATE_BY_CHAIR_TYPE.values())]
            required_function_nos = sorted(
                {function_no for rule in all_seat_rules for function_no in rule.required_function_nos}
            )
            return CommandGateVerdict.model_validate(
                {
                    "command": command,
                    "gateId": "seat_target",
                    "supported": False,
                    "reason": "seat_target_unknown",
                    "requiredFunctionNos": required_function_nos,
                    "matchedFunctionNos": [],
                    "counterpartFunctionNos": required_function_nos,
                }
            )

        selected_rule = next(rule for rule in command_rules if rule.gate_id == gate_id)
        return _evaluate_rule(selected_rule, capabilities, function_nos=function_nos, learn_info=learn_info)

    selected = command_rules[0]
    return _evaluate_rule(selected, capabilities, function_nos=function_nos, learn_info=learn_info)
