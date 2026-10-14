# Nature Remo - Home Assistant Custom Integration

⭐ If this integration helps you, please consider giving it a star on GitHub!

📄 日本語版のREADMEはこちら 👉 [README_ja.md](README_ja.md)

This is a custom integration for linking Nature Remo devices with Home Assistant.  
It enables you to control appliances like air conditioners and lights, and monitor temperature, humidity, and more directly in your smart home setup.

---

## ⚠️ Disclaimer
This is an **unofficial** integration and is not affiliated with Nature Inc. or Home Assistant.  
Please use this integration **at your own risk**.

---

## Features

- Control appliances (air conditioners, lights) registered to Nature Remo
- Retrieve temperature, humidity, illuminance, and motion sensor data
- Access smart meter data (consumption, generation, instant power) via Nature Remo E / E Lite
- Monitor EcoCute water heater states, energy and remaining hot water
- Control lighting modes using custom service calls
- Send IR commands using remote entities created from defined signals
- Press learned IR signals from dashboard buttons

---

## Installation (via HACS)

Click the button below to easily add this repository to HACS.

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=NaNaLinks&repository=homeassistant_nature_remo&category=integration)

1. Open HACS in Home Assistant
2. Click the menu (⋮) in the top right corner
3. Select "Custom repositories"
4. Add this repository URL:
   https://github.com/NaNaLinks/homeassistant_nature_remo  
   Category: Integration
5. Install "Nature Remo"
6. Restart Home Assistant

---

## Installation (Manual)

1. Download or clone this repository and place it in the following path:

```
<config directory>/custom_components/nature_remo/
```

2. Restart Home Assistant.

---

## Setup Instructions

1. Go to *Settings → Devices & Services → Add Integration* and search for `Nature Remo`
2. Enter your access token (API key) and integration name
   - You can issue an API token at [Nature Official Site](https://home.nature.global)
3. Your registered appliances will be automatically imported as entities

---

## Options

- Set the update interval (30, 60 or 90 seconds; default: 60).
- Select a Remo device to open its settings section. Only Remo devices with an air conditioner show external temperature and humidity selections.
- Save to apply changes automatically; the integration reloads without a separate notification. Leave the device selection empty to change only the interval.

⚠️ Nature Remo Cloud API has rate limits.  
Setting a very short update interval may cause the integration to reach the API request limit.

---

## Supported Entities

| Type    | Description                                                        |
|---------|--------------------------------------------------------------------|
| climate | Control air conditioners (cooling, heating, dry)                   |
| binary_sensor | Motion detection and water heater operating states |
| number  | Auto/Dry temperature offset for supported air conditioners         |
| light   | Control lights (on/off, mode selection)                            |
| sensor  | Temperature, humidity, illuminance, last motion timestamp, power (buy/sell), external sensor relays, water heater energy/volume/modes |
| remote  | Send learned IR/AC/LIGHT signals and TV preset or learned commands |
| button | Send each learned signal on any appliance |

*Additional entities may be supported in future updates.*

---

## Migration to v0.3.0

Appliances now have their own devices, linked to the physical Remo. Built-in temperature, humidity, illuminance and motion stay on the Remo device. Entity IDs are preserved (except for the motion domain change below), but **reconfigure device-based automations and area assignments** for the new appliance devices. Remo E remains registered as a parent even without built-in sensor entities.

Motion sensors move from `sensor.<object_id>` to `binary_sensor.<object_id>`. The integration preserves the object ID and your name, icon, area, disabled and hidden settings, then removes the old registry entry. If the new ID is already occupied, Home Assistant chooses a free ID; the repair lists the actual mapping.

Update automations, scripts and dashboards to reference the new `binary_sensor.` IDs. **Settings → System → Repairs** lists every changed ID; dismiss the repair after updating your references. Last motion sensors retain their IDs and now provide a timestamp.

---

## Air Conditioner Controls

Air conditioners use the v2 API and expose fan speed, vertical swing and horizontal swing when the model supports them. Temperature units follow the appliance settings.

- Turning on an air conditioner that is off always starts **Auto** mode. Calling turn on while it is running does nothing. Models without Auto do not offer turn on; choose a supported HVAC mode instead.
- Auto and Dry modes with relative temperatures expose a **Temperature offset** number (-3 to +3, step 0.5). It is unavailable in other modes or while off. The climate entity does not display an absolute target temperature in these modes.
- The last confirmed offset is reused when starting Auto. Commands update both entities only after a successful response.

---

## Light Modes

Use `nature_remo.send_light_mode` with `entity_id` and a supported button name in `mode` to control a light. The service finds the light's account each time, including when multiple accounts are configured. Disabled, unloaded or missing lights and unsupported modes return an error before sending a command. State changes only after a successful send.

When response data is requested, the result remains `{"status": "success", "appliance_id": "..."}`.

---

## Learned Signal Buttons

Each signal registered in the Remo app creates a button with the signal name on its appliance device, including IR, AC, LIGHT and TV appliances. Press it from a dashboard or with `button.press`. Existing remote entities continue to work. Reload the integration after adding signals.

Special AC operations such as internal cleaning can be used by learning the operation as a signal in the Remo app. API `extras` are not exposed as entities. Send failures appear as Home Assistant errors.

---

## Sample: Using Remote Entities

This integration supports `remote` entities generated from Nature Remo's defined `signals`. These entities allow you to send IR commands directly from Home Assistant.

### Example: Service Call

You can call a signal like this using `remote.send_command`:

```yaml
service: remote.send_command
target:
  entity_id: remote.living_room_remote  # Your remote entity ID
data:
  command: "Power On"  # The name of the signal as defined in Remo
```

---

## TV Remotes

Every TV registered in Remo gets a remote entity, even without learned signals. Use `remote.send_command` with a preset button name (such as `vol-up`), its label, or a learned signal name; matching ignores letter case. Unknown commands return an error. The `input` attribute shows the API state and updates from successful command responses and normal polling.

Only TVs with both `power-on` and `power-off` support `remote.turn_on` / `remote.turn_off`. HA cannot hide those shared remote actions for individual TVs: on toggle-only models, they return an unsupported error without sending. To explicitly toggle power, send `power` with `remote.send_command`. Power starts unknown; after a discrete send, the displayed state is assumed, since infrared cannot confirm the TV's physical state.

TV behavior is covered by mocked tests; the previously used REGZA 32ZP2 is no longer available for hardware verification. TV is provided as remote, without media_player features.

---

## EcoCute Water Heaters

Remo E water heaters (`EL_ELECTRIC_WATER_HEATER`) expose read-only sensors on their appliance device:

- Operating, water-heating and hot-water-supply states; daytime heating permission and automatic bath mode
- Cumulative energy in kWh (`total_increasing`), suitable for the Energy dashboard
- Remaining hot water in liters; water-heating mode and bath operation

Only supported properties in the first successful response create entities. Values are fetched by a separate five-minute poll, enabled only when a supported heater is present. Reload after adding a heater or new properties. Invalid or missing readings remain unknown; valid zero values are preserved. Failed polls keep the previous snapshot and mark entities unavailable until a successful poll. A temporary heater API failure during startup does not block other appliances; heater sensors appear automatically when the first fetch succeeds. Property timestamps are not used to infer availability, and no heater controls are provided.

---

## External Temperature and Humidity Sensors

Configure external temperature and humidity sensors for each Remo device that has an air conditioner.

The climate entity reads external temperature and humidity through these relay sensors. If a relay is unconfigured, unavailable or disabled, it falls back to the corresponding built-in Remo reading. When the source recovers, climate updates immediately.

### How it works

- Open the integration settings from Home Assistant
- Select the Remo device, then continue to its settings section
- Choose temperature and humidity sensor entities, or clear a selection to return to the built-in sensor
- Save; changes are applied by an automatic reload

Each configured air conditioner also gets an **External temperature** and/or **External humidity** sensor on its appliance device. These relay the source value, device class, state class and native unit without additional API calls. They update immediately when the source changes and become unavailable when it is missing or nonnumeric. The initial display unit follows the source; Home Assistant unit preferences can then override it.

Once configured, the selected external sensors will be used for:

- Displaying temperature and humidity in the climate entity
- Providing more accurate environmental data for air conditioner control

### Notes

- With no external sensor, or while its relay is unavailable, climate uses the built-in Remo value. If that value is also missing, the measurement is unknown.
- The selected entity must exist and have a finite numeric state. Temperature must use °C or °F, and humidity must use %.
- Choose the original sensor as the source. Nature Remo relay sensors cannot be selected, to avoid self-references and cycles.
- Existing external sensor selections are preserved when upgrading. Editing one Remo leaves other devices unchanged.

---

## Author

- Author: [@nanosns](https://github.com/nanosns) (NaNaRin)
- Project: [@NaNaLinks](https://github.com/NaNaLinks)
- Socials: [note](https://note.com/nanomana)

---

## License

MIT License