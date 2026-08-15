# Changelog

## v2.0.43

- Adds privacy-safe persistent UniFi polling diagnostics at `/share/switch_vision/unifi/diagnostics.json`.
- Records poll status/stage, adopted-device count, accepted/rejected switch counts, safe hardware model names, safe feature names, and classification reasons without storing device IDs, names, MAC addresses, IP addresses, serial numbers, credentials, or API keys.
- Persists diagnostic evidence when configuration, MQTT connection, or UniFi List Adopted Devices polling fails, avoiding contribution bundles that contain only a snapshot lock file.
- Adds tolerant switch-family detection for `USW ...` and `US ...` hardware even when feature metadata is incomplete or misleading.
- Adds explicit gateway/switch-hybrid recognition for UDM Pro, UDM SE, and UDM Pro SE model forms.
- Preserves hard exclusions for UPS, PDU, USP, RPS, and Power Backup managed-power hardware.
- Adds regression coverage for UDM SE, USW 24 Pro, USW 8, USW 24 POE, managed-power rejection, diagnostic privacy, and persistent API-failure diagnostics.
- Preserves the existing three-consecutive-empty-poll retirement protection and normalized MQTT/device snapshot behavior.

## v2.0.42

- Excludes UniFi managed power hardware such as UPS, PDU, USP, RPS and Power Backup devices from the Switch Vision switch inventory even when switching-adjacent capabilities are exposed.
- Treats null or false `features.switching` values as not-a-switch.
- Adds legacy `US ...` switch-name fallback handling when feature data is absent.
- Adds regression coverage from Support My Switch contribution `SV-2026-000002`, confirming `US 48 PoE 500W` remains a switch while `UPS 2U` is rejected.
- Adds `US 48 PoE 500W` to the live-tested UniFi evidence list.
- Documents that SNMP is required for per-port Activity LED animation when UniFi does not expose per-port RX/TX traffic.

## v2.0.41

- Prevents a single successful empty/no-switch UniFi poll from destructively retiring every known switch; three consecutive empty switching-device polls are now required before whole-set retirement.
- Preserves previous snapshot devices and marks them MQTT-offline during empty-set confirmation polls.
- Validates controller URLs, site identifiers, MQTT ports/poll intervals, and MQTT topic/discovery prefixes before connecting.
- Defaults new installations to TLS certificate verification and logs an explicit warning when verification is disabled or plaintext HTTP is used.
- Stops including UniFi HTTP response bodies in runtime errors/logs and bounds API response size.
- Serializes snapshot access with an owner-only lock file, rejects symlink snapshot targets, and writes snapshots atomically with verified owner-only permissions.
- Runs with a restrictive umask and secures the UniFi shared-state directory to owner-only access.
- Restricts GitHub Actions to read-only repository permissions and cancels superseded validation runs for the same ref.
- Adds offline v2.0.41 hardening regressions for destructive-empty protection, validation, redaction, snapshot permissions, and source guards.

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
