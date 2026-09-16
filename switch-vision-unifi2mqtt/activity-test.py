#!/usr/bin/env python3
from __future__ import annotations

import copy

import port_activity as activity
import unifi2mqtt as core


def official_device(state: str = "UP") -> dict:
    return {
        "id": "device-uuid",
        "name": "Flex Mini",
        "model": "USW Flex Mini",
        "firmware": "2.1.6",
        "mac_address": "58:d6:1f:14:6e:40",
        "state": "ONLINE",
        "ports": [
            {
                "idx": 1,
                "state": "UP",
                "connector": "RJ45",
                "max_speed_mbps": 1000,
                "speed_mbps": 1000,
                "poe": {"available": False},
            },
            {
                "idx": 4,
                "state": state,
                "connector": "RJ45",
                "max_speed_mbps": 1000,
                "speed_mbps": 1000 if state == "UP" else None,
                "poe": {"available": False},
            },
        ],
        "system": {},
        "api_capabilities": {"port_detail": True, "per_port_traffic": False},
        "freshness": {"last_success_at": 1, "stale": False, "reason": ""},
    }


def classic_device(rx4: int = 100, tx4: int = 200) -> dict:
    return {
        "external_id": "device-uuid",
        "model": "USMINI",
        "mac": "58:d6:1f:14:6e:40",
        "port_table": [
            {
                "port_idx": 1,
                "up": True,
                "speed": 1000,
                "is_uplink": True,
                "rx_bytes": 1000,
                "tx_bytes": 2000,
                "rx_packets": 10,
                "tx_packets": 20,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
            },
            {
                "port_idx": 4,
                "up": True,
                "speed": 1000,
                "is_uplink": False,
                "rx_bytes": rx4,
                "tx_bytes": tx4,
                "rx_packets": 30,
                "tx_packets": 40,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
            },
        ],
    }


def port(device: dict, idx: int) -> dict:
    return next(item for item in device["ports"] if item["idx"] == idx)


def main() -> int:
    assert activity.VERSION == "4.0.2"

    # First observation establishes the cumulative-counter baseline and must not
    # create a false activity pulse.
    first = activity.enrich_device(
        official_device(), classic_device(), None, sampled_at=1000
    )
    assert first["api_capabilities"]["per_port_traffic"] is True
    assert port(first, 1)["traffic"]["is_uplink"] is True
    assert port(first, 4)["traffic"]["rx_bytes"] == 100
    assert port(first, 4)["traffic"]["tx_bytes"] == 200
    assert port(first, 4)["activity"] is False
    assert port(first, 4)["activity_at"] == 0

    # TX-only change is activity; this is important because the live remote test
    # observed valid TX-only activity samples before the large traffic burst.
    changed = activity.enrich_device(
        official_device(), classic_device(100, 250), first, sampled_at=1010
    )
    assert port(changed, 4)["activity"] is True
    assert port(changed, 4)["activity_at"] == 1010

    # An unchanged controller snapshot returns idle but preserves the last
    # activity timestamp so consumers can implement a visual hold interval.
    idle = activity.enrich_device(
        official_device(), classic_device(100, 250), changed, sampled_at=1020
    )
    assert port(idle, 4)["activity"] is False
    assert port(idle, 4)["activity_at"] == 1010

    # Counter decreases are treated as reset/reboot baselines, never activity.
    reset = activity.enrich_device(
        official_device(), classic_device(5, 7), idle, sampled_at=1030
    )
    assert port(reset, 4)["activity"] is False
    assert port(reset, 4)["activity_at"] == 0
    assert port(reset, 4)["counter_reset"] is True

    # Link-down always suppresses activity regardless of counter values.
    down = activity.enrich_device(
        official_device("DOWN"), classic_device(500, 600), changed, sampled_at=1040
    )
    assert port(down, 4)["activity"] is False
    assert port(down, 4)["activity_at"] == 0

    # Device identity is MAC-authoritative. Classic external_id is advisory
    # because its namespace is not proven to match the Integration API UUID.
    advisory_id = classic_device()
    advisory_id["external_id"] = "another-device"
    matched = activity.enrich_device(official_device(), advisory_id, first)
    assert matched["api_capabilities"]["per_port_traffic"] is True
    assert port(matched, 4)["traffic"]["available"] is True

    wrong_mac = classic_device()
    wrong_mac["mac"] = "58:d6:1f:14:6e:41"
    failed = activity.enrich_device(official_device(), wrong_mac, first)
    assert failed["api_capabilities"]["per_port_traffic"] is False
    assert port(failed, 4)["traffic"]["available"] is False

    duplicate_port = classic_device()
    duplicate_port["port_table"].append(copy.deepcopy(duplicate_port["port_table"][1]))
    failed = activity.enrich_device(official_device(), duplicate_port, first)
    assert failed["api_capabilities"]["per_port_traffic"] is False
    assert port(failed, 4)["traffic"]["available"] is False

    duplicate_device_rows = [classic_device(), copy.deepcopy(classic_device())]
    failed_list = activity.enrich_devices(
        [official_device()], duplicate_device_rows, {"device-uuid": first}, sampled_at=1050
    )
    assert failed_list[0]["api_capabilities"]["per_port_traffic"] is False

    malformed = official_device()
    malformed["ports"].append({"idx": None, "state": "DOWN"})
    unavailable = activity.mark_traffic_unavailable(malformed, first)
    assert unavailable["api_capabilities"]["per_port_traffic"] is False

    # The classic path is identical for local and remote transports. UniFiClient
    # adds the Site Manager connector prefix when transport=remote.
    remote = core.UniFiClient("", "auto", "key", True, False, "remote", "host-123")
    remote.host_id = "host-123"
    remote_url = remote._request_url("/proxy/network/api/s/default/stat/device")
    assert remote_url == (
        "https://api.ui.com/v1/connector/consoles/host-123"
        "/proxy/network/api/s/default/stat/device"
    )

    class FakeApi:
        def __init__(self):
            self.paths = []
        def resolve_site(self):
            return {"id": "site-uuid", "internalReference": "default", "name": "Default"}
        def _get(self, path):
            self.paths.append(path)
            return {"data": [classic_device()]}

    fake_api = FakeApi()
    rows = activity.fetch_classic_devices(fake_api)
    assert len(rows) == 1
    assert fake_api.paths == ["/proxy/network/api/s/default/stat/device"]

    # v4 owns activity/counter retained-state cleanup while intentionally only
    # exposing Activity as a new Home Assistant entity.
    activity.install_runtime_hooks()
    topics = core.retained_topics_for_device(
        first, "switch_vision/unifi", "homeassistant"
    )
    assert any(topic.endswith("/port/4/activity") for topic in topics)
    assert any(topic.endswith("/port/4/rx_bytes") for topic in topics)
    assert any("binary_sensor" in topic and topic.endswith("/config") for topic in topics)

    # Multi-controller traffic publishing must use exactly the same scoped ID as
    # the base NamespacedPublisher, never the raw device ID.
    class FakeNamespacedPublisher:
        topic_prefix = "switch_vision/unifi"
        discovery_prefix = "homeassistant"
        def __init__(self):
            self.states = []
            self.discovery_rows = []
        def _namespaced_device(self, device):
            clone = dict(device)
            clone["id"] = "c_deadbeef_device_uuid"
            return clone
        def publish(self, topic, value):
            self.states.append((topic, value))
        def discovery(self, component, uid, payload):
            self.discovery_rows.append((component, uid, payload))

    scoped = FakeNamespacedPublisher()
    activity.publish_enriched_device(scoped, changed)
    state_topics = [topic for topic, _value in scoped.states]
    assert state_topics
    assert all("/device_uuid/" not in topic for topic in state_topics)
    assert all("/c_deadbeef_device_uuid/" in topic for topic in state_topics)
    assert scoped.discovery_rows
    assert all(
        row[2]["state_topic"].startswith(
            "switch_vision/unifi/c_deadbeef_device_uuid/"
        )
        for row in scoped.discovery_rows
    )

    print("Switch Vision UniFi2MQTT 4.0 per-port activity regression: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
