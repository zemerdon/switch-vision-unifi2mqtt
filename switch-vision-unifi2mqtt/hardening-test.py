#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
from pathlib import Path
from urllib.error import HTTPError


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
        paho_mod = types.ModuleType("paho")
        mqtt_mod = types.ModuleType("paho.mqtt")
        paho_mod.mqtt = mqtt_mod
        mqtt_mod.client = client_mod
        sys.modules["paho"] = paho_mod
        sys.modules["paho.mqtt"] = mqtt_mod
        sys.modules["paho.mqtt.client"] = client_mod
    path = Path(__file__).with_name("unifi2mqtt.py")
    spec = importlib.util.spec_from_file_location("sv_unifi_hardening", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config_payload(**updates):
    data = {
        "controller_url": "https://192.0.2.1",
        "site_id": "default",
        "api_key": "fixture-key",
        "verify_ssl": "true",
        "allow_insecure_http": "false",
        "poll_interval": "30",
        "mqtt_host": "mqtt.local",
        "mqtt_port": "1883",
        "mqtt_username": "",
        "mqtt_password": "",
        "mqtt_tls": "false",
        "mqtt_verify_ssl": "true",
        "mqtt_ca": "",
        "mqtt_topic_prefix": "switch_vision/unifi",
        "mqtt_discovery_prefix": "homeassistant",
    }
    data.update(updates)
    return data


def load_cfg(m, data):
    names = ("SV_MQTT_HOST", "SV_MQTT_PORT", "SV_MQTT_USERNAME", "SV_MQTT_PASSWORD")
    saved = {name: os.environ.pop(name, None) for name in names}
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "options.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return m.load_config(path)
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def expect_config_failure(m, data, text):
    try:
        load_cfg(m, data)
    except RuntimeError as exc:
        assert text in str(exc), (text, str(exc))
    else:
        raise AssertionError(f"invalid configuration accepted: expected {text!r}")


def main() -> int:
    m = load_module()
    assert m.VERSION == "2.0.48"
    assert m.EMPTY_SWITCH_CONFIRM_POLLS == 3
    assert not m.is_switch({"features": ["switching"], "model": "AirWire"})
    assert m.is_switch({"features": ["switching"], "model": "UDM Pro Max"})

    cfg = load_cfg(m, config_payload())
    assert cfg["controller_url"] == "https://192.0.2.1"
    assert cfg["mqtt_port"] == "1883"
    assert cfg["poll_interval"] == "30"
    assert cfg["mqtt_topic_prefix"] == "switch_vision/unifi"

    expect_config_failure(m, config_payload(controller_url="ftp://192.0.2.1"), "absolute http:// or https://")
    expect_config_failure(
        m,
        config_payload(controller_url="http://192.0.2.1"),
        "allow_insecure_http",
    )
    http_cfg = load_cfg(
        m,
        config_payload(
            controller_url="http://192.0.2.1",
            allow_insecure_http="true",
        ),
    )
    assert http_cfg["controller_url"] == "http://192.0.2.1"
    assert http_cfg["allow_insecure_http"] is True
    expect_config_failure(m, config_payload(controller_url="https://user:pass@192.0.2.1"), "embedded credentials")
    expect_config_failure(m, config_payload(controller_url="https://192.0.2.1/path"), "without an extra path")
    expect_config_failure(m, config_payload(mqtt_topic_prefix="switch_vision/#"), "invalid MQTT topic prefix")
    expect_config_failure(m, config_payload(mqtt_discovery_prefix="homeassistant/+"), "invalid MQTT topic prefix")
    expect_config_failure(m, config_payload(mqtt_port="70000"), "between 1 and 65535")

    cfg_tls = load_cfg(
        m,
        config_payload(
            mqtt_tls="true",
            mqtt_verify_ssl="false",
        ),
    )
    assert cfg_tls["mqtt_tls"] is True
    assert cfg_tls["mqtt_verify_ssl"] is False
    assert cfg_tls["mqtt_ca"] == ""

    source = Path(__file__).with_name("unifi2mqtt.py").read_text(encoding="utf-8")
    assert "self.client.tls_set(" in source
    assert "self.client.tls_insecure_set(True)" in source
    assert "ssl.CERT_REQUIRED if verify_mqtt_tls else ssl.CERT_NONE" in source

    client = m.UniFiClient("https://192.0.2.1", "default", "fixture-key", False)
    old_urlopen = m.urlopen
    secret = "DO_NOT_LOG_THIS_BODY_SECRET"
    def fail_urlopen(*args, **kwargs):
        raise HTTPError(
            "https://192.0.2.1/test", 500, "failure", None, io.BytesIO(secret.encode())
        )
    m.urlopen = fail_urlopen
    try:
        try:
            client._get("/test")
        except RuntimeError as exc:
            assert "HTTP 500" in str(exc)
            assert secret not in str(exc)
        else:
            raise AssertionError("HTTP error was not raised")
    finally:
        m.urlopen = old_urlopen

    auth_secret = "DO_NOT_LOG_AUTH_RESPONSE"

    def fail_auth(*args, **kwargs):
        raise HTTPError(
            "https://192.0.2.1/test",
            401,
            "unauthorized",
            None,
            io.BytesIO(
                auth_secret.encode()
            ),
        )

    m.urlopen = fail_auth

    try:
        try:
            client._get("/test")
        except RuntimeError as exc:
            message = str(exc)
            assert "HTTP 401" in message
            assert (
                "Network Integration API key"
                in message
            )
            assert auth_secret not in message
        else:
            raise AssertionError(
                "HTTP 401 was not raised"
            )
    finally:
        m.urlopen = old_urlopen

    class GuardPublisher:
        def require_connected(self):
            pass
        last = None
        def __init__(self, cfg):
            self.retired = []
            self.availability = []
            GuardPublisher.last = self
        def publish_device(self, device):
            return set()
        def publish_availability(self, device, status):
            self.availability.append((device.get("id"), status))
        def cleanup_retired_topics(self, previous, current):
            return set()
        def remove_device(self, device):
            self.retired.append(device.get("id"))
            return set()
        def close(self):
            pass

    class EmptyApi:
        def __init__(self, *args, **kwargs):
            pass
        def list_devices(self):
            return []
        def detail(self, device_id):
            raise AssertionError("detail should not be called for an empty poll")
        def stats(self, device_id):
            raise AssertionError("stats should not be called for an empty poll")

    old_publisher, old_api = m.Publisher, m.UniFiClient
    try:
        m.Publisher, m.UniFiClient = GuardPublisher, EmptyApi
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "private-state"
            snapshot = root / "devices.json"
            root.mkdir()
            previous_device = {
                "id": "known-1",
                "name": "Known Switch",
                "model": "USW Pro 24 PoE",
                "firmware": "7.4.1",
                "state": "ONLINE",
                "ports": [],
                "system": {},
                "api_capabilities": {},
            }
            snapshot.write_text(json.dumps({"schema_version": 1, "devices": [previous_device]}), encoding="utf-8")

            for expected_streak in (1, 2):
                m.poll_once(config_payload(), snapshot)
                doc = json.loads(snapshot.read_text(encoding="utf-8"))
                assert doc["devices"] == [previous_device]
                assert doc["empty_switch_polls"] == expected_streak
                assert GuardPublisher.last is not None
                assert GuardPublisher.last.retired == []
                assert ("known-1", "offline") in GuardPublisher.last.availability
                assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
                assert stat.S_IMODE(snapshot.parent.stat().st_mode) == 0o700

            m.poll_once(config_payload(), snapshot)
            doc = json.loads(snapshot.read_text(encoding="utf-8"))
            assert doc["devices"] == []
            assert doc["empty_switch_polls"] == 0
            assert GuardPublisher.last is not None
            assert GuardPublisher.last.retired == ["known-1"]

            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            try:
                m.write_snapshot(link, [], 0)
            except RuntimeError as exc:
                assert "symlink snapshot" in str(exc)
            else:
                raise AssertionError("symlink snapshot target was accepted")
    finally:
        m.Publisher, m.UniFiClient = old_publisher, old_api

    app_dir = Path(__file__).resolve().parent
    config_text = (app_dir / "config.yaml").read_text(encoding="utf-8")
    run_text = (app_dir / "run.sh").read_text(encoding="utf-8")
    assert 'verify_ssl: "true"' in config_text
    assert 'site_id: "auto"' in config_text
    assert "umask 077" in run_text and "chmod 0700 /share/switch_vision/unifi" in run_text

    print("Switch Vision UniFi2MQTT v2.0.48 hardening regression: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
