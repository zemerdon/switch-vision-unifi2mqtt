#!/usr/bin/env python3
"""Offline regression checks for Switch Vision UniFi2MQTT."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path


def load_module():
    try:
        import paho.mqtt.client  # noqa: F401
    except ModuleNotFoundError:
        client_mod = types.ModuleType("paho.mqtt.client")
        class CallbackAPIVersion:
            VERSION1 = object()
        class Client:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
        client_mod.CallbackAPIVersion = CallbackAPIVersion
        client_mod.Client = Client
        paho_mod = types.ModuleType("paho")
        mqtt_mod = types.ModuleType("paho.mqtt")
        paho_mod.mqtt = mqtt_mod
        mqtt_mod.client = client_mod
        sys.modules["paho"] = paho_mod
        sys.modules["paho.mqtt"] = mqtt_mod
        sys.modules["paho.mqtt.client"] = client_mod

    path = Path(__file__).with_name("unifi2mqtt.py")
    spec = importlib.util.spec_from_file_location("switch_vision_unifi2mqtt", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load unifi2mqtt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    m = load_module()

    # Supervisor may serialize mandatory first-time settings as null. The
    # runtime must reject them as missing rather than treating None as text.
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "options.json"
        config_path.write_text(json.dumps({
            "controller_url": "https://192.168.1.1",
            "site_id": None,
            "api_key": None,
            "mqtt_host": "core-mosquitto",
        }), encoding="utf-8")
        try:
            m.load_config(config_path)
        except RuntimeError as exc:
            assert "api_key" in str(exc)
            assert "site_id" not in str(exc)
        else:
            raise AssertionError("null required UniFi configuration was accepted")

        config_path.write_text(json.dumps({
            "controller_url": "https://192.168.1.1",
            "site_id": None,
            "api_key": "key",
            "mqtt_host": "core-mosquitto",
        }), encoding="utf-8")

        cfg = m.load_config(config_path)
        assert cfg["site_id"] == "auto"

    # Supervisor-resolved MQTT environment values override legacy/default
    # broker settings without overwriting the saved Home Assistant options.
    with tempfile.TemporaryDirectory() as td:
        config_path = Path(td) / "options.json"
        config_path.write_text(json.dumps({
            "controller_url": "https://192.168.1.1",
            "site_id": "site",
            "api_key": "key",
            "mqtt_host": "core-mosquitto",
            "mqtt_port": "1883",
            "mqtt_username": "",
            "mqtt_password": "",
        }), encoding="utf-8")
        names = ("SV_MQTT_HOST", "SV_MQTT_PORT", "SV_MQTT_USERNAME", "SV_MQTT_PASSWORD")
        saved = {name: os.environ.get(name) for name in names}
        try:
            os.environ["SV_MQTT_HOST"] = "mqtt-service"
            os.environ["SV_MQTT_PORT"] = "1884"
            os.environ["SV_MQTT_USERNAME"] = "service-user"
            os.environ["SV_MQTT_PASSWORD"] = "service-pass"
            cfg = m.load_config(config_path)
            assert cfg["mqtt_host"] == "mqtt-service"
            assert cfg["mqtt_port"] == "1884"
            assert cfg["mqtt_username"] == "service-user"
            assert cfg["mqtt_password"] == "service-pass"
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    # SV-2026-000009: switching feature alone is not switch identity.
    assert not m.is_switch({"features": ["switching"], "model": "AirWire"})
    assert not m.is_switch({"features": {"switching": {}}, "model": "AirWire"})
    assert not m.is_switch({"features": ["switching"], "model": "anything"})
    assert m.is_switch({"features": [], "model": "USW Pro 24 PoE"})
    assert m.is_switch({"features": [], "model": "UCG Ultra"})
    assert m.is_switch({"features": [], "model": "UCG Fiber"})
    # SV-2026-000011: UDM Pro Max is a legitimate gateway/switch hybrid.
    assert m.is_switch({"features": ["switching"], "model": "UDM Pro Max"})
    assert m.switch_classification({"features": ["switching"], "model": "UDM Pro Max"}) == (
        True, "known_gateway_switch_hybrid"
    )
    assert not m.is_switch({"features": ["accessPoint"], "model": "U6 Pro"})


    # v2.0.44: local Network site selection resolves through the official
    # /integration/v1/sites endpoint before site-specific API requests.
    paths = []

    client = m.UniFiClient(
        "https://192.0.2.1",
        "default",
        "fixture-key",
        False,
    )

    def one_site_get(path):
        paths.append(path)

        if path.endswith("/sites"):
            return {
                "data": [
                    {
                        "id":
                            "11111111-1111-1111-1111-111111111111",
                        "internalReference": "default",
                        "name": "Main",
                    }
                ]
            }

        if path.endswith("/devices"):
            return {"data": []}

        raise AssertionError(path)

    client._get = one_site_get

    assert client.resolve_site_id() == (
        "11111111-1111-1111-1111-111111111111"
    )

    assert client.list_devices() == []

    assert paths == [
        "/proxy/network/integration/v1/sites",
        (
            "/proxy/network/integration/v1/sites/"
            "11111111-1111-1111-1111-111111111111/"
            "devices"
        ),
    ]

    auto_client = m.UniFiClient(
        "https://192.0.2.1",
        "auto",
        "fixture-key",
        False,
    )

    auto_client._get = lambda path: {
        "data": [
            {
                "id":
                    "22222222-2222-2222-2222-222222222222",
                "internalReference": "site-a",
                "name": "Site A",
            }
        ]
    }

    assert auto_client.resolve_site_id() == (
        "22222222-2222-2222-2222-222222222222"
    )

    named_client = m.UniFiClient(
        "https://192.0.2.1",
        "Lab",
        "fixture-key",
        False,
    )

    named_client._get = lambda path: {
        "data": [
            {
                "id":
                    "33333333-3333-3333-3333-333333333333",
                "internalReference": "lab-site",
                "name": "Lab",
            }
        ]
    }

    assert named_client.resolve_site_id() == (
        "33333333-3333-3333-3333-333333333333"
    )

    ambiguous_client = m.UniFiClient(
        "https://192.0.2.1",
        "auto",
        "fixture-key",
        False,
    )

    ambiguous_client._get = lambda path: {
        "data": [
            {
                "id": "site-1",
                "internalReference": "one",
                "name": "One",
            },
            {
                "id": "site-2",
                "internalReference": "two",
                "name": "Two",
            },
        ]
    }

    try:
        ambiguous_client.resolve_site_id()
    except RuntimeError as exc:
        assert "Multiple UniFi Network sites" in str(exc)
    else:
        raise AssertionError(
            "ambiguous automatic site selection was accepted"
        )

    # Support My Switch contribution SV-2026-000002.
    assert m.is_switch({
        "features": ["switching"],
        "model": "US 48 PoE 500W",
    })
    assert m.is_switch({
        "features": [],
        "model": "US 48 PoE 500W",
    })
    assert not m.is_switch({
        "features": ["switching"],
        "model": "UPS 2U",
    })
    assert not m.is_switch({
        "features": {"switching": {}},
        "model": "UPS 2U",
    })

    detail = {
        "id": "device-1", "name": "Lab Switch", "model": "USW Enterprise 8 PoE",
        "state": "ONLINE", "firmwareVersion": "7.4.1", "ipAddress": "192.0.2.10",
        "interfaces": {"ports": [
            {"idx": 1, "state": "UP", "connector": "RJ45", "maxSpeedMbps": 2500, "speedMbps": 1000,
             "poe": {"standard": "802.3at", "type": 2, "enabled": True, "state": "UP"}},
            {"idx": 9, "state": "DOWN", "connector": "SFPPLUS", "maxSpeedMbps": 10000},
        ]},
    }
    stats = {"uptimeSec": 123, "cpuUtilizationPct": 4.5, "memoryUtilizationPct": 30.0,
             "uplink": {"txRateBps": 100, "rxRateBps": 200}, "interfaces": {}}
    n = m.normalize_device(detail, detail, stats)
    assert n["model"] == "USW Enterprise 8 PoE"
    assert n["ip_address"] == "192.0.2.10"
    assert len(n["ports"]) == 2
    assert n["ports"][0]["connector"] == "RJ45"
    assert n["ports"][0]["poe"]["standard"] == "802.3at"
    assert n["ports"][1]["connector"] == "SFPPLUS"
    assert n["system"]["cpu_utilization_pct"] == 4.5
    assert n["api_capabilities"]["per_port_traffic"] is False

    # UDM Pro live-API semantics: the bridge accepts gateway/switch hybrids
    # when switching is exposed and never reports a negotiated speed on DOWN ports.
    assert m.is_switch({"features": ["switching"], "model": "UDM Pro"})
    udm = {
        "id": "udm-1", "name": "Gateway", "model": "UDM Pro",
        "state": "ONLINE", "firmwareVersion": "5.1.26",
        "interfaces": {"ports": [
            {"idx": 1, "state": "DOWN", "connector": "RJ45", "maxSpeedMbps": 1000, "speedMbps": 10},
            {"idx": 9, "state": "UP", "connector": "RJ45", "maxSpeedMbps": 1000, "speedMbps": 1000},
            {"idx": 10, "state": "UP", "connector": "SFPPLUS", "maxSpeedMbps": 10000, "speedMbps": 10000},
            {"idx": 11, "state": "UP", "connector": "SFPPLUS", "maxSpeedMbps": 10000, "speedMbps": 10000},
        ]},
    }
    udm_n = m.normalize_device(udm, udm, stats)
    assert udm_n["model"] == "UDM Pro"
    assert udm_n["ports"][0]["state"] == "DOWN"
    assert udm_n["ports"][0]["speed_mbps"] is None
    assert udm_n["ports"][1]["speed_mbps"] == 1000
    assert udm_n["ports"][2]["connector"] == "SFPPLUS"
    assert udm_n["ports"][3]["connector"] == "SFPPLUS"

    old = dict(detail)
    old["interfaces"] = ["ports"]
    assert m.extract_ports(old) == []

    # Paho 2.x constructor path.
    class V:
        VERSION1 = "v1"
    calls = []
    class C2:
        def __init__(self, *args, **kwargs): calls.append((args, kwargs))
    old_mqtt = m.mqtt
    m.mqtt = types.SimpleNamespace(CallbackAPIVersion=V, Client=C2)
    m.make_mqtt_client()
    assert calls and calls[-1][0] == ("v1",)

    # Paho 1.x constructor path.
    calls.clear()
    class C1:
        def __init__(self, *args, **kwargs): calls.append((args, kwargs))
    m.mqtt = types.SimpleNamespace(Client=C1)
    m.make_mqtt_client()
    assert calls and calls[-1][0] == ()
    m.mqtt = old_mqtt

    # Publish flush path must observe queued message completion before close.
    class Info:
        def __init__(self): self.checked = 0
        def is_published(self):
            self.checked += 1
            return True
    class FakeClient:
        def __init__(self): self.info = Info(); self.stopped = False; self.disconnected = False
        def publish(self, *args, **kwargs): return self.info
        def loop_stop(self): self.stopped = True
        def disconnect(self): self.disconnected = True
    pub = object.__new__(m.Publisher)
    pub.client = FakeClient(); pub._pending = []
    pub.topic_prefix = "switch_vision/unifi"; pub.discovery_prefix = "homeassistant"
    pub.publish("switch_vision/unifi/test", "1")
    assert len(pub._pending) == 1
    pub.close()
    assert pub.client.info.checked >= 1 and pub.client.stopped and pub.client.disconnected


    # A transient detail/statistics failure for one switch must not delete that
    # already-known switch from the normalized snapshot.
    class CapturePublisher:
        last = None
        def __init__(self, cfg):
            self.devices = []
            self.availability = []
            self.cleaned = []
            self.retired = []
            CapturePublisher.last = self
        def require_connected(self):
            pass
        def publish_device(self, device):
            self.devices.append(device)
            return set()
        def publish_availability(self, device, status):
            self.availability.append((device.get("id"), status))
        def cleanup_retired_topics(self, previous, current):
            self.cleaned.append((previous.get("id"), current.get("id")))
            return set()
        def remove_device(self, device):
            self.retired.append(device.get("id"))
            return set()
        def close(self):
            pass

    class PartialApi:
        def __init__(self, *args, **kwargs):
            pass
        def list_devices(self):
            return [
                {"id": "ok", "name": "Fresh", "model": "USW Enterprise 8 PoE", "features": ["switching"]},
                {"id": "keep", "name": "Keep", "model": "USW Pro 24 PoE", "features": ["switching"]},
            ]
        def detail(self, device_id):
            if device_id == "keep":
                raise RuntimeError("transient detail failure")
            return {"id": device_id, "name": "Fresh", "model": "USW Enterprise 8 PoE", "state": "ONLINE", "interfaces": {"ports": []}}
        def stats(self, device_id):
            return {}

    old_publisher, old_api = m.Publisher, m.UniFiClient
    try:
        m.Publisher, m.UniFiClient = CapturePublisher, PartialApi
        with tempfile.TemporaryDirectory() as td:
            snapshot_path = Path(td) / "devices.json"
            previous_device = {
                "id": "keep", "name": "Keep", "model": "USW Pro 24 PoE", "firmware": "7.4.1",
                "state": "ONLINE", "ports": [{"idx": 1, "state": "UP"}], "system": {}, "api_capabilities": {}
            }
            snapshot_path.write_text(json.dumps({"schema_version": 1, "devices": [previous_device]}), encoding="utf-8")
            m.poll_once({"controller_url": "https://controller", "site_id": "site", "api_key": "key", "verify_ssl": "false"}, snapshot_path)
            refreshed = json.loads(snapshot_path.read_text(encoding="utf-8"))
            kept = [d for d in refreshed["devices"] if d.get("id") == "keep"]
            assert len(kept) == 1 and kept[0] == previous_device
            assert CapturePublisher.last is not None
            assert ("keep", "offline") in CapturePublisher.last.availability
    finally:
        m.Publisher, m.UniFiClient = old_publisher, old_api


    # Hardware-free fixture regression: realistic UniFi data through
    # normalization and MQTT/Home Assistant Discovery generation.
    fixture_path = Path(__file__).with_name("fixtures") / "unifi_devices_fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    normalized = m.normalize_device(fixture["summary"], fixture["detail"], fixture["stats"])

    assert normalized["name"] == "Garage Switch"
    assert normalized["model"] == "USW Lite 16 PoE"
    assert normalized["firmware"] == "7.4.1"
    assert normalized["state"] == "ONLINE"
    assert normalized["system"]["cpu_utilization_pct"] == 12.5
    assert normalized["system"]["memory_utilization_pct"] == 43.2
    assert normalized["system"]["uplink_tx_rate_bps"] == 123456
    assert normalized["system"]["uplink_rx_rate_bps"] == 654321
    assert len(normalized["ports"]) == 4

    by_port = {p["idx"]: p for p in normalized["ports"]}
    assert by_port[1]["state"] == "UP"
    assert by_port[1]["speed_mbps"] == 1000
    assert by_port[1]["poe"]["enabled"] is True
    assert by_port[1]["poe"]["state"] == "UP"
    assert by_port[2]["state"] == "DOWN"
    assert by_port[2]["speed_mbps"] is None
    assert by_port[2]["poe"]["state"] == "DOWN"

    class CaptureClient:
        def __init__(self):
            self.messages = []
        def publish(self, topic, payload, qos=0, retain=False):
            self.messages.append((topic, payload, qos, retain))
            return None
        def loop_stop(self):
            pass
        def disconnect(self):
            pass

    fixture_pub = object.__new__(m.Publisher)
    fixture_pub.topic_prefix = "switch_vision/unifi"
    fixture_pub.discovery_prefix = "homeassistant"
    fixture_pub.client = CaptureClient()
    fixture_pub._pending = []
    fixture_pub.publish_device(normalized)

    messages = fixture_pub.client.messages
    topics = {topic: payload for topic, payload, qos, retain in messages}
    assert messages
    assert all(retain is True for _, _, _, retain in messages)
    assert set(topics) == m.retained_topics_for_device(
        normalized, "switch_vision/unifi", "homeassistant"
    )

    base = "switch_vision/unifi/garage_switch_1"
    assert topics[f"{base}/available"] == "online"
    assert topics[f"{base}/model"] == "USW Lite 16 PoE"
    assert topics[f"{base}/firmware"] == "7.4.1"
    assert topics[f"{base}/online"] == "ON"
    assert topics[f"{base}/cpu"] == "12.5"
    assert topics[f"{base}/memory"] == "43.2"
    assert topics[f"{base}/uplink_tx_rate"] == "123456"
    assert topics[f"{base}/uplink_rx_rate"] == "654321"
    assert topics[f"{base}/port/1/status"] == "ON"
    assert topics[f"{base}/port/1/speed"] == "1000"
    assert topics[f"{base}/port/1/poe_enabled"] == "ON"
    assert topics[f"{base}/port/1/poe_active"] == "ON"
    assert topics[f"{base}/port/2/status"] == "OFF"
    assert f"{base}/port/2/speed" not in topics
    assert topics[f"{base}/port/2/poe_active"] == "OFF"

    discovery_topic = "homeassistant/sensor/switch_vision_unifi_garage_switch_1_model/config"
    discovery = json.loads(topics[discovery_topic])
    assert discovery["unique_id"] == "switch_vision_unifi_garage_switch_1_model"
    assert discovery["state_topic"] == f"{base}/model"
    assert discovery["availability_mode"] == "all"
    assert discovery["availability"] == [
        {
            "topic": "switch_vision/unifi/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
        {
            "topic": f"{base}/available",
            "payload_available": "online",
            "payload_not_available": "offline",
        },
    ]
    assert discovery["device"]["identifiers"] == ["switch_vision_unifi_garage_switch_1"]
    assert discovery["device"]["manufacturer"] == "Ubiquiti"
    assert discovery["device"]["model"] == "USW Lite 16 PoE"

    port_discovery_topic = "homeassistant/binary_sensor/switch_vision_unifi_garage_switch_1_port_1_status/config"
    port_discovery = json.loads(topics[port_discovery_topic])
    assert port_discovery["state_topic"] == f"{base}/port/1/status"
    assert port_discovery["payload_on"] == "ON"
    assert port_discovery["payload_off"] == "OFF"

    previous = json.loads(json.dumps(normalized))
    previous["ports"].append({
        "idx": 99,
        "state": "UP",
        "connector": "RJ45",
        "max_speed_mbps": 1000,
        "speed_mbps": 1000,
        "poe": {
            "available": False,
            "standard": None,
            "type": None,
            "enabled": None,
            "state": None,
        },
    })
    cleanup_pub = object.__new__(m.Publisher)
    cleanup_pub.topic_prefix = "switch_vision/unifi"
    cleanup_pub.discovery_prefix = "homeassistant"
    cleanup_pub.client = CaptureClient()
    cleanup_pub._pending = []
    stale = cleanup_pub.cleanup_retired_topics(previous, normalized)
    expected_stale = (
        m.retained_topics_for_device(previous, cleanup_pub.topic_prefix, cleanup_pub.discovery_prefix)
        - m.retained_topics_for_device(normalized, cleanup_pub.topic_prefix, cleanup_pub.discovery_prefix)
    )
    assert stale == expected_stale
    assert stale
    assert {
        topic for topic, payload, qos, retain in cleanup_pub.client.messages if payload == "" and retain is True
    } == expected_stale

    remove_pub = object.__new__(m.Publisher)
    remove_pub.topic_prefix = "switch_vision/unifi"
    remove_pub.discovery_prefix = "homeassistant"
    remove_pub.client = CaptureClient()
    remove_pub._pending = []
    removed = remove_pub.remove_device(normalized)
    assert removed == m.retained_topics_for_device(
        normalized, remove_pub.topic_prefix, remove_pub.discovery_prefix
    )
    assert {
        topic for topic, payload, qos, retain in remove_pub.client.messages if payload == "" and retain is True
    } == removed

    # v2.0.43 tolerant UniFi switch classification.
    cases = [
        (
            {
                "model": "UDM SE",
                "features": [],
            },
            True,
            "known_gateway_switch_hybrid",
        ),
        (
            {
                "model": "UDM-Pro-SE",
                "features": ["routing"],
            },
            True,
            "known_gateway_switch_hybrid",
        ),
        (
            {
                "model": "USW 24 Pro",
                "features": ["accessPoint"],
            },
            True,
            "unifi_switch_model",
        ),
        (
            {
                "model": "USW 8",
                "features": [],
            },
            True,
            "unifi_switch_model",
        ),
        (
            {
                "model": "USW 24 POE",
                "features": ["somethingElse"],
            },
            True,
            "unifi_switch_model",
        ),
        (
            {
                "model": "UPS 2U",
                "features": ["switching"],
            },
            False,
            "managed_power_device",
        ),
        (
            {
                "model": "PDU Pro",
                "features": {"switching": {}},
            },
            False,
            "managed_power_device",
        ),
    ]

    for device, expected, reason in cases:
        actual, actual_reason = (
            m.switch_classification(device)
        )
        assert actual is expected, (
            device,
            actual,
            expected,
        )
        assert actual_reason == reason, (
            device,
            actual_reason,
            reason,
        )
        assert m.is_switch(device) is expected

    # Diagnostics must retain hardware/classification
    # evidence without identifiers, names, addresses,
    # credentials, or other user-specific fields.
    with tempfile.TemporaryDirectory() as td:
        snapshot_path = (
            Path(td) / "devices.json"
        )

        diagnostic_devices = [
            {
                "id": "PRIVATE-DEVICE-ID",
                "name": "PRIVATE-DEVICE-NAME",
                "mac": "aa:bb:cc:dd:ee:ff",
                "ip": "192.0.2.123",
                "serial": "PRIVATE-SERIAL",
                "model": "UDM SE",
                "features": [
                    "routing",
                    "switching",
                ],
            },
            {
                "id": "PRIVATE-SWITCH-ID",
                "name": "PRIVATE-SWITCH-NAME",
                "model": "USW 24 Pro",
                "features": [
                    "switching",
                ],
            },
            {
                "id": "PRIVATE-UPS-ID",
                "name": "PRIVATE-UPS-NAME",
                "model": "UPS 2U",
                "features": [
                    "switching",
                ],
            },
        ]

        m.write_diagnostics(
            snapshot_path,
            status="success",
            stage="complete",
            devices=diagnostic_devices,
        )

        diagnostics_path = (
            Path(td) / "diagnostics.json"
        )

        assert diagnostics_path.is_file()
        assert (
            os.stat(diagnostics_path).st_mode
            & 0o777
        ) == 0o600

        diagnostics = json.loads(
            diagnostics_path.read_text(
                encoding="utf-8"
            )
        )

        assert diagnostics["status"] == "success"
        assert diagnostics["stage"] == "complete"
        assert diagnostics["adopted_devices"] == 3
        assert diagnostics["switching_devices"] == 2
        assert diagnostics["rejected_devices"] == 1

        rows = diagnostics[
            "device_classification"
        ]

        assert [
            row["model"]
            for row in rows
        ] == [
            "UDM SE",
            "USW 24 Pro",
            "UPS 2U",
        ]

        assert rows[0]["accepted"] is True
        assert rows[1]["accepted"] is True
        assert rows[2]["accepted"] is False

        raw = diagnostics_path.read_text(
            encoding="utf-8"
        )

        forbidden = (
            "PRIVATE-DEVICE-ID",
            "PRIVATE-DEVICE-NAME",
            "PRIVATE-SWITCH-ID",
            "PRIVATE-SWITCH-NAME",
            "PRIVATE-UPS-ID",
            "PRIVATE-UPS-NAME",
            "PRIVATE-SERIAL",
            "aa:bb:cc:dd:ee:ff",
            "192.0.2.123",
        )

        for value in forbidden:
            assert value not in raw, value

    # A List Adopted Devices failure must leave
    # persistent privacy-safe diagnostics instead
    # of only the snapshot lock file.
    class FailingListApi:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            pass

        def list_devices(self):
            raise RuntimeError(
                "synthetic API failure"
            )

    old_publisher = m.Publisher
    old_api = m.UniFiClient

    try:
        m.Publisher = CapturePublisher
        m.UniFiClient = FailingListApi

        with tempfile.TemporaryDirectory() as td:
            snapshot_path = (
                Path(td) / "devices.json"
            )

            try:
                m.poll_once(
                    {
                        "controller_url":
                            "https://controller",
                        "site_id": "site",
                        "api_key": "key",
                        "verify_ssl": "false",
                    },
                    snapshot_path,
                )
            except RuntimeError as exc:
                assert (
                    "synthetic API failure"
                    in str(exc)
                )
            else:
                raise AssertionError(
                    "API failure was not raised"
                )

            diagnostics = json.loads(
                (
                    Path(td)
                    / "diagnostics.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            assert (
                diagnostics["status"]
                == "error"
            )
            assert (
                diagnostics["stage"]
                == "list_devices"
            )
            assert (
                diagnostics["error_type"]
                == "RuntimeError"
            )
            assert (
                diagnostics[
                    "adopted_devices"
                ]
                == 0
            )

    finally:
        m.Publisher = old_publisher
        m.UniFiClient = old_api

    print(
        "UniFi v2.0.45 classification/"
        "diagnostics regression: PASS"
    )

    print(f"Offline fixture MQTT/Discovery messages validated: {len(messages)}")

    print("Switch Vision UniFi2MQTT self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
