# Switch Vision UniFi2MQTT

Switch Vision UniFi2MQTT is the optional UniFi Network API bridge used by **Switch Vision**.
It reads adopted UniFi switching devices through the official read-only UniFi Network Integration API, normalizes switch and port state, publishes Home Assistant MQTT Discovery data, and writes a normalized snapshot for Switch Vision Discovery.

> **Status:** Experimental hardware-support component. Current standalone release line: v2.0.44.

## What it does

- Uses the UniFi Network Integration API with `X-API-KEY` authentication.
- Reads adopted switching devices, device details and latest statistics.
- Publishes retained MQTT state and Home Assistant MQTT Discovery entities.
- Writes `/share/switch_vision/unifi/devices.json` for Switch Vision Discovery.
- Writes privacy-safe `/share/switch_vision/unifi/diagnostics.json` on successful and failed polling so Support My Switch can diagnose controller/classification problems without exposing credentials or device identifiers.
- Keeps UniFi API collection separate from SNMP2MQTT.
- Preserves the previous device snapshot if a transient per-device API refresh fails.
- Requires three consecutive successful no-switch polls before retiring every previously known switch.
- Validates MQTT topic prefixes and controller URL structure before connecting.
- Stores the normalized snapshot with owner-only permissions and serialized writers.
- Never calls UniFi write/action endpoints.

## Supported live-tested models

Current Switch Vision evidence includes:

- USW Lite 16 PoE
- USW Pro 24 PoE
- USW Enterprise 8 PoE
- USW Pro XG 8 PoE
- US 48 PoE 500W
- UDM Pro gateway/switch hybrid

Support remains contribution-driven and model validation status is maintained by the main Switch Vision device registry.

## Home Assistant App repository

This repository is laid out as a Home Assistant App repository. The app lives in:

```text
switch-vision-unifi2mqtt/
```

Switch Vision Installer can manage this component as an **optional** UniFi support dependency.

## Configuration

Required:

- `controller_url`
- `api_key`

Site selection:

- `site_id` defaults to `auto`.
- UniFi2MQTT first queries the local Network API site list and resolves the actual site UUID automatically.
- `default` is accepted as an automatic/default-site selector.
- Multi-site installations may specify the site UUID, exact site name, or internal reference.

By default, the app resolves Home Assistant's Supervisor MQTT service automatically. `mqtt_host`, MQTT credentials and other MQTT fields remain available as optional overrides for custom brokers.

Optional MQTT and polling settings are exposed through the Home Assistant App configuration UI and Switch Vision Hub when the app is installed.

The API key and MQTT password are treated as secrets and are not written into the normalized snapshot. New installations default to TLS certificate verification; disabling verification remains available for self-signed local controllers and produces an explicit runtime warning.

## Data model

Normalized data is written to:

```text
/share/switch_vision/unifi/devices.json
```

The current UniFi API provides reliable link, negotiated speed, connector, PoE and system/uplink information. Per-port traffic is not fabricated when the API does not expose it.

## Activity LEDs

Switch Vision per-port Activity LEDs require per-port RX/TX traffic data. The UniFi Network API does not currently provide reliable per-port traffic data across supported UniFi hardware, so **SNMP is required for per-port Activity LED animation**.

UniFi-only installations still provide supported port link state, negotiated speed, connector, PoE and system telemetry.

## Validation

Run the offline regression test with:

```bash
python3 switch-vision-unifi2mqtt/self-test.py
```

The GitHub Actions validation workflow also checks Python syntax, shell syntax, configuration/version consistency and the UniFi regression suite.

## Related projects

- Switch Vision
- Switch Vision Installer
- Switch Vision SNMP2MQTT

Switch Vision is a community project and is not affiliated with or endorsed by Ubiquiti Inc.
