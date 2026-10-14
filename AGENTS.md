# AGENTS.md

Guidance for coding agents (Claude Code, Codex, and others) and contributors working in this repository. `CLAUDE.md` imports this file, so keep everything here.

## Project

Unofficial Home Assistant custom integration (domain `nature_remo`) for the Nature Remo Cloud API (`https://api.nature.global/1`). It is distributed through HACS (`hacs.json`, `content_in_root: false`) or by copying `custom_components/nature_remo/` into a Home Assistant config directory. The release version lives in `custom_components/nature_remo/manifest.json` (`version`).

This repository is the fork `NEXTAltair/homeassistant_nature_remo` of `NaNaLinks/homeassistant_nature_remo` (the upstream).

## Project Structure

- `custom_components/nature_remo/` holds the integration source:
  - `api.py` handles cloud requests and parses smart meter values.
  - `coordinator.py` manages normal polling; `echonetlite.py` manages optional water heater polling and hexadecimal parsing.
  - `config_flow.py` and `options_flow.py` handle configuration.
- `climate.py`, `light.py`, `sensor.py`, `remote.py`, `number.py`, `binary_sensor.py` and `button.py` are the entity platforms in that package.
- `translations/`, `services.yaml` and `manifest.json` hold localized text, service definitions and integration metadata.
- `tests/` has the API, coordinator and config flow tests. Shared fixtures live in `conftest.py`.
- `README.md` (English) and `README_ja.md` (Japanese) mirror each other section by section. Update both for user-facing changes.

## Build, Test, and Development Commands

Tests use `pytest-homeassistant-custom-component` in a local `.venv` (gitignored). There is no build step, linter config or CI.

```sh
uv venv .venv
uv pip install --python .venv/bin/python -U -r requirements_test.txt   # latest HA and test harness
.venv/bin/python -m pytest                                              # all tests
.venv/bin/python -m pytest tests/test_api.py -k missing                 # a subset
```

`requirements_test.txt` is intentionally unpinned. Always test against the latest Home Assistant release, and never pin HA or the test harness. Use the Python version that the latest HA requires (3.14 as of HA 2026.10).

The final check is loading the integration in a real Home Assistant instance:
1. Copy or symlink `custom_components/nature_remo/` into `<config>/custom_components/`.
2. Restart Home Assistant.
3. Add Nature Remo under Settings → Devices & Services.

## Architecture

**One main coordinator per config entry, plus an optional ECHONET Lite coordinator.** `__init__.py:async_setup_entry`:
1. Creates `NatureRemoAPI(entry.data["api_key"], async_get_clientsession(hass))` and a `NatureRemoCoordinator`.
2. Stores the main coordinator, API, shared aircon controls and optional `echonetlite_coordinator` in `hass.data[DOMAIN][entry.entry_id]`.
3. Registers the `nature_remo.send_light_mode` service.
4. Sets up `sensor` first so external relays are ready, then forwards to `climate`, `light`, `remote`, `number`, `binary_sensor` and `button`.

**The coordinator does two things per poll** (`coordinator.py:_async_update_data`). The interval comes from `entry.options["update_interval"]` and defaults to 60 s. It calls `GET /devices` and `GET /appliances`, then:
- returns `coordinator.data` = `{appliance_id: raw appliance JSON}`. Climate and light read their live state (`settings`, `aircon.range.modes`, `light.state`) from here.
- as a side effect, rebuilds categorized dicts that platforms use to decide *which entities exist*:
  - `devices`: Remo hardware plus `newest_events` te/hu/il
  - `motion_sensors`
  - `aircons`: type `AC`
  - `lights`: type `LIGHT`
  - `smart_meters`: type `EL_SMART_METER`
  - `ir_remotes`: any `AC`, `LIGHT` or `IR` appliance with `signals`

The new snapshot is built into local dicts and swapped in only after both responses are lists. A failed or malformed poll keeps the previous data. Empty lists are a valid snapshot.

Error mapping:
- `NatureRemoAuthError` (401/403) → `ConfigEntryAuthFailed`, which starts `config_flow.async_step_reauth`.
- `NatureRemoRateLimitError` (429) → `UpdateFailed(retry_after=…)`, derived from `X-Rate-Limit-Reset`.
- Other failures → `UpdateFailed`.

Entities are created only once, at platform setup, from those dicts. Newly added appliances therefore need an integration reload.

**Entity patterns:**
- **`light.py`** uses plain `LightEntity`.
  - They hold private `_state` fields and subscribe manually via `coordinator.async_add_listener(self.update_status)` in `async_added_to_hass`.
  - After a command succeeds, they set state from the API response.
- **`climate.py` / `number.py` / `sensor.py` / `remote.py` / `binary_sensor.py` / `button.py`** use `CoordinatorEntity` and read from the coordinator dicts in properties.
  - Motion is registered by the `binary_sensor` platform. `migration.py` transfers old sensor-domain registry entries before platform setup, preserving object IDs and user settings, and creates a persistent, dismissible repair with the ID mapping.
  - Last motion remains a sensor with `SensorDeviceClass.TIMESTAMP` and a timezone-aware datetime value.
  - Motion is "on" if the last `mo` event was within 5 minutes.
- Climate and number share an `AirconControl` per appliance (`hass.data[DOMAIN][entry_id]["aircon_controls"]`). It publishes successful v2 ApplianceResponse objects through the coordinator and remembers the last confirmed relative offset.

**Device grouping:** `device.py` builds all device information. Setup explicitly registers physical Remo devices, including sensorless Remo E hardware, before registering appliances. Hardware identifiers remain `(DOMAIN, remo_device_id)`; appliances use `(DOMAIN, appliance_id)` with nickname and model manufacturer/name. `async_update_device(via_device_id=...)` links each appliance to its hardware (supported since before HA 2025.12, avoiding the now-deprecated `via_device` argument). All appliance entities use the shared helper; built-in environmental and motion sensors remain on the hardware. Entity unique IDs and registered entity IDs stay unchanged. Options list only physical Remo registry devices, never appliance children.

**Options flow** (`options_flow.py`):
- The first step selects the update interval and optional Remo hardware. The second step uses a fixed `device` section (`ip_address`, `external_temperature`, `external_humidity`), translated with the selected hardware name. External fields appear only for hardware with an AC.
- Existing flat storage keys remain unchanged. Editing one device preserves all others. Optional sensor selections use suggestions, so clearing them removes the saved key. Selections must exist, have finite numeric states, and use °C/°F for temperature or % for humidity.
- Stored keys:
  - `update_interval` (30/60/90)
  - `<HA device.id>`: an IP address, currently unused elsewhere
  - `external_temperature_<remo_device_id>`
  - `external_humidity_<remo_device_id>`
- The options update listener reloads the integration after saving and is removed on unload. No manual reload notification is posted.
- `translations/{en,ja}.json` provide config and options fields, sections, errors, entity names and repairs.

**External sensors:**
- `external_sensor.py` defines nonpolling relay sensors, one per configured type and AC, attached to the AC appliance device. The sensor platform stores instances in `hass.data[DOMAIN][entry_id]["external_sensors"][appliance_id][kind]`.
- Relays subscribe to source state events on addition and unsubscribe on removal. They copy finite values, device/state classes and native units; missing or nonnumeric sources become unavailable. Source units are suggested initially, and HA retains control over display unit preferences. Relay listeners notify climate directly, so renaming the relay entity does not break updates. Disabled or removed relays are unavailable to consumers.
- Source options use the existing Remo hardware ID keys; multiple ACs on one hardware receive separate relays. Nature Remo relays across all config entries are excluded as source options and rejected on submission; saved relay sources are skipped to prevent self-references and cycles.

- `NatureRemoClimate` receives its relay instances at setup and reads their native values/units. It never reads external options or source entity states itself. Temperature is converted to the climate native unit.
- If a relay is absent, unavailable or has an incompatible unit, climate falls back to the Remo `te`/`hu` reading. Relay change listeners are removed when climate unloads.

**Climate mode mapping:**
- `MODE_MAP` translates between HA and Remo modes: `HEAT`↔`warm`, `FAN_ONLY`↔`blow`, `COOL`, `DRY`, `AUTO`.
- Supported HVAC modes, the temperature range and step, and the fan and swing modes all derive from the appliance's `aircon.range.modes[<remo_mode>]`.
- Commands POST all seven form fields to `/2/appliances/{id}/aircon_settings`: `operation_mode`, `temperature`, `temperature_unit`, `air_volume`, `air_direction`, `air_direction_h`, `button`. Empty strings must be included.
- TURN_ON starts AUTO only when off and is not exposed without AUTO; TURN_OFF sends `power-off`.
- Relative auto/dry temperatures use a number (-3..3, step 0.5); climate hides its target temperature in those modes. Singleton empty `dir`/`dirh` options mean unsupported swing.

**Light custom service:**
- Light entities register themselves in `coordinator.entity_map[entity_id]`.
- The `send_light_mode` service handler in `__init__.py` looks them up there and validates against the light's button names (`_supported_effects`).
- It then mutates the entity's private state directly.

**Signal buttons:**
- Each signal on any appliance creates a button named after the signal, attached to its appliance device.
- Pressing calls `POST /signals/{id}/send`; API errors propagate to the caller without changing appliance state. Removed signals become unavailable through the existing coordinator.
- Existing remote entities remain available for compatibility. New signals need an integration reload.

**TV remote:**
- `tv_remote.py` creates a remote for every TV, even without learned signals, using `nature_remo_remote_{appliance_id}`. It reads current buttons, signals and `tv.state.input` from the main coordinator.
- Commands resolve case-insensitively in this order: canonical button name, label, learned signal name. Empty-name placeholders are ignored. Unknown commands raise localized service errors before sending.
- Preset commands POST `/appliances/{id}/tv` with `button=<name>`. The successful TVState response is validated and stored in `tv.state`; learned signals use the existing signal endpoint. No extra GET is sent.
- Only TVs with both `power-on` and `power-off` accept turn_on/off. HA remote cannot hide those shared services, so unsupported calls raise localized errors without sending. Explicit `power` is allowed.
- Power starts unknown. Successful discrete sends set an assumed state; a toggle or learned signal clears it. No physical power state is inferred from input.

**Legacy IR remote:**
- Commands map case-insensitively to signal names from the Remo app.
- Signals named `on`/`オン` and `off`/`オフ` back `turn_on`/`turn_off`.
- Sends `POST /signals/{id}/send`.

**Water heaters (EcoCute):**
- Setup creates `NatureRemoEchonetLiteCoordinator` only if the main snapshot contains `EL_ELECTRIC_WATER_HEATER`. It fetches `GET /echonetlite/appliances` separately every 300 seconds and skips the request if no heaters remain in the main snapshot.
- The response wraps an `appliances` list. Both `epc` and `val` are hexadecimal strings; parsing is separate from decimal smart meter values. `Device` is uppercase here; device metadata comes from the main appliance snapshot.
- EPC 80/b2/c0/c3/e3 provide binary states, 85 provides cumulative kWh (`total_increasing`), e1 provides stored liters (`measurement`), and b0/ea provide translated enums. Only supported EPCs in the first successful snapshot create entities; later additions require reload.
- Invalid codes, lengths, non-hex strings and out-of-range numbers become None. Legitimate zeros remain zero. Missing EPCs become unknown; failed or malformed polls preserve the prior snapshot and mark entities unavailable. Authentication starts reauth; rate limits honor reset times.
- A non-authentication failure of the first optional fetch does not block normal platforms. The coordinator retains an unavailable empty snapshot and retries on its timer; sensor and binary_sensor subscribe until the first successful snapshot to create their entities without a reload. Initial authentication failures still start reauth.
- Listener lifetimes belong to the config entry. Discovery and five-minute polling continue even when the heater has no supported EPC entities, and both stop on unload. No write controls or updated_at freshness checks are provided.

**API client** (`api.py`):
- All calls go through `_request`. It logs the `X-Rate-Limit-*` headers and tolerates their absence.
- It raises `NatureRemoError` or a subclass on any non-200 response or invalid JSON. It never returns `None`.
- `NatureRemoError` subclasses `HomeAssistantError`, so a failed command surfaces in the UI. Entities update state only after the call returns; never change state optimistically before the command.
- Smart meter values are decoded from ECHONET Lite EPCs in `parse_smart_meter_properties`: 0xD3 coefficient, 0xE0 cumulative buy, 0xE1 unit, 0xE3 cumulative sell, 0xE7 instantaneous W.
  - These yield `None`: values outside the spec range (e.g. 0xFFFFFFFE = no measurement), unreadable values, an invalid coefficient, and a missing or unknown unit. That shows as unknown in HA. Never use 0, because a fake 0 corrupts `total_increasing` statistics.
  - An absent coefficient defaults to 1, per the spec.
  - `GET /appliances` returns these as **decimal** strings. `GET /echonetlite/appliances` returns ECHONET values as **hex** strings. Don't mix the two parsers.

## Coding Style & Conventions

- Use four-space indentation, `snake_case` functions and variables, `PascalCase` classes and uppercase constants. Follow the surrounding type annotations. No formatter or linter is configured, so keep edits consistent with neighboring code.
- Keep network operations asynchronous and use Home Assistant's shared HTTP session.
- Comments, docstrings and many log messages are bilingual, Japanese then English. Follow this when adding code.
- Existing inline tags such as `# [Issue#4]` refer to **upstream** issues. Fork issue numbers overlap with upstream ones, so tag new code with the fork's issue as `# [fork#N]`.
- Unique IDs must stay stable, because changing them orphans users' entities:
  - `nature_remo_climate_{appliance_id}`
  - `nature_remo_temperature_offset_{appliance_id}`
  - `nature_remo_light_{appliance_id}`
  - `nature_remo_remote_{appliance_id}`
  - `nature_remo_signal_{signal_id}`
  - `nature_remo_water_heater_{appliance_id}_{epc}` (lowercase hexadecimal EPC)
  - `nature_remo_sensor_{id}_{key}`
  - `{device_id}_motion`
  - `{device_id}_last_motion`
  - `nature_remo_external_temperature_{appliance_id}`
  - `nature_remo_external_humidity_{appliance_id}`
- User-visible text goes in both `translations/en.json` and `translations/ja.json`.
- The Nature Remo Cloud API is rate-limited to 30 requests per 5 minutes per account. Avoid adding extra polling calls; the optional five-minute ECHONET Lite poll is the sole exception.

## Testing Guidelines

- Tests use `pytest-homeassistant-custom-component`. `pytest.ini` enables automatic asyncio handling and puts the repo root on `pythonpath`.
- Name files `test_*.py` and functions `test_<behavior>`.
- Mock HTTP with the `aioclient_mock` fixture; this is why the API client must use `async_get_clientsession`. Reuse the appliance builders in `tests/conftest.py`, which are shaped like real API responses.
- Add regression tests for changed behavior, especially authentication, rate limits, malformed responses and missing meter readings. No coverage threshold is configured.
- Never put real API responses in tests: they contain IP and MAC addresses, serial numbers and IDs. Use synthetic values with the same shape.

## Commit & Pull Request Guidelines

- History favors short, action-oriented subjects such as `Fix smart meter missing values` and `Bump version to 0.2.4`.
- PRs should explain the problem, the resulting behavior and the validation (HA version and test results). Include screenshots for visible UI changes.
- Work is tracked as issues on this fork. PRs target this fork's `main`, are squash-merged, and close their issue with `Closes #N`.
- Never push, open PRs or comment upstream.
  - When fork issues, PRs or commits mention upstream, write it as a code span, such as `` `upstream Issue 20` ``.
  - Never write `NaNaLinks/homeassistant_nature_remo#N` or a link to an upstream issue. That posts a cross-reference on the upstream issue.
- Releases:
  1. Bump `version` in `manifest.json` in its own PR.
  2. Tag the merge commit `vX.Y.Z`.
  3. Publish a GitHub Release (not a draft or prerelease). HACS installs from release tags.

## Security & Configuration

- Never commit access tokens, `.env`, `secrets.yaml` or Home Assistant runtime data.
- Don't call the real Nature API from tests or automation. Commands sent to real appliances move real devices.
- Represent unavailable readings as `None`, and keep legitimate zero readings.
