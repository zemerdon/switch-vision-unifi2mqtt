#!/usr/bin/env python3
# Offline MQTT lifecycle regression for Switch Vision UniFi2MQTT v2.0.45.
from __future__ import annotations

import importlib.util
import json
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
                pass
        client_mod.CallbackAPIVersion = CallbackAPIVersion
        client_mod.Client = Client
        client_mod.MQTT_ERR_SUCCESS = 0
        paho_mod = types.ModuleType("paho")
        mqtt_mod = types.ModuleType("paho.mqtt")
        paho_mod.mqtt = mqtt_mod
        mqtt_mod.client = client_mod
        sys.modules["paho"] = paho_mod
        sys.modules["paho.mqtt"] = mqtt_mod
        sys.modules["paho.mqtt.client"] = client_mod

    path = Path(__file__).with_name("unifi2mqtt.py")
    spec = importlib.util.spec_from_file_location(
        "switch_vision_unifi2mqtt_lifecycle",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load unifi2mqtt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Info:
    def __init__(self, rc=0):
        self.rc = rc
        self.checked = 0

    def is_published(self):
        self.checked += 1
        return True


class FakeClient:
    def __init__(self):
        self.messages = []
        self.will = None
        self.connect_async_calls = []
        self.reconnect_delays = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self.connected = True
        self.next_rc = 0
        self.on_connect = None

    def username_pw_set(self, *args):
        self.auth = args

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, payload, qos, retain)

    def reconnect_delay_set(self, min_delay, max_delay):
        self.reconnect_delays = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive):
        self.connect_async_calls.append(
            (host, port, keepalive)
        )

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos=0, retain=False):
        self.messages.append(
            (topic, payload, qos, retain)
        )
        rc = self.next_rc
        self.next_rc = 0
        return Info(rc)


def main():
    m = load_module()
    fake = FakeClient()
    original_factory = m.make_mqtt_client
    m.make_mqtt_client = lambda: fake
    try:
        pub = m.Publisher({
            "mqtt_host": "mqtt.example",
            "mqtt_port": 1883,
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "mqtt_topic_prefix":
                "switch_vision/unifi",
            "mqtt_discovery_prefix":
                "homeassistant",
        })
    finally:
        m.make_mqtt_client = original_factory

    assert fake.will == (
        "switch_vision/unifi/status",
        "offline",
        1,
        True,
    )
    assert fake.connect_async_calls == [
        ("mqtt.example", 1883, 60)
    ]
    assert fake.reconnect_delays == (1, 30)
    assert fake.loop_started is True

    pub._on_connect(fake, None, None, 0)
    assert (
        "switch_vision/unifi/status",
        "online",
        1,
        True,
    ) in fake.messages

    device = {
        "id": "switch-1",
        "name": "Test Switch",
        "model": "USW Lite 8 PoE",
        "firmware": "1.0",
        "state": "ONLINE",
        "ports": [],
        "system": {},
        "api_capabilities": {},
    }
    pub.publish_device(device)
    pub.flush()
    assert pub._pending == []

    topics = {
        topic: (payload, qos, retain)
        for topic, payload, qos, retain
        in fake.messages
    }
    available = (
        "switch_vision/unifi/switch_1/available"
    )
    assert topics[available] == (
        "online",
        1,
        True,
    )

    discovery_topic = (
        "homeassistant/sensor/"
        "switch_vision_unifi_switch_1_model/config"
    )
    payload = json.loads(
        topics[discovery_topic][0]
    )
    assert topics[discovery_topic][1] == 1
    assert payload["availability_mode"] == "all"
    assert [
        x["topic"]
        for x in payload["availability"]
    ] == [
        "switch_vision/unifi/status",
        available,
    ]

    fake.connected = False
    try:
        pub.require_connected()
    except RuntimeError:
        pass
    else:
        raise AssertionError(
            "disconnected MQTT broker was accepted"
        )
    fake.connected = True
    pub.require_connected()

    fake.next_rc = 4
    try:
        pub.publish(
            "switch_vision/unifi/test",
            "1",
        )
    except RuntimeError as exc:
        assert "rc=4" in str(exc)
    else:
        raise AssertionError(
            "failed MQTT publish return code was ignored"
        )

    for index in range(500):
        pub.publish(
            f"switch_vision/unifi/burst/{index}",
            index,
        )
    pub.flush()
    assert pub._pending == []

    pub.close()
    assert (
        "switch_vision/unifi/status",
        "offline",
        1,
        True,
    ) in fake.messages
    assert fake.disconnected is True
    assert fake.loop_stopped is True

    print(
        "UniFi2MQTT v2.0.45 MQTT lifecycle regression: PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
