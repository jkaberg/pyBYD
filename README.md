# pybyd

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

## China (CN) region

When the server hostname is China (for example `https://dilinksuperappserver-cn.byd.auto`), pyBYD uses the CN stack: WBSK request/response envelopes, `/app/auth/login`, `/app/auth/getAllListByUserId`, a single-shot GPS call to `/vehicleInfo/gps/locationRequestService`, and CN MQTT broker/topic naming (`dynasty_…` client id, `/dynasty/res/…` topics when using Dynasty branding).

Set the base URL and optional CN app headers (see [BYD-re](https://github.com/Niek/BYD-re) for reference values):

```bash
export BYD_BASE_URL="https://dilinksuperappserver-cn.byd.auto"
export BYD_USERNAME="13800138000"   # phone-style account id, as in the CN app
export BYD_PASSWORD="your-password"

# Optional CN-specific overrides (defaults match common CN app builds)
export BYD_APP_CHANNEL="99"
export BYD_CN_APP_VERSION="9.10.2"
export BYD_CN_APP_INNER_VERSION="502"
export BYD_TARGET_BRAND="1"
export BYD_VEHICLE_BRAND="1"
export BYD_NETWORK_OPERATOR="无"
# export BYD_BRAND_FLAG="dynasty"   # HTTP BrandFlag header (default dynasty)

# If the hostname is ambiguous, force region explicitly:
# export BYD_REGION=cn
```

`BYD_TARGET_BRAND` selects which EMQ broker field the client reads (`dynastyEmqBroker`, `oceanEmqBroker`, etc.). Session `identifier` for token envelopes and MQTT uses the brand-specific user id when present, otherwise `superId` (see `Session.effective_api_identifier`).

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

Helper tooling is in [scripts/](scripts/) and generally expects `BYD_USERNAME` / `BYD_PASSWORD`. For CN, set `BYD_BASE_URL` and the `BYD_CN_*` / `BYD_APP_CHANNEL` variables from the section above; dumps and mapping tables then reflect the CN API shape (raw key sets differ from overseas, so compare like with like).

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
