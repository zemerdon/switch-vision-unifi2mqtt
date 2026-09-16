#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import unifi2mqtt as core

VERSION = "4.0.2"

# Snapshot/MQTT contract for the cumulative counters proven on real UniFi
# switching hardware. Rate fields from the classic endpoint remain deliberately
# non-authoritative because the controller refresh cadence is coarser than the
# HTTP polling cadence.
COUNTER_FIELDS = (
    "rx_bytes",
    "tx_bytes",
    "rx_packets",
    "tx_packets",
    "rx_errors",
    "tx_errors",
    "rx_dropped",
    "tx_dropped",
)

_BASE_RETAINED_TOPICS = core.retained_topics_for_device


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def normalize_mac(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[^0-9a-f]", "", raw)
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-f]{12}", compact):
        return ""
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def parse_classic_devices(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    else:
        raise RuntimeError("Unexpected classic Network device response")
    return [row for row in rows if isinstance(row, dict)]


def fetch_classic_devices(api: core.UniFiClient) -> list[dict[str, Any]]:
    """Fetch one classic stat/device payload for the already-selected site.

    UniFiClient._get is transport-aware, so this exact path works both directly
    against a local UniFi OS controller and through the Site Manager connector.
    """
    site = api.resolve_site()
    site_ref = str(site.get("internalReference") or "").strip()
    if not site_ref or len(site_ref) > 256 or core._has_control_chars(site_ref):
        raise RuntimeError("Resolved UniFi site has no usable internal reference")
    payload = api._get(
        "/proxy/network/api/s/"
        f"{quote(site_ref, safe='')}/stat/device"
    )
    return parse_classic_devices(payload)


def _classic_rows_by_mac(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        mac = normalize_mac(row.get("mac"))
        if mac:
            result.setdefault(mac, []).append(row)
    return result


def _classic_ports(row: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    raw_ports = row.get("port_table")
    if not isinstance(raw_ports, list):
        return result
    for raw in raw_ports:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("port_idx"))
        except (TypeError, ValueError):
            continue
        if idx <= 0:
            continue
        result.setdefault(idx, []).append(raw)
    return result


def _previous_ports(device: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(device, dict):
        return {}
    ports = device.get("ports")
    if not isinstance(ports, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for port in ports:
        if not isinstance(port, dict):
            continue
        try:
            idx = int(port.get("idx"))
        except (TypeError, ValueError):
            continue
        result[idx] = port
    return result


def _unavailable_port(
    port: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    clone = json.loads(json.dumps(port))
    previous_traffic = (
        previous.get("traffic")
        if isinstance(previous, dict) and isinstance(previous.get("traffic"), dict)
        else {}
    )
    traffic = dict(previous_traffic)
    traffic["available"] = False
    clone["traffic"] = traffic
    clone["activity"] = False
    clone["activity_at"] = 0
    clone.pop("counter_reset", None)
    return clone


def mark_traffic_unavailable(
    device: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clone = json.loads(json.dumps(device))
    capabilities = (
        dict(clone.get("api_capabilities"))
        if isinstance(clone.get("api_capabilities"), dict)
        else {}
    )
    capabilities["per_port_traffic"] = False
    clone["api_capabilities"] = capabilities

    previous_by_idx = _previous_ports(previous)
    ports = clone.get("ports") if isinstance(clone.get("ports"), list) else []
    unavailable: list[Any] = []
    for port in ports:
        if not isinstance(port, dict):
            unavailable.append(port)
            continue
        try:
            idx = int(port.get("idx"))
        except (TypeError, ValueError):
            idx = -1
        unavailable.append(_unavailable_port(port, previous_by_idx.get(idx)))
    clone["ports"] = unavailable
    return clone


def enrich_device(
    device: dict[str, Any],
    classic: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    sampled_at: int | None = None,
) -> dict[str, Any]:
    """Join one official Integration device to one classic device row.

    The join is intentionally strict: normalized hardware MAC is the sole
    device join key. Classic external_id is not assumed to share the official
    Integration API ID namespace and is therefore advisory only. Every official
    port needs exactly one classic port_idx with valid cumulative RX/TX byte
    counters before the device-level per_port_traffic capability becomes true.
    """
    clone = json.loads(json.dumps(device))
    now = int(sampled_at if sampled_at is not None else time.time())
    device_mac = normalize_mac(clone.get("mac_address"))
    classic_mac = normalize_mac(classic.get("mac"))
    if not device_mac or device_mac != classic_mac:
        return mark_traffic_unavailable(clone, previous)

    classic_ports = _classic_ports(classic)
    previous_by_idx = _previous_ports(previous)
    ports = clone.get("ports") if isinstance(clone.get("ports"), list) else []
    enriched_ports: list[dict[str, Any]] = []
    all_ports_joined = bool(ports)

    for port in ports:
        if not isinstance(port, dict):
            all_ports_joined = False
            continue
        try:
            idx = int(port.get("idx"))
        except (TypeError, ValueError):
            all_ports_joined = False
            enriched_ports.append(port)
            continue

        candidates = classic_ports.get(idx, [])
        previous_port = previous_by_idx.get(idx)
        if len(candidates) != 1:
            all_ports_joined = False
            enriched_ports.append(_unavailable_port(port, previous_port))
            continue

        raw = candidates[0]
        counters = {field: _non_negative_int(raw.get(field)) for field in COUNTER_FIELDS}
        if counters["rx_bytes"] is None or counters["tx_bytes"] is None:
            all_ports_joined = False
            enriched_ports.append(_unavailable_port(port, previous_port))
            continue

        traffic: dict[str, Any] = {
            "available": True,
            "sampled_at": now,
            "is_uplink": bool(raw.get("is_uplink")),
        }
        for field, value in counters.items():
            if value is not None:
                traffic[field] = value

        activity = False
        activity_at = 0
        counter_reset = False
        previous_traffic = (
            previous_port.get("traffic")
            if isinstance(previous_port, dict)
            and isinstance(previous_port.get("traffic"), dict)
            else {}
        )
        previous_rx = _non_negative_int(previous_traffic.get("rx_bytes"))
        previous_tx = _non_negative_int(previous_traffic.get("tx_bytes"))
        current_rx = int(traffic["rx_bytes"])
        current_tx = int(traffic["tx_bytes"])
        link_up = str(port.get("state") or "").upper() == "UP"

        if link_up and previous_rx is not None and previous_tx is not None:
            if current_rx < previous_rx or current_tx < previous_tx:
                counter_reset = True
            elif current_rx > previous_rx or current_tx > previous_tx:
                activity = True
                activity_at = now
            else:
                try:
                    activity_at = max(0, int(previous_port.get("activity_at") or 0))
                except (TypeError, ValueError, AttributeError):
                    activity_at = 0

        if not link_up or counter_reset:
            activity = False
            activity_at = 0

        enriched = json.loads(json.dumps(port))
        enriched["traffic"] = traffic
        enriched["activity"] = activity
        enriched["activity_at"] = activity_at
        if counter_reset:
            enriched["counter_reset"] = True
        else:
            enriched.pop("counter_reset", None)
        enriched_ports.append(enriched)

    clone["ports"] = enriched_ports
    capabilities = (
        dict(clone.get("api_capabilities"))
        if isinstance(clone.get("api_capabilities"), dict)
        else {}
    )
    capabilities["per_port_traffic"] = all_ports_joined
    clone["api_capabilities"] = capabilities
    return clone


def enrich_devices(
    devices: list[dict[str, Any]],
    classic_rows: list[dict[str, Any]],
    previous_by_id: dict[str, dict[str, Any]],
    *,
    sampled_at: int | None = None,
) -> list[dict[str, Any]]:
    by_mac = _classic_rows_by_mac(classic_rows)
    output: list[dict[str, Any]] = []
    for device in devices:
        did = str(device.get("id") or "").strip()
        previous = previous_by_id.get(did)
        freshness = device.get("freshness") if isinstance(device.get("freshness"), dict) else {}
        if bool(freshness.get("stale")):
            output.append(device)
            continue
        mac = normalize_mac(device.get("mac_address"))
        matches = by_mac.get(mac, []) if mac else []
        if len(matches) != 1:
            output.append(mark_traffic_unavailable(device, previous))
            continue
        output.append(
            enrich_device(
                device,
                matches[0],
                previous,
                sampled_at=sampled_at,
            )
        )
    return output


def retained_topics_for_device_v4(
    device: dict[str, Any],
    topic_prefix: str,
    discovery_prefix: str,
) -> set[str]:
    topics = set(_BASE_RETAINED_TOPICS(device, topic_prefix, discovery_prefix))
    did = core.slug(device.get("id") or device.get("name"))
    base = f"{topic_prefix.strip('/')}/{did}"
    discovery_base = discovery_prefix.strip("/")
    ports = device.get("ports") if isinstance(device.get("ports"), list) else []
    for port in ports:
        if not isinstance(port, dict) or port.get("idx") is None:
            continue
        try:
            idx = int(port["idx"])
        except (TypeError, ValueError):
            continue
        prefix = f"port/{idx}"
        for key in (
            "traffic_available",
            "activity",
            "activity_at",
            "rx_bytes",
            "tx_bytes",
        ):
            topics.add(f"{base}/{prefix}/{key}")
        uid = f"switch_vision_unifi_{did}_{core.slug(prefix + '/activity')}"
        topics.add(f"{discovery_base}/binary_sensor/{uid}/config")
    return topics


def _publisher_device(pub: core.Publisher, device: dict[str, Any]) -> dict[str, Any]:
    """Return the exact MQTT identity used by the active publisher.

    Multi-controller mode scopes raw UniFi device IDs before publishing. Reusing
    that mapping here keeps the v4 traffic topics collision-safe and aligned
    with the already-published core device topics.
    """
    mapper = getattr(pub, "_namespaced_device", None)
    if callable(mapper):
        mapped = mapper(device)
        if isinstance(mapped, dict):
            return mapped
    return dict(device)


def publish_enriched_device(pub: core.Publisher, device: dict[str, Any]) -> None:
    """Publish only the v4 traffic extension after the core device publish.

    RX/TX counters stay available as retained MQTT state without creating two
    additional Home Assistant entities for every physical port. Activity is the
    one new HA binary sensor because Switch Vision consumes it visually.
    """
    effective = _publisher_device(pub, device)
    did = core.slug(effective.get("id") or effective.get("name"))
    base = f"{pub.topic_prefix}/{did}"
    ha_device = {
        "identifiers": [f"switch_vision_unifi_{did}"],
        "name": effective.get("name") or "UniFi Switch",
        "manufacturer": "Ubiquiti",
        "model": effective.get("model") or "Unknown",
        "sw_version": effective.get("firmware") or None,
    }
    ports = effective.get("ports") if isinstance(effective.get("ports"), list) else []
    for port in ports:
        if not isinstance(port, dict) or port.get("idx") is None:
            continue
        try:
            idx = int(port["idx"])
        except (TypeError, ValueError):
            continue
        prefix = f"port/{idx}"
        traffic = port.get("traffic") if isinstance(port.get("traffic"), dict) else {}
        available = bool(traffic.get("available"))
        pub.publish(f"{base}/{prefix}/traffic_available", "ON" if available else "OFF")
        pub.publish(f"{base}/{prefix}/activity", "ON" if bool(port.get("activity")) else "OFF")
        pub.publish(f"{base}/{prefix}/activity_at", int(port.get("activity_at") or 0))
        if available:
            pub.publish(f"{base}/{prefix}/rx_bytes", traffic.get("rx_bytes"))
            pub.publish(f"{base}/{prefix}/tx_bytes", traffic.get("tx_bytes"))

        uid = f"switch_vision_unifi_{did}_{core.slug(prefix + '/activity')}"
        pub.discovery(
            "binary_sensor",
            uid,
            {
                "name": f"Port {idx} Activity",
                "unique_id": uid,
                "state_topic": f"{base}/{prefix}/activity",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": ha_device,
                "availability_topic": f"{base}/available",
                "payload_available": "online",
                "payload_not_available": "offline",
            },
        )


def _snapshot_payload(snapshot: Path) -> dict[str, Any]:
    try:
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_stale(device: dict[str, Any]) -> bool:
    freshness = device.get("freshness") if isinstance(device.get("freshness"), dict) else {}
    return bool(freshness.get("stale"))


def poll_once_v4(
    cfg: dict[str, Any],
    snapshot: Path,
    pub: core.Publisher | None = None,
) -> None:
    """Run one official poll, then non-fatally enrich it with classic counters."""
    owns_publisher = pub is None
    current_pub = pub if pub is not None else core.Publisher(cfg)
    try:
        with core.snapshot_operation_lock(snapshot):
            before = _snapshot_payload(snapshot)
            previous_rows = before.get("devices") if isinstance(before.get("devices"), list) else []
            previous_by_id = {
                str(row.get("id")): row
                for row in previous_rows
                if isinstance(row, dict) and str(row.get("id") or "").strip()
            }

            # Keep the official Integration API as the source of truth for
            # discovery/identity/link/speed/PoE and all retirement semantics.
            core._poll_once_unlocked(cfg, snapshot, current_pub)

            current = _snapshot_payload(snapshot)
            rows = current.get("devices") if isinstance(current.get("devices"), list) else []
            devices = [row for row in rows if isinstance(row, dict)]
            live = [row for row in devices if not _is_stale(row)]
            if not live:
                return

            try:
                api = core.client_from_config(cfg)
                classic_rows = fetch_classic_devices(api)
                enriched = enrich_devices(
                    devices,
                    classic_rows,
                    previous_by_id,
                    sampled_at=int(time.time()),
                )
                joined = sum(
                    1
                    for row in enriched
                    if not _is_stale(row)
                    and isinstance(row.get("api_capabilities"), dict)
                    and row["api_capabilities"].get("per_port_traffic") is True
                )
                logging.info(
                    "UniFi per-port traffic enrichment: %d/%d live switch(es) joined.",
                    joined,
                    len(live),
                )
            except Exception as exc:
                logging.warning(
                    "UniFi per-port traffic enrichment unavailable; official telemetry remains active: %s",
                    core.privacy_safe_error_type(exc, "network_api_unavailable"),
                )
                enriched = []
                for row in devices:
                    if _is_stale(row):
                        enriched.append(row)
                        continue
                    enriched.append(
                        mark_traffic_unavailable(
                            row,
                            previous_by_id.get(str(row.get("id") or "")),
                        )
                    )

            # Only live official devices are republished. Stale devices remain
            # offline exactly as the core poll marked them.
            for row in enriched:
                if _is_stale(row):
                    continue
                publish_enriched_device(current_pub, row)

            core.write_snapshot(
                snapshot,
                enriched,
                int(current.get("empty_switch_polls") or 0),
                int(current.get("stale_after_seconds") or core.snapshot_stale_after_seconds(cfg)),
            )
            current_pub.flush()
    finally:
        if owns_publisher:
            current_pub.close()


def install_runtime_hooks() -> None:
    """Install the v4 retained-topic contract without altering core semantics."""
    core.VERSION = VERSION
    core.retained_topics_for_device = retained_topics_for_device_v4
