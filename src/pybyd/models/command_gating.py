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
    # remotely (others only support CLOSE).  ``openWindowLearnInfo`` on
    # ``vehicleFunLearnInfo`` is ``1`` only on the open-capable VINs: a
    # Sealion 7 EU 2024 has it at ``1`` and OPENWINDOW cracks the windows
    # to ~10 % vent (#47).  ``openWindow499LearnInfo`` alone is not
    # enough: an ATTO 3 EVO has it at ``1`` with ``openWindowLearnInfo``
    # at ``0``, and there OPENWINDOW never leaves ``res: 1`` and blocks
    # the next command with 6024 for a minute or two (#20).
    CommandGateRule.model_validate(
        {
            "gateId": "open_windows",
            "command": RemoteCommand.OPEN_WINDOWS,
            "requiredFunctionNos": ["1026"],
            "requiredLearnInfoKeys": ["openWindowLearnInfo"],
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
            "gateId": "seat_rear_left",
            "command": RemoteCommand.SEAT_CLIMATE,
            "requiredFunctionNos": ["10030006", "10030007"],
        }
    ),
    CommandGateRule.model_validate(
        {
            "gateId": "seat_rear_right",
            "command": RemoteCommand.SEAT_CLIMATE,
            "requiredFunctionNos": ["10030008", "10030009"],
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
            "requiredFunctionNos": ["10300002", "10100001"],
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
            # Stopping uses the same `RESERVATIONCHARGING` (`1012`) capability
            # as starting — the changeChargeStatue toggle with ``status: "0"``.
            "gateId": "stop_charge",
            "command": RemoteCommand.STOP_CHARGE,
            "requiredFunctionNos": ["1012"],
        }
    ),
)

# Seat command requires explicit target selection via control_params.chairType.
_SEAT_GATE_BY_CHAIR_TYPE: dict[str, str] = {
    "1": "seat_driver",
    "2": "seat_passenger",
    "3": "seat_rear_left",
    "4": "seat_rear_right",
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


GUEST_PERMISSION_BASIC_CONTROL = "21"
GUEST_PERMISSION_GPS = "31"
GUEST_PERMISSION_CHARGING = "41"
GUEST_PERMISSION_BATTERY_HEAT = "43"

_COMMAND_GUEST_PERMISSION: dict[RemoteCommand, str] = {
    RemoteCommand.START_CLIMATE: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.STOP_CLIMATE: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.SCHEDULE_CLIMATE: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.SEAT_CLIMATE: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.LOCK: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.UNLOCK: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.OPEN_TRUNK: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.CLOSE_TRUNK: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.OPEN_WINDOWS: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.CLOSE_WINDOWS: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.FIND_CAR: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.FLASH_LIGHTS: GUEST_PERMISSION_BASIC_CONTROL,
    RemoteCommand.BATTERY_HEAT: GUEST_PERMISSION_BATTERY_HEAT,
    RemoteCommand.START_CHARGE: GUEST_PERMISSION_CHARGING,
    RemoteCommand.STOP_CHARGE: GUEST_PERMISSION_CHARGING,
}

_GUEST_ALWAYS_ALLOWED_FUNCTION_NOS: frozenset[str] = frozenset(
    {
        "1004",
        "10020001",
        "10020002",
        "10020003",
        "10020005",
        "10020006",
    }
)


def _filter_guest_function_nos(
    function_nos: set[str],
    *,
    is_shared: bool,
    guest_permissions: frozenset[str] | None,
) -> set[str]:
    """Apply level-3/4 guest functionNo allow-list when present in ``guest_permissions``."""
    if not is_shared or not guest_permissions:
        return function_nos
    # only codes that are real command functionNos count as an allow-list,
    # deeper tree codes (eg "2101") must not wipe out every command
    level34 = guest_permissions & known_command_function_nos()
    if not level34:
        return function_nos
    allowed = level34 | _GUEST_ALWAYS_ALLOWED_FUNCTION_NOS
    return function_nos & allowed


def _guest_permission_ok(
    command: RemoteCommand,
    *,
    is_shared: bool,
    guest_permissions: frozenset[str] | None,
) -> bool:
    """Check level-2 guest permission code for *command* when the account is shared."""
    if not is_shared or not guest_permissions:
        return True
    required = _COMMAND_GUEST_PERMISSION.get(command)
    if required is None:
        return True
    return required in guest_permissions


def _evaluate_rule(
    rule: CommandGateRule,
    capabilities: VehicleCapabilities,
    *,
    function_nos: set[str],
    learn_info: Mapping[str, int] | None = None,
    is_shared: bool = False,
    guest_permissions: frozenset[str] | None = None,
) -> CommandGateVerdict:
    effective_fns = _filter_guest_function_nos(
        function_nos,
        is_shared=is_shared,
        guest_permissions=guest_permissions,
    )
    matched_function_nos = sorted([fn for fn in rule.required_function_nos if fn in effective_fns])
    counterpart_function_nos = sorted(
        function_no for function_no in rule.required_function_nos if function_no not in matched_function_nos
    )

    fn_supported = _require(effective_fns, rule.required_function_nos)

    # Fine gate: only enforced when the rule declares it AND the caller
    # actually provided a ``learn_info`` mapping.  Rules without
    # ``required_learn_info_keys`` keep the original function_no-only
    # contract, so this stays additive.
    matched_learn_info_keys = _learn_info_match(learn_info, rule.required_learn_info_keys)
    learn_info_supported = not rule.required_learn_info_keys or learn_info is None or bool(matched_learn_info_keys)
    guest_supported = _guest_permission_ok(
        rule.command,
        is_shared=is_shared,
        guest_permissions=guest_permissions,
    )

    supported = fn_supported and learn_info_supported and guest_supported
    if not guest_supported:
        reason = "guest_permission_denied"
    elif not fn_supported:
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
    is_shared: bool = False,
    guest_permissions: frozenset[str] | None = None,
) -> list[CommandGateVerdict]:
    """Evaluate all canonical gates for reporting and diagnostics."""
    function_nos = set(capabilities.function_nos)
    return [
        _evaluate_rule(
            rule,
            capabilities,
            function_nos=function_nos,
            learn_info=learn_info,
            is_shared=is_shared,
            guest_permissions=guest_permissions,
        )
        for rule in _COMMAND_GATE_RULES
    ]


def evaluate_command_gate(
    command: RemoteCommand,
    capabilities: VehicleCapabilities,
    *,
    control_params: Mapping[str, object] | None = None,
    learn_info: Mapping[str, int] | None = None,
    is_shared: bool = False,
    guest_permissions: frozenset[str] | None = None,
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
        return _evaluate_rule(
            selected_rule,
            capabilities,
            function_nos=function_nos,
            learn_info=learn_info,
            is_shared=is_shared,
            guest_permissions=guest_permissions,
        )

    selected = command_rules[0]
    return _evaluate_rule(
        selected,
        capabilities,
        function_nos=function_nos,
        learn_info=learn_info,
        is_shared=is_shared,
        guest_permissions=guest_permissions,
    )
