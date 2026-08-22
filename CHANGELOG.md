# Changelog

## v2.0.48

- Rejects devices that only advertise the UniFi API `switching` feature without belonging to a recognised switch or gateway/switch-hybrid model family.
- Prevents Ubiquiti AirWire from being misclassified as a Switch Vision switch (SV-2026-000009).
- Explicitly preserves UCG Ultra, UCG Fiber, and UDM Pro Max as gateway/switch hybrids while retaining existing UDM Pro-family handling.
- Adds permanent regressions for AirWire rejection and UCG Ultra/UCG Fiber/UDM Pro Max acceptance.

## v2.0.47

- Pin the UniFi2MQTT Home Assistant `base:3.22` container to its resolved immutable multi-architecture OCI digest.
- Preserve amd64 and arm64 platform selection through the pinned OCI image index.
- Prevent future UniFi2MQTT rebuilds from silently consuming a different `base:3.22` image without an explicit source change.
- Preserve UniFi API authentication and site resolution, MQTT lifecycle and Last Will behavior, TLS/HTTP transport controls, snapshots, diagnostics, Home Assistant discovery, and device classification unchanged.

## v2.0.46

- Add optional TLS for explicitly configured/custom MQTT brokers while preserving the normal Home Assistant Supervisor MQTT path.
- Add optional `/ssl` CA-file support through a read-only Home Assistant SSL mapping.
- Keep MQTT certificate verification enabled by default; disabling verification requires an explicit option and logs a warning.
- Require an explicit `allow_insecure_http: true` opt-in before a plaintext UniFi controller URL is accepted.
- Preserve HTTPS controller defaults, persistent MQTT/LWT behaviour, site resolution, snapshot privacy, device classification, and Discovery contracts unchanged.
- Extend hardening coverage for plaintext-controller opt-in and MQTT TLS configuration.

## v2.0.45

- Keeps one MQTT client/session alive for the daemon lifetime instead of reconnecting for every UniFi poll.
- Adds retained bridge availability at `switch_vision/unifi/status` with a retained MQTT Last Will of `offline`.
- Republishes retained bridge `online` state after initial connection and every automatic broker reconnect.
- Adds bridge-level and per-device Home Assistant availability with `availability_mode: all`, so entities fail closed when either the bridge or device is unavailable.
- Publishes Home Assistant Discovery and availability messages at QoS 1 while keeping normal metric/state traffic at QoS 0.
- Checks MQTT publish return codes and uses bounded completion flushing instead of silently clearing unfinished publishes.
- Uses Paho asynchronous connection/reconnect handling so a temporarily unavailable broker does not require restarting UniFi2MQTT.
- Adds offline regressions for LWT configuration, reconnect availability, dual Home Assistant availability, failed publish return codes, graceful shutdown, and large publish bursts.
- Preserves the v2.0.44 UniFi Network Integration API authentication and automatic site-resolution behavior unchanged.

## v2.0.44

- Adds automatic local UniFi Network site discovery through the official Integration API site-list endpoint.
- Changes `site_id` from a required first-run value to an automatic selector that defaults to `auto`.
- Accepts `auto` or `default` for automatic/default-site resolution.
- Allows multi-site installations to select a site by UUID, exact site name, or internal reference.
- Resolves the real site UUID before adopted-device, detail, and statistics requests.
- Adds clearer HTTP 401/403 guidance for incorrect or unauthorized API keys while continuing to suppress controller response bodies.
- Adds regression coverage for automatic single-site resolution, default-site resolution, named-site resolution, ambiguous multi-site rejection, and authentication-error redaction.
- Preserves v2.0.43 device classification, privacy-safe diagnostics, managed-power exclusions, and three-empty-poll retirement protection.

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
