# Switch Vision UniFi2MQTT

Switch Vision UniFi2MQTT is the optional UniFi Network API bridge used by **Switch Vision**.
It reads adopted UniFi switching devices through the official read-only UniFi Network Integration API, normalizes switch and port state, publishes Home Assistant MQTT Discovery data, and writes a normalized snapshot for Switch Vision Discovery.

> **Status:** Experimental hardware-support component. The authoritative release version is defined by `VERSION` and `switch-vision-unifi2mqtt/config.yaml`.

## What it does

- Uses API-key authentication only; no UniFi username/password session is required.
- Keeps the official UniFi Network Integration API authoritative for device discovery, identity, port inventory, link state, negotiated speed, connector and PoE data.
- Uses the read-only classic Network `stat/device` endpoint as non-fatal per-port traffic enrichment in UniFi2MQTT 4.0.0.
- Defaults polling to 10 seconds across both the Home Assistant app schema and runtime fallback paths, matching Switch Vision Core's validated 12-second activity hold.
- Supports the same traffic enrichment directly against a Local controller and through the Remote UniFi Site Manager connector.
- Publishes retained MQTT state and Home Assistant MQTT Discovery entities.
- Writes `/share/switch_vision/unifi/devices.json` for Switch Vision Discovery.
- Writes privacy-safe `/share/switch_vision/unifi/diagnostics.json` on successful and failed polling so Support My Switch can diagnose controller/classification problems without exposing credentials or private device identifiers.
- Keeps UniFi API collection separate from SNMP2MQTT.
- Preserves the previous device snapshot if a transient per-device API refresh fails.
- Requires three consecutive successful no-switch polls before retiring every previously known switch.
- Validates MQTT topic prefixes and controller URL structure before connecting.
- Stores normalized state with owner-only permissions and serialized writers.
- Never calls UniFi write/action endpoints.

## Supported live-tested models

Current Switch Vision evidence includes:

- USW Flex Mini (`USMINI`) — local and remote per-port activity validation
- USW Lite 16 PoE
- USW Pro 24 PoE
- USW Enterprise 8 PoE
- USW Pro XG 8 PoE
- US 48 PoE 500W
- UDM Pro gateway/switch hybrid

Support remains contribution-driven and model validation status is maintained by the main Switch Vision device registry.

## UniFi2MQTT 4.0 per-port activity

UniFi2MQTT 4.0.0 adds native per-port traffic/activity telemetry without making the classic API responsible for switch discovery.

The collection contract is deliberately split:

```text
Official Integration API
  -> device discovery / identity
  -> port inventory
  -> link state
  -> negotiated speed
  -> connector / PoE

Classic Network stat/device
  -> cumulative per-port RX/TX bytes
  -> packets / errors / drops when present
  -> uplink marker
  -> activity enrichment
```

A device is enriched only when its normalized hardware MAC matches exactly one classic device row. Classic `external_id` is advisory only because its namespace is not assumed to match the Integration API device UUID. Physical ports are then joined by official `idx` to classic `port_idx`. If a device or port join is missing or ambiguous, traffic support fails closed rather than attaching counters to the wrong switch.

Activity is derived from cumulative byte counters:

```text
first sample           -> baseline, no activity
RX or TX increases     -> activity
counters unchanged     -> idle
counter decreases      -> reset/reboot baseline, no activity
port DOWN              -> no activity
```

The controller refreshes classic traffic counters at a coarser cadence than HTTP requests, so cumulative counter deltas are authoritative. Rate-like `*-r` fields are not used to decide activity.

The v4 MQTT extension includes retained state under each physical port such as:

```text
port/<n>/traffic_available
port/<n>/activity
port/<n>/activity_at
port/<n>/rx_bytes
port/<n>/tx_bytes
```

Activity is exposed through Home Assistant MQTT Discovery. RX/TX cumulative counters remain available as MQTT state without automatically creating two additional Home Assistant entities for every port.

If classic enrichment is unavailable, the switch remains online through the official Integration API and continues to provide supported link/speed/PoE/system telemetry. `per_port_traffic` simply remains false until a deterministic enrichment join succeeds again.

### Live validation

The 4.0.0 activity path was live-tested on a USW Flex Mini over both transports:

- **Local:** direct controller `/proxy/network/api/s/<site>/stat/device`
- **Remote:** `https://api.ui.com/v1/connector/consoles/<host_id>/proxy/network/api/s/<site>/stat/device`

Both paths returned the same per-port cumulative counters. Remote activity was also validated while moving approximately 100 Mbit/s through a port negotiated at 1 Gbit/s.

## Home Assistant App repository

This repository is laid out as a Home Assistant App repository. The app lives in:

```text
switch-vision-unifi2mqtt/
```

Switch Vision Installer can manage this component as an **optional** UniFi support dependency.

## Configuration

### Existing single-controller mode

The existing local configuration remains supported. `transport` defaults to `local`, so installs that do not set it continue to use the directly reachable UniFi Network API with:

- `controller_url`
- `api_key`
- `site_id` (defaults to `auto`)

The app configuration includes an optional `api_key` field so Home Assistant can expose it in the app configuration UI. Existing saved keys are preserved.

Fresh Local profiles default `verify_ssl` to `false` because self-hosted/local UniFi controllers commonly use a local or self-signed certificate. Enable certificate verification when the Local controller presents a certificate trusted by the Home Assistant host. Remote Site Manager connections always use verified HTTPS.

Site selection works as follows:

- `site_id` defaults to `auto`.
- UniFi2MQTT queries the Network Integration site list and resolves the actual Network site UUID automatically.
- `default` is accepted as an automatic/default-site selector.
- Multi-site controllers may specify the Network site UUID, exact site name, or internal reference.

### Site Manager connector transport

Set `transport: remote` for consoles that are not directly reachable from Home Assistant. This mode uses a UniFi Site Manager API key with `https://api.ui.com` and the official console connector. It does not use a UniFi username/password session.

Remote mode first resolves a Site Manager `host_id` (or accepts `host_id: auto` when exactly one usable Network host is visible), then sends Network API calls through:

```text
https://api.ui.com/v1/connector/consoles/<host_id>/proxy/network/...
```

The Site Manager site identifier returned by `/v1/sites` is deliberately not substituted for the Network Integration site UUID. UniFi2MQTT resolves the Network site through the connector itself. Remote transport always uses verified HTTPS; local `verify_ssl` / `allow_insecure_http` switches do not weaken the cloud connector.

Example remote configuration:

```yaml
transport: remote
host_id: auto
site_id: auto
api_key: YOUR_SITE_MANAGER_API_KEY
```

### Local / Remote priority and fallback

UniFi2MQTT can keep Local Integration API and Remote Site Manager credentials configured at the same time. `priority_transport` selects the path attempted first on every poll; `fallback_transport` may select the other path or `none`. If the priority path is unavailable, the bridge uses the configured fallback for that poll and retries the priority path on the next poll.

The legacy `transport`, `controller_url`, `host_id`, `site_id`, and `api_key` fields remain accepted as migration inputs. Multi-controller `controllers` entries remain independent of the single-controller priority/fallback plan.

### Multi-controller / multi-site mode

Set `controllers` to a non-empty list to poll more than one reachable UniFi controller or gateway in the same UniFi2MQTT instance. Each entry contains:

- `id` — a stable short operator label used to derive an opaque collision-safe controller namespace; the label itself is not written into the shared Switch Vision data tree;
- `transport` — optional, defaults to `local`; set `remote` to use the Site Manager connector;
- `controller_url` — required for local transport; ignored for remote transport;
- `host_id` — optional Site Manager host selector for remote transport, default `auto`;
- `api_key` — local Integration API key for local transport or Site Manager API key for remote transport;
- optional `site_id` — defaults to `auto` and resolves the Network Integration site in either transport;
- optional `verify_ssl` and `allow_insecure_http` controls, local transport only.

Example:

```yaml
controllers:
  - id: home
    transport: local
    controller_url: https://10.0.0.1
    site_id: auto
    api_key: YOUR_HOME_API_KEY
  - id: branch
    transport: remote
    host_id: auto
    site_id: Branch Office
    api_key: YOUR_SITE_MANAGER_API_KEY
```

The same controller URL may be listed more than once with different `site_id` values when multiple sites need to run concurrently. Local-mode controllers must be reachable by the Home Assistant host. Remote-mode entries use the Site Manager connector; username/password authentication is not used.

Multi-controller mode isolates each controller's retirement/previous-snapshot state inside the Home Assistant app's private persistent `/data/multi_controller_state/` area. That private state is intentionally outside `/share/switch_vision`, so Support My Switch does not admit raw per-controller snapshots or operator controller labels. A failed controller therefore cannot trigger retirement of healthy devices from another controller.

The Discovery-facing aggregate remains `/share/switch_vision/unifi/devices.json`. It uses opaque controller-scoped composite device IDs to prevent duplicate raw UniFi device IDs from colliding. The same bounded composite identity is used for Home Assistant MQTT device identity.

Removing a controller from configuration retires only that controller's retained MQTT/Home Assistant Discovery topics.

By default, the app resolves Home Assistant's Supervisor MQTT service automatically. `mqtt_host`, MQTT credentials and other MQTT fields remain available as optional overrides for custom brokers.

The API key and MQTT password are treated as secrets and are not written into privacy-safe diagnostics. New installations default to TLS certificate verification; disabling verification remains available for self-signed local controllers and produces an explicit runtime warning.

## Data model

Normalized aggregate data is written to:

```text
/share/switch_vision/unifi/devices.json
```

The normalized per-port object can include official link/speed/PoE fields plus a `traffic` block, `activity`, and `activity_at` when deterministic classic enrichment succeeds. Raw classic controller payloads are never stored in the shared snapshot.

## Validation

Run the offline regression tests with:

```bash
python3 switch-vision-unifi2mqtt/self-test.py
python3 switch-vision-unifi2mqtt/hardening-test.py
python3 switch-vision-unifi2mqtt/mqtt-lifecycle-test.py
python3 switch-vision-unifi2mqtt/activity-test.py
python3 switch-vision-unifi2mqtt/classic-port-probe-test.py
python3 switch-vision-unifi2mqtt/multi-controller-test.py
python3 switch-vision-unifi2mqtt/multi-controller-probe-test.py
python3 switch-vision-unifi2mqtt/optional-profile-test.py
```

The permanent GitHub Actions validation workflow checks Python and shell syntax, Supervisor MQTT wrapper behaviour, release/configuration consistency, Local/Remote activity regressions, legacy and multi-controller regressions, privacy-safe capability probing, and real amd64/arm64 container builds.

## Related projects

- Switch Vision
- Switch Vision Installer
- Switch Vision SNMP2MQTT

Switch Vision is a community project and is not affiliated with or endorsed by Ubiquiti Inc.
