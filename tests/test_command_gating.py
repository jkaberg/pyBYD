"""Tests for the command_gating module.

Focuses on the secondary ``required_learn_info_keys`` gate added for
Issue #47.  The existing ``required_function_nos`` behaviour is exercised
indirectly through ``test_client_command_gating`` and ``test_latest_config``.
"""

from __future__ import annotations

import pytest

from pybyd.models.command_gating import (
    CommandGateRule,
    _evaluate_rule,
    evaluate_command_gate,
)
from pybyd.models.control import RemoteCommand
from pybyd.models.latest_config import VehicleCapabilities


def _caps_with(function_nos: list[str]) -> VehicleCapabilities:
    """Build a capabilities object carrying the given functionNos."""
    return VehicleCapabilities.model_validate(
        {
            "vin": "TEST" + "X" * 13,
            "source": "test_fixture",
            "functionNos": function_nos,
        }
    )


class TestLearnInfoGate:
    """The secondary gate against ``vehicleFunLearnInfo``."""

    def test_rule_without_learn_info_keys_ignores_mapping(self):
        """Rules with no ``required_learn_info_keys`` keep current behaviour."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_no_learn_info",
                "command": RemoteCommand.LOCK,
                "requiredFunctionNos": ["1005"],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with(["1005"]),
            function_nos={"1005"},
            learn_info={"someUnrelatedKey": 1},
        )
        assert verdict.supported is True
        assert verdict.matched_learn_info_keys == []

    def test_learn_info_keys_required_and_present(self):
        """Rule with ``required_learn_info_keys`` supported when key > 0."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_with_learn_info",
                "command": RemoteCommand.OPEN_WINDOWS,
                "requiredFunctionNos": ["1026"],
                "requiredLearnInfoKeys": ["openWindowLearnInfo"],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with(["1026"]),
            function_nos={"1026"},
            learn_info={"openWindowLearnInfo": 1},
        )
        assert verdict.supported is True
        assert verdict.reason == "supported"
        assert verdict.matched_learn_info_keys == ["openWindowLearnInfo"]

    @pytest.mark.parametrize("value", [0, -1])
    def test_learn_info_keys_required_but_absent_or_negative(self, value: int):
        """``0`` / ``-1`` count as not-present per the issue's data."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_with_learn_info_off",
                "command": RemoteCommand.OPEN_WINDOWS,
                "requiredFunctionNos": ["1026"],
                "requiredLearnInfoKeys": ["openWindowLearnInfo"],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with(["1026"]),
            function_nos={"1026"},
            learn_info={"openWindowLearnInfo": value},
        )
        assert verdict.supported is False
        assert verdict.reason == "learn_info_missing"
        assert verdict.matched_learn_info_keys == []

    def test_learn_info_any_match_is_enough(self):
        """Multiple keys → any positive value passes the gate."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_multi_keys",
                "command": RemoteCommand.OPEN_WINDOWS,
                "requiredFunctionNos": ["1026"],
                "requiredLearnInfoKeys": [
                    "openWindowLearnInfo",
                    "openWindow499LearnInfo",
                ],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with(["1026"]),
            function_nos={"1026"},
            learn_info={"openWindowLearnInfo": 0, "openWindow499LearnInfo": 1},
        )
        assert verdict.supported is True
        assert verdict.matched_learn_info_keys == ["openWindow499LearnInfo"]

    def test_learn_info_none_skips_check(self):
        """``learn_info=None`` means caller has no data → skip secondary gate."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_no_caller_learn_info",
                "command": RemoteCommand.OPEN_WINDOWS,
                "requiredFunctionNos": ["1026"],
                "requiredLearnInfoKeys": ["openWindowLearnInfo"],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with(["1026"]),
            function_nos={"1026"},
            learn_info=None,
        )
        # Backwards-compat: rules with learn_info_keys still pass when the
        # caller hasn't supplied vehicleFunLearnInfo.
        assert verdict.supported is True
        assert verdict.matched_learn_info_keys == []

    def test_function_no_missing_takes_precedence(self):
        """When function_no is missing, reason stays ``function_no_missing``."""
        rule = CommandGateRule.model_validate(
            {
                "gateId": "test_fn_missing",
                "command": RemoteCommand.OPEN_WINDOWS,
                "requiredFunctionNos": ["1026"],
                "requiredLearnInfoKeys": ["openWindowLearnInfo"],
            }
        )
        verdict = _evaluate_rule(
            rule,
            _caps_with([]),
            function_nos=set(),
            learn_info={"openWindowLearnInfo": 1},
        )
        assert verdict.supported is False
        assert verdict.reason == "function_no_missing"


class TestEvaluateCommandGatePublicSurface:
    """Top-level ``evaluate_command_gate`` accepts and forwards ``learn_info``."""

    @pytest.mark.parametrize(
        "command",
        [
            RemoteCommand.START_CLIMATE,
            RemoteCommand.STOP_CLIMATE,
            RemoteCommand.SCHEDULE_CLIMATE,
        ],
    )
    def test_electric_ac_function_no_allows_climate_commands(self, command: RemoteCommand):
        """Vehicles that advertise Electric A/C as 1015 can use HVAC commands."""
        verdict = evaluate_command_gate(command, _caps_with(["1015"]))
        assert verdict.supported is True
        assert verdict.reason == "supported"
        assert verdict.matched_function_nos == ["1015"]

    def test_open_windows_blocked_when_learn_info_off(self):
        """The shipped OPEN_WINDOWS rule rejects when learn_info=0."""
        verdict = evaluate_command_gate(
            RemoteCommand.OPEN_WINDOWS,
            _caps_with(["1026"]),
            learn_info={"openWindowLearnInfo": 0, "openWindow499LearnInfo": 0},
        )
        assert verdict.supported is False
        assert verdict.reason == "learn_info_missing"

    def test_open_windows_allowed_when_learn_info_on(self):
        """The shipped OPEN_WINDOWS rule allows when learn_info=1."""
        verdict = evaluate_command_gate(
            RemoteCommand.OPEN_WINDOWS,
            _caps_with(["1026"]),
            learn_info={"openWindowLearnInfo": 1},
        )
        assert verdict.supported is True
        assert verdict.reason == "supported"

    def test_open_windows_backward_compat_without_learn_info(self):
        """Without learn_info, OPEN_WINDOWS stays supported on 1026 only."""
        verdict = evaluate_command_gate(
            RemoteCommand.OPEN_WINDOWS,
            _caps_with(["1026"]),
        )
        assert verdict.supported is True

    def test_stop_charge_blocked_when_booking_charge_off(self):
        """The STOP_CHARGE rule rejects when bookingCharge is 0 or missing."""
        verdict = evaluate_command_gate(
            RemoteCommand.STOP_CHARGE,
            _caps_with(["1012"]),
            learn_info={"bookingCharge": 0},
        )
        assert verdict.supported is False
        assert verdict.reason == "learn_info_missing"

    @pytest.mark.parametrize("booking_charge", [4, 5])
    def test_stop_charge_allowed_when_booking_charge_supported(self, booking_charge: int):
        """The STOP_CHARGE rule accepts when bookingCharge is positive (4 for PHEV, 5 for BEV)."""
        verdict = evaluate_command_gate(
            RemoteCommand.STOP_CHARGE,
            _caps_with(["1012"]),
            learn_info={"bookingCharge": booking_charge},
        )
        assert verdict.supported is True
        assert verdict.reason == "supported"

    def test_stop_charge_backward_compat_without_learn_info(self):
        """Without learn_info, STOP_CHARGE falls back to 1012 check only."""
        verdict = evaluate_command_gate(
            RemoteCommand.STOP_CHARGE,
            _caps_with(["1012"]),
        )
        assert verdict.supported is True
