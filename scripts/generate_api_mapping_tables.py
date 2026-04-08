#!/usr/bin/env python3
"""Generate GitHub-friendly markdown tables from live BYD endpoint data.

The script logs in via pyBYD, polls each data endpoint for a VIN, and emits
two markdown tables per endpoint:

- Mapped rows (raw keys that pyBYD parses, plus latest-config normalized rows)
- Unmapped rows (raw keys not parsed by pyBYD)

Each table contains:

- Raw API key
- Raw current value
- Parsed in pyBYD

**Region:** Raw key sets differ between China (CN) and overseas backends. A snapshot taken with
``BYD_BASE_URL`` pointing at a CN host is not directly comparable to one from an EU/overseas host.

For enum-mapped fields, the parsed column includes:
- current mapping (``raw -> Enum.Member``)
- all enum possibilities (one per line via ``<br>``)

Usage
-----
::

    export BYD_USERNAME="you@example.com"
    export BYD_PASSWORD="your-password"
    python scripts/generate_api_mapping_tables.py

Options::

    --vin LNBX...       Target specific VIN (default: first account vehicle)
    --output FILE       Write markdown to FILE instead of stdout
    --skip-push         Skip push notification endpoint

China (CN) example::

    export BYD_BASE_URL="https://dilinksuperappserver-cn.byd.auto"
    export BYD_USERNAME="13800138000"
    export BYD_PASSWORD="your-password"
    export BYD_CN_APP_INNER_VERSION="502"
    export BYD_TARGET_BRAND="1"
    python scripts/generate_api_mapping_tables.py --output api-mapping-cn.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.alias_generators import to_camel

# Allow running from the repo root without installing the package.
_repo = Path(__file__).resolve().parent.parent
_src = _repo / "src"
if _src.is_dir():
    sys.path.insert(0, str(_src))

from pybyd import BydClient, BydConfig  # noqa: E402
from pybyd.exceptions import BydApiError, BydTransportError  # noqa: E402
from pybyd.models._base import BydEnum  # noqa: E402
from pybyd.models.command_gating import evaluate_all_command_gates  # noqa: E402
from pybyd.models.latest_config import (  # noqa: E402
    VehicleCapabilities,
    VehicleLatestConfig,
    registered_latest_config_function_nos,
)


@dataclass(frozen=True)
class EndpointSpec:
    """Data endpoint specification."""

    key: str
    title: str


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec("realtime", "Realtime"),
    EndpointSpec("gps", "GPS"),
    EndpointSpec("hvac", "HVAC"),
    EndpointSpec("charging", "Charging"),
    EndpointSpec("energy", "Energy"),
    EndpointSpec("latest_config", "Latest Config"),
    EndpointSpec("push", "Push Notifications"),
)

_SENSITIVE_KEY_HINTS: tuple[str, ...] = (
    "vin",
    "requestserial",
    "serial",
    "userid",
    "token",
    "email",
    "phone",
    "mobile",
    "password",
    "latitude",
    "longitude",
    "gps",
    "updatetime",
    "timestamp",
)

_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_JSON_SENSITIVE_FIELD_PATTERN = re.compile(
    r'("(?:vin|requestSerial|serial|userId|token|latitude|longitude|gps(?:TimeStamp)?|updateTime|timestamp)"\s*:\s*)(".*?"|\d+|null)',
    re.IGNORECASE,
)
_KV_SENSITIVE_FIELD_PATTERN = re.compile(
    r"\b(?:vin|requestSerial|serial|userId|token|latitude|longitude|gps(?:TimeStamp)?|updateTime|timestamp)\s*=\s*([^,\s]+)",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    """Return True when *key* is considered sensitive for sharing."""
    normalized = _leaf_key(key).lower().replace("_", "")
    return any(hint in normalized for hint in _SENSITIVE_KEY_HINTS)


def _redact_value(key: str, value: Any) -> Any:
    """Redact sensitive values while preserving non-sensitive content."""
    if _is_sensitive_key(key):
        return "<REDACTED>"
    return value


def _redact_text(value: str) -> str:
    """Redact sensitive identifiers from arbitrary text snippets."""
    redacted = _VIN_PATTERN.sub("<REDACTED>", value)
    redacted = _JSON_SENSITIVE_FIELD_PATTERN.sub(r'\1"<REDACTED>"', redacted)
    redacted = _KV_SENSITIVE_FIELD_PATTERN.sub(lambda m: f"{m.group(0).split('=')[0]}=<REDACTED>", redacted)
    return redacted


def _flatten_json(obj: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict/list values into dot/index paths."""
    flattened: dict[str, Any] = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            flattened.update(_flatten_json(value, prefix=path))
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                list_path = f"{path}[{index}]"
                if isinstance(item, dict) and item:
                    flattened.update(_flatten_json(item, prefix=list_path))
                else:
                    flattened[list_path] = item
        else:
            flattened[path] = value
    return flattened


def _leaf_key(path: str) -> str:
    """Return the last object key of a flattened path."""
    last_segment = path.split(".")[-1]
    return re.sub(r"\[\d+\]", "", last_segment)


def _extract_validation_aliases(value: object) -> set[str]:
    """Extract string aliases from pydantic validation alias objects."""
    aliases: set[str] = set()
    if isinstance(value, str):
        aliases.add(value)
        return aliases

    choices = getattr(value, "choices", None)
    if isinstance(choices, tuple):
        for choice in choices:
            if isinstance(choice, str):
                aliases.add(choice)

    path = getattr(value, "path", None)
    if isinstance(path, tuple) and all(isinstance(part, str) for part in path):
        aliases.add(".".join(path))

    return aliases


def _enum_type_from_annotation(annotation: object) -> type[BydEnum] | None:
    """Return the enum class contained in a field annotation, if any."""
    if isinstance(annotation, type) and issubclass(annotation, BydEnum):
        return annotation

    origin = get_origin(annotation)
    if origin is None:
        return None

    for arg in get_args(annotation):
        enum_cls = _enum_type_from_annotation(arg)
        if enum_cls is not None:
            return enum_cls
    return None


def _annotation_label(annotation: object) -> str:
    """Return a readable type label for a field annotation."""
    if isinstance(annotation, type):
        return annotation.__name__

    origin = get_origin(annotation)
    if origin is None:
        return str(annotation)

    parts = [_annotation_label(arg) for arg in get_args(annotation)]
    if parts:
        return " | ".join(parts)
    return str(origin)


def _build_field_maps(model_obj: BaseModel) -> tuple[dict[str, str], dict[str, type[BydEnum]]]:
    """Build alias->field and field->enum maps for a pydantic model instance."""
    alias_to_field: dict[str, str] = {}
    field_to_enum: dict[str, type[BydEnum]] = {}

    model_fields = type(model_obj).model_fields
    for field_name, field_info in model_fields.items():
        if field_name in {"raw", "vin"}:
            continue

        aliases: set[str] = {to_camel(field_name)}
        if isinstance(field_info.alias, str):
            aliases.add(field_info.alias)
        aliases.update(_extract_validation_aliases(field_info.validation_alias))

        for alias in aliases:
            alias_to_field[alias] = field_name

        enum_cls = _enum_type_from_annotation(field_info.annotation)
        if enum_cls is not None:
            field_to_enum[field_name] = enum_cls

    return alias_to_field, field_to_enum


def _field_type_label(model_obj: BaseModel, field_name: str) -> str:
    """Return readable field type for *field_name* on *model_obj*."""
    model_fields = type(model_obj).model_fields
    field_info = model_fields.get(field_name)
    if field_info is None:
        return "unknown"
    return _annotation_label(field_info.annotation)


def _json_value(value: Any) -> str:
    """Format a value for markdown table cells."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, BydEnum):
        return f"{value.__class__.__name__}.{value.name}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _escape_cell(value: str) -> str:
    """Escape markdown table separators."""
    return value.replace("|", "\\|")


def _resolve_field_for_raw_key(raw_key: str, alias_to_field: dict[str, str], key_aliases: dict[str, str]) -> str | None:
    """Resolve a raw API key path to a model field name."""
    candidates = [raw_key, _leaf_key(raw_key)]
    for candidate in candidates:
        canonical = key_aliases.get(candidate, candidate)
        mapped = alias_to_field.get(canonical)
        if mapped is not None:
            return mapped
    return None


def _enum_block(enum_cls: type[BydEnum]) -> str:
    """Return line-shifted enum possibilities for markdown table cells."""
    lines = [f"Enum {enum_cls.__name__}:"]
    for member in enum_cls:
        lines.append(f"{member.value} → {enum_cls.__name__}.{member.name}")
    return "<br>".join(lines)


def _parsed_cell(raw_value: Any, parsed_value: Any, enum_cls: type[BydEnum] | None) -> str:
    """Build the "parsed in pyBYD" cell content."""
    mapping_line = f"{_json_value(raw_value)} → {_json_value(parsed_value)}"
    if enum_cls is None:
        return mapping_line
    return f"{mapping_line}<br><br>{_enum_block(enum_cls)}"


def _format_endpoint_error(exc: Exception) -> str:
    """Build a compact error summary for failed endpoint fetches."""
    sanitized_message = _redact_text(str(exc))
    if isinstance(exc, BydTransportError):
        return (
            f"TransportError status={exc.status_code if exc.status_code is not None else 'unknown'} "
            f"endpoint={exc.endpoint or 'unknown'} message={sanitized_message}"
        )
    if isinstance(exc, BydApiError):
        return f"ApiError code={exc.code or 'unknown'} endpoint={exc.endpoint or 'unknown'} message={sanitized_message}"
    return f"{type(exc).__name__}: {sanitized_message}"


def _latest_config_capabilities(model_obj: BaseModel, vin: str | None) -> VehicleCapabilities | None:
    """Return normalized capabilities for latest-config views when possible."""
    if isinstance(model_obj, VehicleCapabilities):
        return model_obj
    if isinstance(model_obj, VehicleLatestConfig) and vin is not None:
        return VehicleCapabilities.from_latest_config(vin, model_obj)
    return None


def _latest_config_normalized_rows(model_obj: BaseModel, vin: str | None) -> list[tuple[str, Any, str]]:
    """Build synthetic normalized capability rows for latest-config endpoint output."""
    capabilities = _latest_config_capabilities(model_obj, vin)
    if capabilities is None:
        return []

    rows: list[tuple[str, Any, str]] = []
    for field_name in type(capabilities).model_fields:
        if field_name in {"raw", "vin"}:
            continue
        raw_key = f"__normalized.{field_name}__"
        raw_value = _redact_value(raw_key, getattr(capabilities, field_name))
        parsed_text = f"{_json_value(raw_value)} → {field_name} ({_field_type_label(capabilities, field_name)})"
        rows.append((raw_key, raw_value, parsed_text))

    registered_function_nos = registered_latest_config_function_nos()
    for function_no in capabilities.function_nos:
        is_registered = function_no in registered_function_nos
        raw_key = f"__function_no.{function_no}__"
        raw_value = {
            "functionNo": function_no,
            "registered": is_registered,
        }
        parsed_text = f"{is_registered} → function_no_registered ({function_no})"
        rows.append((raw_key, raw_value, parsed_text))

    for verdict in evaluate_all_command_gates(capabilities):
        raw_key = f"__command_gate.{verdict.command.value}.{verdict.gate_id}__"
        raw_value = {
            "command": verdict.command.value,
            "gateId": verdict.gate_id,
            "supported": verdict.supported,
            "reason": verdict.reason,
            "matchedFunctionNos": verdict.matched_function_nos,
            "counterpartFunctionNos": verdict.counterpart_function_nos,
        }
        parsed_text = (
            f"{verdict.supported} → command_gate "
            f"(reason={verdict.reason}; "
            f"matched_function_nos={verdict.matched_function_nos}; "
            f"counterpart_function_nos={verdict.counterpart_function_nos})"
        )
        rows.append((raw_key, raw_value, parsed_text))

    return rows


def _render_table_block(lines: list[str], title: str, rows: list[tuple[str, Any, str]]) -> None:
    """Append a titled markdown table block to *lines*."""
    lines.extend(
        [
            f"### {title}",
            "",
            "| Raw API key | Raw current value | Parsed in pyBYD |",
            "|---|---:|---|",
        ]
    )

    if not rows:
        lines.append("| _(no rows)_ |  |  |")
        lines.append("")
        return

    for raw_key, raw_value, parsed_text in rows:
        lines.append(
            f"| {_escape_cell(raw_key)} | {_escape_cell(_json_value(raw_value))} | {_escape_cell(parsed_text)} |"
        )

    lines.append("")


def _endpoint_table(endpoint: EndpointSpec, model_obj: BaseModel, *, vin: str | None = None) -> str:
    """Render one endpoint section and markdown tables."""
    raw_obj = getattr(model_obj, "raw", {})
    raw_dict: dict[str, Any] = raw_obj if isinstance(raw_obj, dict) else {}
    flat_raw = _flatten_json(raw_dict)

    alias_to_field, field_to_enum = _build_field_maps(model_obj)
    key_aliases: dict[str, str] = getattr(type(model_obj), "_KEY_ALIASES", {})

    lines: list[str] = [
        f"## {endpoint.title}",
        "",
    ]

    mapped_rows: list[tuple[str, Any, str]] = []
    unmapped_rows: list[tuple[str, Any, str]] = []

    for raw_key in sorted(flat_raw.keys()):
        is_sensitive = _is_sensitive_key(raw_key)
        raw_value = _redact_value(raw_key, flat_raw[raw_key])
        field_name = _resolve_field_for_raw_key(raw_key, alias_to_field, key_aliases)

        if field_name is None:
            if endpoint.key == "gps" and is_sensitive:
                parsed_text = "<REDACTED> → Not parsed (raw only)"
            else:
                parsed_text = "Not parsed (raw only)"
            unmapped_rows.append((raw_key, raw_value, parsed_text))
        else:
            if endpoint.key == "gps" and is_sensitive:
                parsed_text = f"<REDACTED> → {field_name} ({_field_type_label(model_obj, field_name)})"
            else:
                parsed_value = _redact_value(field_name, getattr(model_obj, field_name))
                enum_cls = field_to_enum.get(field_name)
                parsed_text = _parsed_cell(raw_value, parsed_value, enum_cls)
            mapped_rows.append((raw_key, raw_value, parsed_text))

    if endpoint.key == "latest_config":
        mapped_rows.extend(_latest_config_normalized_rows(model_obj, vin))

    _render_table_block(lines, "Mapped", mapped_rows)
    _render_table_block(lines, "Unmapped", unmapped_rows)

    return "\n".join(lines)


async def _fetch_endpoint(client: BydClient, endpoint_key: str, vin: str) -> BaseModel:
    """Fetch and return a single endpoint model."""
    if endpoint_key == "realtime":
        return await client.get_vehicle_realtime(vin)
    if endpoint_key == "gps":
        return await client.get_gps_info(vin)
    if endpoint_key == "hvac":
        return await client.get_hvac_status(vin)
    if endpoint_key == "charging":
        return await client.get_charging_status(vin)
    if endpoint_key == "energy":
        return await client.get_energy_consumption(vin)
    if endpoint_key == "latest_config":
        return await client.get_latest_config(vin)
    if endpoint_key == "push":
        return await client.get_push_state(vin)
    raise ValueError(f"Unsupported endpoint key: {endpoint_key}")


async def _generate_markdown(vin: str | None, *, include_push: bool) -> str:
    """Poll endpoints and return markdown report text."""
    config = BydConfig.from_env()
    now_iso = datetime.now(UTC).isoformat()
    region = "cn" if config.is_china_region else "overseas"
    lines: list[str] = [
        "# pyBYD live API mapping snapshot",
        "",
        "Generated with `scripts/generate_api_mapping_tables.py`:",
        "- `python scripts/generate_api_mapping_tables.py --output api-mapping-live.md`",
        "- Optional: `python scripts/generate_api_mapping_tables.py --vin YOUR_VIN --skip-push`",
        "",
        f"Generated: {now_iso}",
        f"Region: {region}",
        f"Base URL: {config.base_url}",
        "",
    ]

    async with BydClient(config) as client:
        await client.login()
        vehicles = await client.get_vehicles()
        if not vehicles:
            raise RuntimeError("No vehicles found for account")

        selected_vin = vin or vehicles[0].vin
        lines.append(f"VIN: {_redact_value('vin', selected_vin)}")
        lines.append("")

        endpoints = ENDPOINTS if include_push else tuple(spec for spec in ENDPOINTS if spec.key != "push")
        for spec in endpoints:
            try:
                model_obj = await _fetch_endpoint(client, spec.key, selected_vin)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"## {spec.title}")
                lines.append("")
                lines.append(f"> Endpoint fetch failed: {_format_endpoint_error(exc)}")
                lines.append("")
                continue

            lines.append(_endpoint_table(spec, model_obj, vin=selected_vin))

    return "\n".join(lines).rstrip() + "\n"


async def _async_main() -> None:
    parser = argparse.ArgumentParser(description="Generate markdown mapping tables from live BYD API endpoint data.")
    parser.add_argument("--vin", help="Target VIN (default: first account vehicle)")
    parser.add_argument("--output", "-o", help="Write markdown to FILE instead of stdout")
    parser.add_argument("--skip-push", action="store_true", help="Skip push notification endpoint")
    args = parser.parse_args()

    markdown = await _generate_markdown(args.vin, include_push=not args.skip_push)

    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        print(f"Markdown written to {args.output}")
        return

    print(markdown, end="")


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
