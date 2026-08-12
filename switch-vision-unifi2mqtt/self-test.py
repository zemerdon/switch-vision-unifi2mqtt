#!/usr/bin/env python3
"""Offline regression checks for Switch Vision UniFi2MQTT."""
from __future__ import annotations

import importlib.util
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
            assert "site_id" in str(exc) and "api_key" in str(exc)
        else:
            raise AssertionError("null required UniFi configuration was accepted")

    assert m.is_switch({"features": ["switching"], "model": "anything"})
    assert m.is_switch({"features": {"switching": {}}, "model": "anything"})
    assert m.is_switch({"features": [], "model": "USW Pro 24 PoE"})
    assert not m.is_switch({"features": ["accessPoint"], "model": "U6 Pro"})

    detail = {
        "id": "device-1", "name": "Lab Switch", "model": "USW Enterprise 8 PoE",
        "state": "ONLINE", "firmwareVersion": "7.4.1",
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
        def __init__(self, cfg):
            self.devices = []
        def publish_device(self, device):
            self.devices.append(device)
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
    finally:
        m.Publisher, m.UniFiClient = old_publisher, old_api

    print("Switch Vision UniFi2MQTT self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
