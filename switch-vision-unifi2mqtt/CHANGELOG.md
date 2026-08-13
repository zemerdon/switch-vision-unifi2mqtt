# Changelog

## v2.0.40

- Adds the Home Assistant app-local CHANGELOG.md required by Supervisor.
- Adds validation so the repository and app-local changelogs remain synchronized.

## v2.0.39

- Resolves the Home Assistant Supervisor MQTT service automatically for fresh/default installations.
- Preserves explicitly configured custom MQTT brokers and migrates legacy local/default broker hosts at runtime.
- Declares the MQTT service dependency and keeps credentials out of logs.
- Marks previously known devices unavailable when a transient per-device refresh fails.
- Deletes stale retained MQTT state and Home Assistant Discovery topics when ports/entities disappear.
- Deletes retained MQTT/Discovery records for switches that are no longer returned by the UniFi API.
- Expands offline regression coverage for MQTT service overrides and retained-entity lifecycle cleanup.

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
