# Switch Vision UniFi2MQTT

Optional read-only UniFi Network API bridge for Switch Vision.

## UniFi2MQTT 4.0.1

UniFi2MQTT 4.0.1 keeps the official Network Integration API authoritative for discovery, device identity, port inventory, link state, negotiated speed, connector and PoE data, then adds non-fatal per-port traffic enrichment from the read-only classic Network `stat/device` endpoint.

The enrichment path is supported through both transports:

- **Local** — directly reachable UniFi OS / Network controller using a local Integration API key.
- **Remote** — UniFi Site Manager connector using a Site Manager API key and `/v1/connector/consoles/<host_id>/proxy/network/...`.

No username/password authentication is used.

A normalized hardware MAC must match exactly one classic device row, and official `idx` must match exactly one classic `port_idx`. Missing or ambiguous joins fail closed. Classic telemetry never becomes switch-discovery authority: if it is unavailable, official link/speed/PoE telemetry remains active and `per_port_traffic` is false.

Activity is derived from cumulative RX/TX byte counters. The first sample establishes a baseline; an RX or TX increase produces activity; unchanged counters are idle; counter decreases establish a new reset/reboot baseline; and DOWN ports never report activity.

The v4 MQTT extension includes retained state for:

```text
port/<n>/traffic_available
port/<n>/activity
port/<n>/activity_at
port/<n>/rx_bytes
port/<n>/tx_bytes
```

Activity is added to Home Assistant MQTT Discovery. RX/TX totals remain available as retained MQTT state without creating extra HA entities for every counter on every physical port.

The activity path was live-tested on a USW Flex Mini (`USMINI`) through both Local and Remote APIs, including moving per-port counters on a 1 Gbit/s link. The 4.0.1 runtime default is 10-second polling, aligned with Switch Vision Core's 12-second activity hold.

## Single-controller mode

Existing installs continue to use the established local fields. `transport` defaults to `local`:

- `controller_url`
- `api_key`
- `site_id` (`auto` by default)

Remote operation uses a Site Manager API key, resolves a `host_id` through `/v1/hosts`, and then resolves the actual Network Integration site through `/v1/connector/consoles/<host_id>/proxy/network/integration/v1/sites`. It never substitutes the separate Site Manager `/v1/sites` identifier for the Network site UUID.

Local Integration API and Remote Site Manager profiles can be independently configured. Either profile may be used alone, or both may coexist for priority/fallback operation. `priority_transport` is tried first on every poll, `fallback_transport` can select the other profile or `none`, and the priority path is retried automatically after failover. Legacy single-transport fields remain valid migration inputs.

## Multi-controller / multi-site mode

Set `controllers` to a non-empty list to poll several reachable UniFi controllers, gateways or controller/site combinations from one app instance.

Each entry has:

- `id` — stable operator label used to derive an opaque internal namespace; the label itself stays in app configuration/private state;
- optional `transport` — `local` by default or `remote` for Site Manager connector access;
- `controller_url` — required for local transport and ignored for remote transport;
- optional `host_id` — Site Manager host selector for remote transport, default `auto`;
- `api_key` — local Integration API key or Site Manager API key according to transport;
- optional `site_id` — defaults to `auto` and can also be a Network site UUID, exact name or internal reference;
- optional TLS controls for local transport.

Example:

```yaml
controllers:
  - id: home
    transport: local
    controller_url: https://10.0.0.1
    api_key: YOUR_HOME_API_KEY
    site_id: auto
  - id: branch
    transport: remote
    host_id: auto
    api_key: YOUR_SITE_MANAGER_API_KEY
    site_id: Branch Office
```

The same local controller can appear more than once with different site selections. Local-mode controllers must be reachable from Home Assistant. Remote transport uses the Site Manager connector over verified HTTPS.

Multi-controller mode isolates each controller's previous snapshot, empty-set confirmation and retirement state under the app-private persistent `/data/multi_controller_state/` area, then writes a collision-safe aggregate to `/share/switch_vision/unifi/devices.json` for Switch Vision Discovery. A failed controller preserves its previous private snapshot and is marked unavailable without retiring devices from healthy controllers.

Removing a configured controller retires only that controller's retained MQTT and Home Assistant Discovery topics.

## MQTT

By default the app resolves Home Assistant's Supervisor MQTT service automatically. Custom MQTT broker overrides remain supported.

Multi-controller mode uses one MQTT connection and opaque controller-scoped device identities so duplicate raw UniFi device IDs cannot collide. The v4 activity extension reuses the exact same scoped device identity as the base switch topics.

## Privacy and diagnostics

Credentials and operator controller labels are never copied into privacy-safe diagnostics. Aggregate diagnostics report controller counts/status only, without controller IDs, URLs or API keys.

Private per-controller snapshots remain outside `/share/switch_vision`, so Support My Switch only sees the established aggregate snapshot and privacy-safe diagnostics. Raw classic `stat/device` payloads are never persisted; sensitive controller fields from that response are not admitted to the normalized traffic contract.

The standalone classic capability probe remains available as a privacy-safe diagnostic/regression utility, but 4.0 runtime traffic enrichment no longer depends on a startup evidence file.

## Data

Primary aggregate snapshot:

```text
/share/switch_vision/unifi/devices.json
```

Primary privacy-safe diagnostics:

```text
/share/switch_vision/unifi/diagnostics.json
```

Private persistent controller state:

```text
/data/multi_controller_state/
```

Switch Vision UniFi2MQTT never calls UniFi write/action endpoints.
