# Switch Vision UniFi2MQTT

Switch Vision UniFi2MQTT is the optional UniFi Network API bridge used by **Switch Vision**.
It reads adopted UniFi switching devices through the official read-only UniFi Network Integration API, normalizes switch and port state, publishes Home Assistant MQTT Discovery data, and writes a normalized snapshot for Switch Vision Discovery.

> **Status:** Experimental hardware-support component. The authoritative release version is defined by `VERSION` and `switch-vision-unifi2mqtt/config.yaml`.

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
- Stores normalized state with owner-only permissions and serialized writers.
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

### Existing single-controller mode

The existing configuration remains supported:

- `controller_url`
- `api_key`
- `site_id` (defaults to `auto`)

When `controllers` is empty, the compatibility launcher hands control directly to the existing single-controller runtime. This preserves the established MQTT topics, Home Assistant unique IDs, snapshot layout, site resolution, retirement behaviour and diagnostics for existing installations.

Site selection continues to work as before:

- `site_id` defaults to `auto`.
- UniFi2MQTT first queries the local Network API site list and resolves the actual site UUID automatically.
- `default` is accepted as an automatic/default-site selector.
- Multi-site controllers may specify the site UUID, exact site name, or internal reference.

### Multi-controller / multi-site mode

Set `controllers` to a non-empty list to poll more than one reachable UniFi controller or gateway in the same UniFi2MQTT instance. Each entry contains:

- `id` — a stable short operator label used to derive an opaque collision-safe controller namespace; the label itself is not written into the shared Switch Vision data tree;
- `controller_url` — the reachable local UniFi Network controller/gateway origin;
- `api_key` — that controller's local Integration API key;
- optional `site_id` — defaults to `auto` and reuses the existing site resolver;
- optional `verify_ssl` and `allow_insecure_http` transport controls.

Example:

```yaml
controllers:
  - id: home
    controller_url: https://10.0.0.1
    site_id: auto
    api_key: YOUR_HOME_API_KEY
  - id: remote
    controller_url: https://10.20.0.1
    site_id: Branch Office
    api_key: YOUR_REMOTE_API_KEY
```

The same controller URL may be listed more than once with different `site_id` values when multiple sites need to run concurrently. Remote controllers must already be reachable by the Home Assistant host; site-to-site VPN connectivity is a supported local-first network design. Cloud-controller access is not part of this implementation.

Multi-controller mode isolates each controller's retirement/previous-snapshot state inside the Home Assistant app's private persistent `/data/multi_controller_state/` area. That private state is intentionally outside `/share/switch_vision`, so Support My Switch does not admit raw per-controller snapshots or operator controller labels. A failed controller therefore cannot trigger retirement of healthy devices from another controller.

The Discovery-facing aggregate remains `/share/switch_vision/unifi/devices.json`. It uses opaque controller-scoped composite device IDs to prevent duplicate raw UniFi device IDs from colliding. The same bounded composite ID is used for Home Assistant MQTT device identity, and Support My Switch's existing UniFi snapshot sanitizer masks it before a contribution package is built.

Removing a controller from configuration retires only that controller's retained MQTT/Home Assistant Discovery topics.

By default, the app resolves Home Assistant's Supervisor MQTT service automatically. `mqtt_host`, MQTT credentials and other MQTT fields remain available as optional overrides for custom brokers.

The API key and MQTT password are treated as secrets and are not written into privacy-safe diagnostics. New installations default to TLS certificate verification; disabling verification remains available for self-signed local controllers and produces an explicit runtime warning.

## Data model

Normalized aggregate data is written to:

```text
/share/switch_vision/unifi/devices.json
```

The current UniFi API provides reliable link, negotiated speed, connector, PoE and system/uplink information. Per-port traffic is not fabricated when the Integration API does not expose it.

## Activity LEDs

Switch Vision per-port Activity LEDs require per-port RX/TX traffic data. UniFi2MQTT also performs a read-only, non-fatal capability probe against the local classic Network API to determine whether candidate per-port byte counters exist. Probe results are privacy-safe and do not store raw controller payloads.

Where reliable per-port traffic is unavailable, SNMP remains the supported activity source. UniFi-only installations still provide supported port link state, negotiated speed, connector, PoE and system telemetry.

## Validation

Run the offline regression tests with:

```bash
python3 switch-vision-unifi2mqtt/self-test.py
python3 switch-vision-unifi2mqtt/hardening-test.py
python3 switch-vision-unifi2mqtt/mqtt-lifecycle-test.py
python3 switch-vision-unifi2mqtt/classic-port-probe-test.py
python3 switch-vision-unifi2mqtt/multi-controller-test.py
python3 switch-vision-unifi2mqtt/multi-controller-probe-test.py
```

The permanent GitHub Actions validation workflow checks Python and shell syntax, Supervisor MQTT wrapper behaviour, release/configuration consistency, legacy and multi-controller regressions, privacy-safe capability probing, and real amd64/arm64 container builds.

## Related projects

- Switch Vision
- Switch Vision Installer
- Switch Vision SNMP2MQTT

Switch Vision is a community project and is not affiliated with or endorsed by Ubiquiti Inc.
