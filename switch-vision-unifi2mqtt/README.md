# Switch Vision UniFi2MQTT v2.0.43

Optional read-only UniFi Network Integration API bridge for Switch Vision.

The bridge uses:

```text
GET /proxy/network/integration/v1/sites/{siteId}/devices
GET /proxy/network/integration/v1/sites/{siteId}/devices/{deviceId}
GET /proxy/network/integration/v1/sites/{siteId}/devices/{deviceId}/statistics/latest
```

It publishes Home Assistant MQTT Discovery plus retained normalized state for model, firmware, online state, port link/speed/connector/PoE, CPU, memory, uptime and aggregate uplink rates.

A normalized snapshot is written to:

```text
/share/switch_vision/unifi/devices.json
```

Privacy-safe polling diagnostics are written to /share/switch_vision/unifi/diagnostics.json. They retain polling status and safe hardware classification evidence without storing API keys, credentials, device IDs, device names, MAC addresses, IP addresses, or serial numbers.

The bridge is intentionally separate from SNMP2MQTT. Per-port traffic is only exposed when the API actually supplies it; Switch Vision does not synthesize port traffic from aggregate uplink rates. **SNMP is required for Switch Vision per-port Activity LED animation when the UniFi API does not expose per-port RX/TX traffic.**

Required UniFi settings are `controller_url`, `site_id` and `api_key`. Home Assistant's Supervisor MQTT service is used automatically unless a custom `mqtt_host` is configured. The API key is sent only in the `X-API-KEY` request header. New installations verify controller TLS certificates by default. A single empty switching-device response no longer retires all devices; whole-set retirement requires three consecutive empty polls. No write/action endpoints are used.
