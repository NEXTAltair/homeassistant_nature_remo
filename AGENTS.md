# AGENTS.md

Guidance for coding agents (Claude Code, Codex, and others) and contributors working in this repository. `CLAUDE.md` imports this file, so keep everything here.

## Project

Unofficial Home Assistant custom integration (domain `nature_remo`) for the Nature Remo Cloud API (`https://api.nature.global/1`). It is distributed through HACS (`hacs.json`, `content_in_root: false`) or by copying `custom_components/nature_remo/` into a Home Assistant config directory. The release version lives in `custom_components/nature_remo/manifest.json` (`version`).

This repository is the fork `NEXTAltair/homeassistant_nature_remo` of `NaNaLinks/homeassistant_nature_remo` (the upstream).

## Project Structure

- `custom_components/nature_remo/` holds the integration source:
  - `api.py` handles cloud requests and parses smart meter values.
  - `coordinator.py` manages polling.
  - `config_flow.py` and `options_flow.py` handle configuration.
- `climate.py`, `light.py`, `sensor.py`, `remote.py`, `number.py` and `binary_sensor.py` are the entity platforms in that package.
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

**One coordinator per config entry.** `__init__.py:async_setup_entry`:
1. Creates `NatureRemoAPI(entry.data["api_key"], async_get_clientsession(hass))` and a `NatureRemoCoordinator`.
2. Stores both in `hass.data[DOMAIN][entry.entry_id] = {"coordinator", "api"}`.
3. Registers the `nature_remo.send_light_mode` service.
4. Forwards to the platforms `climate`, `light`, `sensor`, `remote`, `number` and `binary_sensor`.

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
- **`light.py`** subclass plain `ClimateEntity` / `LightEntity`.
  - They hold private `_state` fields and subscribe manually via `coordinator.async_add_listener(self.update_status)` in `async_added_to_hass`.
  - After a command succeeds, they set state from the API response.
- **`climate.py` / `number.py` / `sensor.py` / `remote.py` / `binary_sensor.py`** use `CoordinatorEntity` and read from the coordinator dicts in properties.
  - Motion is registered by the `binary_sensor` platform. `migration.py` transfers old sensor-domain registry entries before platform setup, preserving object IDs and user settings, and creates a persistent, dismissible repair with the ID mapping.
  - Last motion remains a sensor with `SensorDeviceClass.TIMESTAMP` and a timezone-aware datetime value.
  - Motion is "on" if the last `mo` event was within 5 minutes.
- Climate and number share an `AirconControl` per appliance (`hass.data[DOMAIN][entry_id]["aircon_controls"]`). It publishes successful v2 ApplianceResponse objects through the coordinator and remembers the last confirmed relative offset.

**Device grouping:** `device.py` builds all device information. Setup explicitly registers physical Remo devices, including sensorless Remo E hardware, before registering appliances. Hardware identifiers remain `(DOMAIN, remo_device_id)`; appliances use `(DOMAIN, appliance_id)` with nickname and model manufacturer/name. `async_update_device(via_device_id=...)` links each appliance to its hardware (supported since before HA 2025.12, avoiding the now-deprecated `via_device` argument). All appliance entities use the shared helper; built-in environmental and motion sensors remain on the hardware. Entity unique IDs and registered entity IDs stay unchanged. Options list only physical Remo registry devices, never appliance children.

**Options flow** (`options_flow.py`):
- The schema keys are *display labels* (Japanese or English, depending on `hass.config.language`). On submit they are mapped back to storage keys via `label_key_map`.
- Stored keys:
  - `update_interval` (30/60/90)
  - `<HA device.id>`: an IP address, currently unused elsewhere
  - `external_temperature_<remo_device_id>`
  - `external_humidity_<remo_device_id>`
- There is no options update listener. Saving posts a persistent notification asking the user to reload.
- `translations/{en,ja}.json` cover only the config flow (user and reauth steps). The options flow still relies on its label keys.

**External sensors (upstream Issue 4):**
- `NatureRemoClimate` stores only `entry_id` and re-reads `entry.options` on each update.
- If an external sensor entity is configured, its state overrides the Remo `te`/`hu` value.
- `async_track_state_change_event` writes climate state when that sensor changes.
- `self.hass` is only available after `async_added_to_hass`.

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

**Remote:**
- Commands map case-insensitively to signal names from the Remo app.
- Signals named `on`/`オン` and `off`/`オフ` back `turn_on`/`turn_off`.
- Sends `POST /signals/{id}/send`.

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
  - `nature_remo_sensor_{id}_{key}`
  - `{device_id}_motion`
  - `{device_id}_last_motion`
- User-visible text goes in both `translations/en.json` and `translations/ja.json`.
- The Nature Remo Cloud API is rate-limited to 30 requests per 5 minutes per account. Avoid adding extra polling calls.

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
