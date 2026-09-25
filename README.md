# pybyd

[![CI](https://github.com/jkaberg/pyBYD/actions/workflows/ci.yml/badge.svg)](https://github.com/jkaberg/pyBYD/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pybyd)](https://pypi.org/project/pybyd/)
[![Python](https://img.shields.io/pypi/pyversions/pybyd)](https://pypi.org/project/pybyd/)
[![License](https://img.shields.io/github/license/jkaberg/pyBYD)](LICENSE)
[![Buy me a coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/jkaberg)

Async Python client for the BYD vehicle API.

`pybyd` focuses on two things:

1. parse BYD responses into typed Pydantic models,
2. send remote commands and return typed results.

Status: **Alpha** (API may evolve before 1.0).

## Installation

Requires Python 3.11+.

```bash
pip install pybyd
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick start (BydClient)

```python
import asyncio
from pybyd import BydClient, BydConfig


async def main() -> None:
    config = BydConfig.from_env()

    async with BydClient(config) as client:
        vehicles = await client.get_vehicles()
        vin = vehicles[0].vin

        realtime = await client.get_vehicle_realtime(vin)
        gps = await client.get_gps_info(vin)

        print(f"VIN: {vin}")
        print(f"Battery: {realtime.elec_percent}%")
        print(f"Location: {gps.latitude}, {gps.longitude}")


asyncio.run(main())
```

`BydConfig.from_env()` supports `BYD_*` environment variables (including MQTT and control PIN settings).

## Remote commands

Remote commands require a control PIN (`BydConfig(control_pin=...)` or `command_pwd=...`) and a one-time verification step:

```python
from pybyd.models import ClimateStartParams, minutes_to_time_span

await client.verify_command_access(vin)

# Door lock
lock_result = await client.lock(vin)
print(lock_result.success)

# Start HVAC at 21°C for 20 minutes
params = ClimateStartParams(temperature=21.0, time_span=minutes_to_time_span(20))
climate_result = await client.start_climate(vin, params=params)
print(climate_result.success)
```

Supported command methods include:

- `lock` / `unlock`
- `start_climate` / `stop_climate` / `schedule_climate`
- `set_seat_climate` / `set_battery_heat`
- `find_car` / `flash_lights` / `close_windows`

## Per-vehicle API (BydCar)

For a higher-level per-VIN workflow, use `BydCar`:

```python
car = await client.get_car(vin)

await car.lock.lock()
await car.hvac.start(temperature=21.0, duration=20)

await car.update_realtime()
print(car.state.realtime)
```

## MQTT and command completion

When MQTT is enabled (default), command completion is MQTT-first with HTTP fallback:

1. trigger command via HTTP,
2. wait briefly for MQTT `remoteControl` result,
3. fall back to HTTP polling if needed.

You can subscribe to command events with `on_command_ack` and lifecycle transitions with `on_command_lifecycle` in `BydClient(...)`.

## Error handling

```python
from pybyd import BydApiError, BydAuthenticationError, BydRemoteControlError

try:
    await client.login()
except BydAuthenticationError as e:
    print(f"Login failed: {e}")

try:
    await client.lock(vin)
except BydRemoteControlError as e:
    print(f"Command failed: {e}")
except BydApiError as e:
    print(f"API error: {e.code} at {e.endpoint}")
```


## Scripts

Helper tooling is in [scripts/](scripts/) and generally expects `BYD_USERNAME` / `BYD_PASSWORD`.

- [scripts/dump_all.py](scripts/dump_all.py): fetch and print endpoint data
- [scripts/data_diff.py](scripts/data_diff.py): interactive poll-and-diff for changed fields
- [scripts/diff_dumps.py](scripts/diff_dumps.py): compare two saved JSON dumps
- [scripts/generate_api_mapping_tables.py](scripts/generate_api_mapping_tables.py): build mapped/unmapped field tables
- [dump_and_diff.sh](dump_and_diff.sh): convenience wrapper for dump + diff workflow

## Credits

- [Niek/BYD-re](https://github.com/Niek/BYD-re)
- [TA2k/ioBroker.byd](https://github.com/TA2k/ioBroker.byd)

## License

MIT
