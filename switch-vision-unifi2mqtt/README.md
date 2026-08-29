# Switch Vision UniFi2MQTT

Optional read-only UniFi Network API bridge for Switch Vision.

## Single-controller mode

Existing installs continue to use the established fields:

- `controller_url`
- `api_key`
- `site_id` (`auto` by default)

When `controllers` is empty, the launcher transfers directly to the existing single-controller runtime so current MQTT topics, Home Assistant unique IDs, snapshots and retirement behaviour remain unchanged.

## Multi-controller / multi-site mode

Set `controllers` to a non-empty list to poll several reachable UniFi controllers, gateways or controller/site combinations from one app instance.

Each entry has:

- `id` — stable operator label used to derive an opaque internal namespace; the label itself stays in app configuration/private state;
- `controller_url` — local/reachable UniFi Network origin;
- `api_key` — local Integration API key for that controller;
- optional `site_id` — defaults to `auto` and can also be a site UUID, exact name or internal reference;
- optional TLS controls.

Example:

```yaml
controllers:
  - id: home
    controller_url: https://10.0.0.1
    api_key: YOUR_HOME_API_KEY
    site_id: auto
  - id: branch
    controller_url: https://10.20.0.1
    api_key: YOUR_BRANCH_API_KEY
    site_id: Branch Office
```

The same controller can appear more than once with different site selections. Remote controllers must already be reachable from Home Assistant, for example over a site-to-site VPN. Cloud API access is not implemented by this feature.

Multi-controller mode isolates each controller's previous snapshot, empty-set confirmation and retirement state under the app-private persistent `/data/multi_controller_state/` area, then writes a collision-safe aggregate to `/share/switch_vision/unifi/devices.json` for Switch Vision Discovery. A failed controller preserves its previous private snapshot and is marked unavailable without retiring devices from healthy controllers.

Removing a configured controller retires only that controller's retained MQTT and Home Assistant Discovery topics.

## MQTT

By default the app resolves Home Assistant's Supervisor MQTT service automatically. Custom MQTT broker overrides remain supported.

Multi-controller mode uses one MQTT connection and opaque controller-scoped device identities so duplicate raw UniFi device IDs cannot collide. Composite IDs are bounded to the Core websocket device-ID contract.

## Privacy and diagnostics

Credentials and operator controller labels are never copied into privacy-safe diagnostics. Aggregate diagnostics report controller counts/status only, without controller IDs, URLs or API keys.

Private per-controller snapshots remain outside `/share/switch_vision`, so Support My Switch only sees the established aggregate snapshot and privacy-safe diagnostics. Its existing UniFi snapshot sanitizer masks aggregate device IDs before a contribution package is built.

The startup classic Network API probe remains read-only and non-fatal. In multi-controller mode it probes each configured controller/site independently and aggregates only privacy-safe counter-presence results.

## Data

Primary aggregate snapshot:

```text
/share/switch_vision/unifi/devices.json
```

Primary privacy-safe diagnostics:

```text
/share/switch_vision/unifi/diagnostics.json
/share/switch_vision/unifi/classic_port_traffic_probe.json
```

Private persistent controller state:

```text
/data/multi_controller_state/
```

Switch Vision UniFi2MQTT never calls UniFi write/action endpoints.
