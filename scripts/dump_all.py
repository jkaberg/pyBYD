#!/usr/bin/env python3
"""Dump all data the pybyd library can fetch.

This script logs in, discovers vehicles, and calls every read-only
endpoint, printing both the parsed model fields **and** the raw API
JSON so you can spot fields that aren't parsed yet.

Usage
-----
Set environment variables and run::

    export BYD_USERNAME="you@example.com"
    export BYD_PASSWORD="your-password"
    python scripts/dump_all.py

Options::

    --vin LNBX...       Only query this VIN (default: all vehicles)
    --json               Output as machine-readable JSON
    --output FILE        Write output to FILE instead of stdout
    --skip-gps           Skip GPS endpoint
    --skip-energy        Skip energy consumption endpoint
    --skip-charging      Skip charging status endpoint
    --skip-hvac          Skip HVAC status endpoint
    --skip-realtime      Skip realtime endpoint
    --skip-latest-config Skip latest capability-config endpoint

China (CN) example::

    export BYD_BASE_URL="https://dilinksuperappserver-cn.byd.auto"
    export BYD_USERNAME="13800138000"
    export BYD_PASSWORD="your-password"
    export BYD_APP_CHANNEL="99"
    export BYD_CN_APP_VERSION="9.10.2"
    export BYD_CN_APP_INNER_VERSION="502"
    export BYD_TARGET_BRAND="1"
    python scripts/dump_all.py --json --output dump-cn.json

Text and JSON output include ``region`` (``cn`` / ``overseas``) and ``base_url`` so dumps are self-describing.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# Allow running from the repo root without installing the package.
_repo = Path(__file__).resolve().parent.parent
_src = _repo / "src"
if _src.is_dir():
    sys.path.insert(0, str(_src))

# pylint: disable=wrong-import-position
from pybyd import BydClient, BydConfig  # noqa: E402

# ── helpers ──────────────────────────────────────────────────


def _model_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a model object to a plain dict (shallow, keeps raw)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    return {"__repr__": repr(obj)}


def _enum_name(val: Any) -> str:
    """Return the enum name if it's an enum, else str(val)."""
    if hasattr(val, "name") and hasattr(val, "value"):
        return f"{val.name} ({val.value})"
    return str(val)


def _section(title: str) -> str:
    line = "=" * 60
    return f"\n{line}\n  {title}\n{line}"


def _format_field(key: str, value: Any, indent: int = 2) -> str:
    prefix = " " * indent
    if isinstance(value, list):
        if not value:
            return f"{prefix}{key}: []"
        lines = [f"{prefix}{key}:"]
        for item in value:
            if isinstance(item, BaseModel):
                for f_key, f_val in item.model_dump().items():
                    lines.append(_format_field(str(f_key), f_val, indent + 4))
                lines.append("")
            elif dataclasses.is_dataclass(item) and not isinstance(item, type):
                for f in dataclasses.fields(item):
                    lines.append(_format_field(f.name, getattr(item, f.name), indent + 4))
                lines.append("")
            else:
                lines.append(f"{prefix}    - {item}")
        return "\n".join(lines)
    if isinstance(value, dict):
        return f"{prefix}{key}: <dict with {len(value)} keys>"
    return f"{prefix}{key}: {_enum_name(value)}"


def _print_model(name: str, obj: Any, out: list[str]) -> dict[str, Any]:
    """Pretty-print a dataclass model and return its dict form."""
    out.append(_section(name))
    d = _model_to_dict(obj)
    for key, value in d.items():
        if key == "raw":
            continue
        out.append(_format_field(key, value))
    return d


def _print_raw(name: str, raw: dict[str, Any], out: list[str]) -> None:
    """Pretty-print the raw API dict."""
    out.append(f"\n  ── {name} (raw JSON) ──")
    out.append(json.dumps(raw, indent=2, default=str, ensure_ascii=False))


# ── main ─────────────────────────────────────────────────────


async def dump_vehicle(
    client: BydClient,
    vin: str,
    *,
    skip: set[str],
    json_mode: bool,
) -> dict[str, Any]:
    """Fetch and dump all data for a single vehicle."""
    out: list[str] = []
    vehicle_data: dict[str, Any] = {"vin": vin}

    # ── Realtime ──
    if "realtime" not in skip:
        out.append(_section(f"REALTIME  vin={vin}"))
        try:
            rt = await client.get_vehicle_realtime(vin)
            d = _print_model("Realtime (parsed)", rt, out)
            _print_raw("Realtime", rt.raw, out)
            vehicle_data["realtime"] = {"parsed": d, "raw": rt.raw}
        except Exception as exc:
            msg = f"  !! realtime failed: {exc}"
            out.append(msg)
            vehicle_data["realtime"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── GPS ──
    if "gps" not in skip:
        out.append(_section(f"GPS  vin={vin}"))
        try:
            gps = await client.get_gps_info(vin)
            d = _print_model("GPS (parsed)", gps, out)
            _print_raw("GPS", gps.raw, out)
            vehicle_data["gps"] = {"parsed": d, "raw": gps.raw}
        except Exception as exc:
            msg = f"  !! gps failed: {exc}"
            out.append(msg)
            vehicle_data["gps"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── Energy ──
    if "energy" not in skip:
        out.append(_section(f"ENERGY  vin={vin}"))
        try:
            energy = await client.get_energy_consumption(vin)
            d = _print_model("Energy (parsed)", energy, out)
            _print_raw("Energy", energy.raw, out)
            vehicle_data["energy"] = {"parsed": d, "raw": energy.raw}
        except Exception as exc:
            msg = f"  !! energy failed: {exc}"
            out.append(msg)
            vehicle_data["energy"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── Charging ──
    if "charging" not in skip:
        out.append(_section(f"CHARGING  vin={vin}"))
        try:
            ch = await client.get_charging_status(vin)
            d = _print_model("Charging (parsed)", ch, out)
            _print_raw("Charging", ch.raw, out)
            vehicle_data["charging"] = {"parsed": d, "raw": ch.raw}
        except Exception as exc:
            msg = f"  !! charging failed: {exc}"
            out.append(msg)
            vehicle_data["charging"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── HVAC ──
    if "hvac" not in skip:
        out.append(_section(f"HVAC  vin={vin}"))
        try:
            hvac = await client.get_hvac_status(vin)
            d = _print_model("HVAC (parsed)", hvac, out)
            _print_raw("HVAC", hvac.raw, out)
            vehicle_data["hvac"] = {"parsed": d, "raw": hvac.raw}
        except Exception as exc:
            msg = f"  !! hvac failed: {exc}"
            out.append(msg)
            vehicle_data["hvac"] = {"error": str(exc), "traceback": traceback.format_exc()}

    # ── Latest Config ──
    if "latest_config" not in skip:
        out.append(_section(f"LATEST CONFIG  vin={vin}"))
        try:
            latest_config = await client.get_latest_config(vin)
            d = _print_model("Latest config (parsed)", latest_config, out)
            _print_raw("Latest config", latest_config.raw, out)
            vehicle_data["latest_config"] = {"parsed": d, "raw": latest_config.raw}
        except Exception as exc:
            msg = f"  !! latest_config failed: {exc}"
            out.append(msg)
            vehicle_data["latest_config"] = {"error": str(exc), "traceback": traceback.format_exc()}

    if not json_mode:
        print("\n".join(out))

    return vehicle_data


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump all data pybyd can fetch for debugging / development.",
    )
    parser.add_argument("--vin", help="Only query this VIN (default: all vehicles)")
    parser.add_argument("--json", action="store_true", dest="json_mode", help="Output machine-readable JSON")
    parser.add_argument("--output", "-o", help="Write output to FILE instead of stdout")
    parser.add_argument("--skip-gps", action="store_true", help="Skip GPS endpoint")
    parser.add_argument("--skip-energy", action="store_true", help="Skip energy consumption endpoint")
    parser.add_argument("--skip-charging", action="store_true", help="Skip charging status endpoint")
    parser.add_argument("--skip-hvac", action="store_true", help="Skip HVAC status endpoint")
    parser.add_argument("--skip-realtime", action="store_true", help="Skip realtime endpoint")
    parser.add_argument("--skip-latest-config", action="store_true", help="Skip latest capability-config endpoint")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    skip: set[str] = set()
    if args.skip_gps:
        skip.add("gps")
    if args.skip_energy:
        skip.add("energy")
    if args.skip_charging:
        skip.add("charging")
    if args.skip_hvac:
        skip.add("hvac")
    if args.skip_realtime:
        skip.add("realtime")
    if args.skip_latest_config:
        skip.add("latest_config")

    config = BydConfig.from_env()
    region = "cn" if config.is_china_region else "overseas"
    result: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "region": region,
        "base_url": str(config.base_url),
        "app_version": config.app_version,
        "app_inner_version": config.app_inner_version,
        "vehicles": [],
    }

    out: list[str] = []
    out.append(_section("pybyd dump_all"))
    out.append(f"  time      : {result['timestamp']}")
    out.append(f"  region    : {region}")
    out.append(f"  base_url  : {config.base_url}")
    out.append(f"  app_ver   : {config.app_version} (inner {config.app_inner_version})")

    async with BydClient(config) as client:
        await client.login()
        session = await client.ensure_session()
        out.append(f"  user_id   : {session.user_id}")
        result["user_id"] = session.user_id

        if not args.json_mode:
            print("\n".join(out))

        # ── Vehicles ──
        vehicles = await client.get_vehicles()
        veh_out: list[str] = []
        veh_out.append(_section("VEHICLES"))

        for v in vehicles:
            d = _print_model(f"Vehicle vin={v.vin}", v, veh_out)
            _print_raw(f"Vehicle vin={v.vin}", v.raw, veh_out)
            result["vehicles"].append({"info": d, "raw": v.raw})

        if not args.json_mode:
            print("\n".join(veh_out))

        # ── Per-vehicle endpoints ──
        target_vins = [args.vin] if args.vin else [v.vin for v in vehicles]

        for vin in target_vins:
            vdata = await dump_vehicle(client, vin, skip=skip, json_mode=args.json_mode)
            # Attach to the matching vehicle entry
            for entry in result["vehicles"]:
                if entry.get("info", {}).get("vin") == vin:
                    entry["data"] = vdata
                    break

    # ── Output ──
    if args.json_mode:
        payload = json.dumps(result, indent=2, default=str, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
            print(f"JSON written to {args.output}", file=sys.stderr)
        else:
            print(payload)
    elif args.output:
        # Re-run with output capture (simple approach: dump JSON anyway)
        Path(args.output).write_text(
            json.dumps(result, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"JSON written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
