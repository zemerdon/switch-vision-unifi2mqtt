# Switch Vision UniFi2MQTT v2.0.37

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

The bridge is intentionally separate from SNMP2MQTT. Per-port traffic is only exposed when the API actually supplies it; Switch Vision does not synthesize port traffic from aggregate uplink rates.

Required settings are `controller_url`, `site_id`, `api_key` and MQTT connection details. The API key is sent only in the `X-API-KEY` request header. No write/action endpoints are used.
