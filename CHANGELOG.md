# Changelog

## v2.0.38

- Adds a realistic offline UniFi switch fixture for hardware-free regression testing.
- Validates UniFi normalization for link, negotiated speed, PoE, CPU, memory, uptime, and uplink rates.
- Captures and validates retained MQTT state messages without requiring a broker.
- Validates Home Assistant MQTT Discovery topics, unique IDs, availability topics, and device metadata.
- Confirms DOWN ports do not publish stale negotiated speed values.
- Keeps the existing live-controller runtime unchanged.

## v2.0.37

- Initial standalone Switch Vision UniFi2MQTT repository baseline.
- Read-only UniFi Network Integration API support.
- Home Assistant MQTT Discovery and retained normalized state.
- Normalized Switch Vision snapshot output.
- Port link, negotiated speed, connector and PoE handling.
- CPU, memory, uptime and uplink-rate collection.
- Paho MQTT 1.x/2.x compatibility.
- Transient per-device refresh preservation.
- Offline UniFi regression/self-test suite.
