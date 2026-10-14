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
- Control lighting modes using custom service calls
- Send IR commands using remote entities created from defined signals

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

- You can set the update interval (in seconds)
  - Default: `60 seconds`

⚠️ Nature Remo Cloud API has rate limits.  
Setting a very short update interval may cause the integration to reach the API request limit.

---

## Supported Entities

| Type    | Description                                                        |
|---------|--------------------------------------------------------------------|
| climate | Control air conditioners (cooling, heating, dry)                   |
| binary_sensor | Motion detection                                             |
| number  | Auto/Dry temperature offset for supported air conditioners         |
| light   | Control lights (on/off, mode selection)                            |
| sensor  | Temperature, humidity, illuminance, last motion timestamp, power (buy/sell)      |
| remote  | Send infrared signals defined as "signals" for IR/AC/LIGHT types  |

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

## External Temperature and Humidity Sensors

You can now configure external temperature and humidity sensors for each device.

By selecting entities from Home Assistant settings, the climate device will use the specified sensors instead of the default values provided by Nature Remo.

### How it works

- Open the integration settings from Home Assistant
- Select a device
- Choose temperature and humidity entities from available sensors
- Save the configuration

Once configured, the selected external sensors will be used for:

- Displaying temperature and humidity in the climate entity
- Providing more accurate environmental data for air conditioner control

### Notes

- If no external sensors are configured, the integration will continue to use the default values from Nature Remo
- Any sensor entity with appropriate temperature or humidity values can be used

---

## Author

- Author: [@nanosns](https://github.com/nanosns) (NaNaRin)
- Project: [@NaNaLinks](https://github.com/NaNaLinks)
- Socials: [note](https://note.com/nanomana)

---

## License

MIT License