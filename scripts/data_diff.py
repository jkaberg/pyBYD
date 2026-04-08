#!/usr/bin/env python3
"""Interactive data-diff CLI for BYD vehicles.

Pick a data category, then keep toggling things and see exactly what
changed — colour-coded in your terminal.

Usage
-----
::

    export BYD_USERNAME="you@example.com"
    export BYD_PASSWORD="your-password"
    python scripts/data_diff.py

Options::

    --vin LNBX...       Only query this VIN (default: first vehicle)
    --raw                Also show raw (unparsed) API field diffs
    --settle SECS        Seconds to wait after Enter before polling (default: 2)
    --baseline-delay S   Delay between noise-calibration snapshots (default: 4)
    --verbose / -v       Enable debug logging

China (CN) example::

    export BYD_BASE_URL="https://dilinksuperappserver-cn.byd.auto"
    export BYD_USERNAME="13800138000"
    export BYD_PASSWORD="your-password"
    export BYD_APP_CHANNEL="99"
    export BYD_CN_APP_INNER_VERSION="502"
    export BYD_TARGET_BRAND="1"
    python scripts/data_diff.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# Allow running from the repo root without installing the package.
_repo = Path(__file__).resolve().parent.parent
_src = _repo / "src"
if _src.is_dir():
    sys.path.insert(0, str(_src))

from pybyd import BydClient, BydConfig  # noqa: E402

# ── Inline helpers (formerly pybyd._tools.field_mapper) ──────


def safe_json_value(obj: Any) -> Any:
    """Recursively convert a value to JSON-safe primitives.

    Enums → ``{"name": ..., "value": ...}``; everything else passes through.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): safe_json_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe_json_value(v) for v in obj]
    # Enum-like
    if hasattr(obj, "name") and hasattr(obj, "value"):
        return {"name": obj.name, "value": obj.value}
    return str(obj)


def flatten_json(
    obj: dict[str, Any],
    *,
    _prefix: str = "",
) -> dict[str, Any]:
    """Flatten a nested dict/list into dot-notation paths.

    Lists use ``[i]`` indexing.  Empty lists/dicts are kept as leaf values.
    """
    out: dict[str, Any] = {}
    for key, value in obj.items():
        path = f"{_prefix}.{key}" if _prefix else key
        if isinstance(value, dict) and value:
            out.update(flatten_json(value, _prefix=path))
        elif isinstance(value, list) and value:
            for i, item in enumerate(value):
                idx_path = f"{path}[{i}]"
                if isinstance(item, dict) and item:
                    out.update(flatten_json(item, _prefix=idx_path))
                else:
                    out[idx_path] = item
        else:
            out[path] = value
    return out


def normalize_for_compare(value: Any) -> Any:
    """Normalise a value for comparison.

    Returns *None* for sentinel / empty values so that they compare as
    "absent" rather than producing spurious diffs.
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ("", "--", "null"):
        return None
    if isinstance(value, float) and (value != value):  # NaN check
        return None
    return value


def diff_flatmaps(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    ignored_paths: set[str] | None = None,
) -> dict[str, tuple[Any, Any]]:
    """Return ``{path: (old, new)}`` for every path that changed.

    Paths in *ignored_paths* are silently skipped.
    """
    ignored = ignored_paths or set()
    changes: dict[str, tuple[Any, Any]] = {}
    all_keys = set(before) | set(after)
    for key in all_keys:
        if key in ignored:
            continue
        old = normalize_for_compare(before.get(key))
        new = normalize_for_compare(after.get(key))
        if old != new:
            changes[key] = (before.get(key), after.get(key))
    return changes


LOG = logging.getLogger("data_diff")

# ── ANSI colours ─────────────────────────────────────────────

_NO_COLOR = bool(os.environ.get("NO_COLOR"))

RED = "" if _NO_COLOR else "\033[31m"
GREEN = "" if _NO_COLOR else "\033[32m"
YELLOW = "" if _NO_COLOR else "\033[33m"
CYAN = "" if _NO_COLOR else "\033[36m"
BOLD = "" if _NO_COLOR else "\033[1m"
DIM = "" if _NO_COLOR else "\033[2m"
RESET = "" if _NO_COLOR else "\033[0m"


def _c(text: str, color: str) -> str:
    """Wrap *text* in an ANSI colour sequence."""
    if _NO_COLOR:
        return text
    return f"{color}{text}{RESET}"


# ── Volatile / noisy field patterns ─────────────────────────

VOLATILE_PATH_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "realtime": [
        re.compile(r"(^|\.)timestamp$"),
        re.compile(r"(^|\.)request_serial$"),
        re.compile(r"(^|\.)speed$"),
        re.compile(r"(^|\.)total_mileage"),
    ],
    "gps": [
        re.compile(r"(^|\.)gps_timestamp"),
        re.compile(r"(^|\.)request_serial$"),
        re.compile(r"(^|\.)speed$"),
        re.compile(r"(^|\.)direction$"),
        re.compile(r"(^|\.)latitude$"),
        re.compile(r"(^|\.)longitude$"),
    ],
    "charging": [
        re.compile(r"(^|\.)update_time"),
        re.compile(r"(^|\.)full_hour"),
        re.compile(r"(^|\.)full_minute"),
    ],
}


def _matches_volatile(endpoint: str, path: str) -> bool:
    return any(pat.search(path) for pat in VOLATILE_PATH_PATTERNS.get(endpoint, []))


# ── Endpoint registry ───────────────────────────────────────

ENDPOINTS: list[tuple[str, str]] = [
    (
        "realtime",
        "Battery %, range, doors, locks, windows, tyre pressure, seat heating, charging state, vehicle on/off",
    ),
    (
        "gps",
        "Latitude, longitude, speed, heading — the device tracker",
    ),
    (
        "hvac",
        "A/C on/off, set temperature, fan mode, defrost, seat heat/vent, steering wheel heat",
    ),
    (
        "charging",
        "SoC, plug connected, charging active, estimated time to full",
    ),
    (
        "energy",
        "Trip energy consumption, average efficiency, fuel consumption (PHEV)",
    ),
    (
        "latest_config",
        "Feature-capability configuration (functionNo/code list)",
    ),
    (
        "push",
        "Push-notification switch (on/off)",
    ),
]


# ── Snapshot helpers ─────────────────────────────────────────


def _model_to_parsed(obj: Any) -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        result: dict[str, Any] = safe_json_value(obj.model_dump(exclude={"raw"}))
        return result
    return {"__repr__": repr(obj)}


def _model_to_raw(obj: Any) -> dict[str, Any]:
    raw = getattr(obj, "raw", None)
    result: dict[str, Any] = safe_json_value(raw) if isinstance(raw, dict) else {}
    return result


async def _fetch(client: BydClient, endpoint: str, vin: str) -> Any:
    """Fetch a single endpoint and return the model object."""
    if endpoint == "realtime":
        return await client.get_vehicle_realtime(vin)
    if endpoint == "gps":
        return await client.get_gps_info(vin)
    if endpoint == "hvac":
        return await client.get_hvac_status(vin)
    if endpoint == "charging":
        return await client.get_charging_status(vin)
    if endpoint == "energy":
        return await client.get_energy_consumption(vin)
    if endpoint == "latest_config":
        return await client.get_latest_config(vin)
    if endpoint == "push":
        return await client.get_push_state(vin)
    raise ValueError(f"Unknown endpoint: {endpoint}")


def _snapshot(obj: Any, *, include_raw: bool) -> dict[str, dict[str, Any]]:
    """Return flat-map snapshots for a model object."""
    parsed = flatten_json(_model_to_parsed(obj))
    snap: dict[str, dict[str, Any]] = {"parsed": parsed}
    if include_raw:
        snap["raw"] = flatten_json(_model_to_raw(obj))
    return snap


# ── Noise calibration ───────────────────────────────────────


async def _calibrate_noise(
    client: BydClient,
    endpoint: str,
    vin: str,
    *,
    include_raw: bool,
    delay: float,
) -> set[str]:
    """Take two snapshots and return paths that changed (= noise)."""
    print(f"\n{DIM}Taking baseline snapshot 1…{RESET}")
    obj1 = await _fetch(client, endpoint, vin)
    snap1 = _snapshot(obj1, include_raw=include_raw)

    print(f"{DIM}Waiting {delay:.0f}s before second snapshot…{RESET}")
    await asyncio.sleep(delay)

    print(f"{DIM}Taking baseline snapshot 2…{RESET}")
    obj2 = await _fetch(client, endpoint, vin)
    snap2 = _snapshot(obj2, include_raw=include_raw)

    noise: set[str] = set()
    for section in snap1:
        diffs = diff_flatmaps(snap1[section], snap2[section])
        for path in diffs:
            noise.add(f"{section}:{path}")

    # Add hardcoded volatile patterns as a safety net
    for section in snap1:
        for path in snap1[section]:
            if _matches_volatile(endpoint, path):
                noise.add(f"{section}:{path}")

    return noise


# ── Diff display ─────────────────────────────────────────────

_MISSING = object()


def _format_value(val: Any) -> str:
    """Compact string representation for a field value."""
    if val is None:
        return "null"
    if val is _MISSING:
        return "<absent>"
    if isinstance(val, dict):
        name = val.get("name")
        value = val.get("value")
        if name is not None and value is not None:
            return f"{name} ({value})"
    if isinstance(val, str):
        return repr(val) if " " in val or not val else val
    return str(val)


def _show_diff(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    noise: set[str],
    *,
    include_raw: bool,
) -> int:
    """Print coloured diff and return the total number of changes."""
    sections = ["parsed"]
    if include_raw:
        sections.append("raw")

    total = 0
    for section in sections:
        b = before.get(section, {})
        a = after.get(section, {})
        diffs = diff_flatmaps(b, a)

        # Filter out noise
        filtered: dict[str, tuple[Any, Any]] = {}
        for path, (old, new) in diffs.items():
            key = f"{section}:{path}"
            if key not in noise:
                filtered[path] = (old, new)

        if not filtered:
            continue

        label = "Parsed fields" if section == "parsed" else "Raw API fields"
        print(f"\n  {BOLD}── {label} ──{RESET}")

        for path in sorted(filtered):
            old, new = filtered[path]
            old_norm = normalize_for_compare(old)
            new_norm = normalize_for_compare(new)

            # Determine change type
            old_exists = old_norm is not None
            new_exists = new_norm is not None

            if not old_exists and new_exists:
                # Added
                print(f"    {_c('+', GREEN)} {_c(path, GREEN)}: {_c(_format_value(new), GREEN)}")
            elif old_exists and not new_exists:
                # Removed
                print(f"    {_c('-', RED)} {_c(path, RED)}: {_c(_format_value(old), RED)}")
            else:
                # Changed
                print(
                    f"    {_c('~', YELLOW)} {_c(path, YELLOW)}: "
                    f"{_c(_format_value(old), RED)} → {_c(_format_value(new), GREEN)}"
                )

            total += 1

    return total


# ── Interactive menu ─────────────────────────────────────────


def _print_menu() -> None:
    print(f"\n{BOLD}Select a data category to watch:{RESET}\n")
    for idx, (name, desc) in enumerate(ENDPOINTS, 1):
        print(f"  {_c(str(idx), CYAN)}) {BOLD}{name}{RESET}")
        print(f"     {DIM}{desc}{RESET}")
    print()


async def _choose_endpoint() -> str:
    """Display numbered menu and return the chosen endpoint name."""
    while True:
        _print_menu()
        answer = (await asyncio.to_thread(input, f"Enter choice [{_c('1-6', CYAN)}]: ")).strip()
        try:
            choice = int(answer)
            if 1 <= choice <= len(ENDPOINTS):
                return ENDPOINTS[choice - 1][0]
        except ValueError:
            # Try matching by name
            for name, _ in ENDPOINTS:
                if answer.lower() == name.lower():
                    return name
        print(_c("Invalid choice. Try again.", RED))


# ── Async input helper ───────────────────────────────────────


async def _ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


# ── Section header ───────────────────────────────────────────


def _section(title: str) -> str:
    line = "=" * 60
    return f"\n{line}\n  {title}\n{line}"


# ── Main ─────────────────────────────────────────────────────


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive data-diff tool for BYD vehicles",
    )
    parser.add_argument("--vin", help="VIN to query (default: first vehicle)")
    parser.add_argument("--raw", action="store_true", help="Also diff raw (unparsed) API fields")
    parser.add_argument("--settle", type=float, default=2.0, help="Seconds to wait after Enter before polling")
    parser.add_argument("--baseline-delay", type=float, default=4.0, help="Delay between noise-calibration snapshots")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cfg = BydConfig.from_env()

    print(_section("BYD Data Diff"))
    print(f"  {DIM}Connecting…{RESET}")

    async with BydClient(cfg) as client:
        await client.login()
        vehicles = await client.get_vehicles()
        if not vehicles:
            print(_c("No vehicles found.", RED))
            return

        vin = args.vin or vehicles[0].vin
        print(f"  VIN: {vin[:6]}…{vin[-4:]}")

        # Choose endpoint
        endpoint = await _choose_endpoint()
        include_raw: bool = args.raw

        print(_section(f"Watching: {endpoint}"))

        # Noise calibration
        print(f"\n{BOLD}Noise calibration{RESET}")
        print(f"{DIM}Two quick polls to detect volatile fields (timestamps, counters, etc.){RESET}")

        noise = await _calibrate_noise(
            client,
            endpoint,
            vin,
            include_raw=include_raw,
            delay=args.baseline_delay,
        )

        if noise:
            print(f"\n  {DIM}Auto-detected {len(noise)} volatile field(s) — these will be hidden:{RESET}")
            for key in sorted(noise):
                section, path = key.split(":", 1)
                print(f"    {DIM}• [{section}] {path}{RESET}")
        else:
            print(f"\n  {DIM}No volatile fields detected.{RESET}")

        # Initial snapshot (the second calibration snapshot becomes our first baseline)
        print(f"\n{DIM}Taking initial snapshot…{RESET}")
        before_obj = await _fetch(client, endpoint, vin)
        before = _snapshot(before_obj, include_raw=include_raw)

        # Main loop
        iteration = 0
        print(f"\n{BOLD}Ready!{RESET} Make a change, then press Enter. {DIM}Ctrl+C to quit.{RESET}\n")

        while True:
            iteration += 1
            try:
                await _ainput(f"  {_c(f'[{iteration}]', CYAN)} Press {BOLD}Enter{RESET} after making a change… ")
            except EOFError:
                break

            if args.settle > 0:
                print(f"  {DIM}Waiting {args.settle:.0f}s for API to propagate…{RESET}")
                await asyncio.sleep(args.settle)

            print(f"  {DIM}Polling {endpoint}…{RESET}")
            try:
                after_obj = await _fetch(client, endpoint, vin)
            except Exception as exc:
                print(f"  {_c(f'Error: {exc}', RED)}")
                continue

            after = _snapshot(after_obj, include_raw=include_raw)
            changes = _show_diff(before, after, noise, include_raw=include_raw)

            if changes:
                print(f"\n  {BOLD}{changes} field(s) changed{RESET}")
            else:
                print(f"\n  {DIM}No changes detected (excluding {len(noise)} volatile field(s)){RESET}")

            # The "after" becomes the new baseline
            before = after
            print()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print(f"\n{DIM}Done.{RESET}")


if __name__ == "__main__":
    main()
