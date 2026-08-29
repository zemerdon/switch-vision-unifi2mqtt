#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import unifi2mqtt as core
from controller_config import parse_controller_entries
from multi_controller import NamespacedPublisher, poll_multi_once


def controller_rows() -> list[dict]:
    return parse_controller_entries(
        {
            "verify_ssl": "true",
            "allow_insecure_http": "false",
            "controllers": [
                {
                    "id": "home",
                    "controller_url": "https://10.0.0.1",
                    "site_id": "default",
                    "api_key": "home-secret",
                },
                {
                    "id": "remote",
                    "controller_url": "https://10.1.0.1",
                    "site_id": "Remote Site",
                    "api_key": "remote-secret",
                },
            ],
        }
    )


def sample_device(name: str, raw_id: str = "same-device-id") -> dict:
    return {
        "id": raw_id,
        "name": name,
        "model": "USW Pro 24 PoE",
        "firmware": "test",
        "state": "ONLINE",
        "ports": [],
        "system": {},
        "api_capabilities": {
            "port_detail": False,
            "per_port_traffic": False,
        },
    }


class FakePublisher:
    def __init__(self) -> None:
        self.namespace = ""
        self.offline: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self.flushes = 0

    def set_identity_namespace(self, namespace: str) -> None:
        self.namespace = namespace

    def publish_availability(self, device: dict, status: str) -> None:
        self.offline.append((self.namespace, str(device.get("id"))))

    def remove_device(self, device: dict) -> set[str]:
        self.removed.append((self.namespace, str(device.get("id"))))
        return {f"topic/{self.namespace}/{device.get('id')}"}

    def flush(self) -> None:
        self.flushes += 1


def global_cfg() -> dict:
    return {
        "mqtt_host": "mqtt",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "mqtt_tls": False,
        "mqtt_verify_ssl": True,
        "mqtt_ca": "",
        "mqtt_topic_prefix": "switch_vision/unifi",
        "mqtt_discovery_prefix": "homeassistant",
        "poll_interval": 30,
    }


def test_controller_config_validation() -> None:
    rows = controller_rows()
    assert [row["namespace"] for row in rows] == ["home", "remote"]
    assert rows[1]["site_id"] == "Remote Site"

    try:
        parse_controller_entries(
            {
                "controllers": [
                    {
                        "id": "a-b",
                        "controller_url": "https://10.0.0.1",
                        "api_key": "one",
                    },
                    {
                        "id": "a_b",
                        "controller_url": "https://10.0.0.2",
                        "api_key": "two",
                    },
                ]
            }
        )
    except RuntimeError as exc:
        assert "collides" in str(exc)
    else:
        raise AssertionError("normalized controller-id collision was accepted")

    try:
        parse_controller_entries(
            {
                "controllers": [
                    {
                        "id": "../bad",
                        "controller_url": "https://10.0.0.1",
                        "api_key": "one",
                    }
                ]
            }
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsafe controller id was accepted")


def test_namespaced_publisher_identity() -> None:
    publisher = NamespacedPublisher.__new__(NamespacedPublisher)
    publisher.identity_namespace = "remote"
    original = sample_device("Switch")
    namespaced = publisher._namespaced_device(original)
    assert namespaced["id"] == "remote__same-device-id"
    assert original["id"] == "same-device-id"


def test_two_controllers_with_same_device_id_do_not_collide() -> None:
    rows = controller_rows()
    publisher = FakePublisher()

    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "devices.json"

        def fake_poller(cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            name = "Home" if "10.0.0.1" in cfg["controller_url"] else "Remote"
            core.write_snapshot(controller_snapshot, [sample_device(name)], 0)

        devices = poll_multi_once(
            global_cfg(),
            rows,
            snapshot,
            publisher,
            poller=fake_poller,
        )

        ids = sorted(str(item["id"]) for item in devices)
        assert ids == ["home__same-device-id", "remote__same-device-id"]
        assert {item["controller_id"] for item in devices} == {"home", "remote"}
        assert {item["source_device_id"] for item in devices} == {"same-device-id"}

        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert sorted(item["id"] for item in payload["devices"]) == ids

        diagnostics = json.loads(
            (Path(temp) / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["status"] == "success"
        assert diagnostics["controllers_configured"] == 2
        assert diagnostics["controllers_successful"] == 2
        assert diagnostics["switching_devices"] == 2
        assert "home" not in json.dumps(diagnostics).lower()
        assert "remote" not in json.dumps(diagnostics).lower()
        assert "10.0.0.1" not in json.dumps(diagnostics)
        assert "10.1.0.1" not in json.dumps(diagnostics)


def test_failed_controller_preserves_snapshot_and_marks_it_offline() -> None:
    rows = controller_rows()
    publisher = FakePublisher()

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        snapshot = root / "devices.json"
        remote_snapshot = root / "controllers" / "remote" / "devices.json"
        core.write_snapshot(remote_snapshot, [sample_device("Remote previous")], 0)

        def fake_poller(cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            if "10.1.0.1" in cfg["controller_url"]:
                raise RuntimeError("simulated remote outage")
            core.write_snapshot(controller_snapshot, [sample_device("Home")], 0)

        devices = poll_multi_once(
            global_cfg(),
            rows,
            snapshot,
            publisher,
            poller=fake_poller,
        )

        assert sorted(item["id"] for item in devices) == [
            "home__same-device-id",
            "remote__same-device-id",
        ]
        assert ("remote", "same-device-id") in publisher.offline

        diagnostics = json.loads((root / "diagnostics.json").read_text(encoding="utf-8"))
        assert diagnostics["status"] == "partial"
        assert diagnostics["controllers_successful"] == 1
        assert diagnostics["controllers_failed"] == 1


def test_removed_controller_is_retired_without_touching_current_controller() -> None:
    home = controller_rows()[:1]
    publisher = FakePublisher()

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        snapshot = root / "devices.json"
        old_snapshot = root / "controllers" / "old" / "devices.json"
        core.write_snapshot(old_snapshot, [sample_device("Old")], 0)
        (root / "controller_state.json").write_text(
            json.dumps({"controllers": ["old"]}),
            encoding="utf-8",
        )
        core.secure_directory(root)
        (root / "controller_state.json").chmod(0o600)

        def fake_poller(_cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            core.write_snapshot(controller_snapshot, [sample_device("Home")], 0)

        devices = poll_multi_once(
            global_cfg(),
            home,
            snapshot,
            publisher,
            poller=fake_poller,
        )

        assert [item["id"] for item in devices] == ["home__same-device-id"]
        assert ("old", "same-device-id") in publisher.removed
        registry = json.loads((root / "controller_state.json").read_text(encoding="utf-8"))
        assert registry["controllers"] == ["home"]


def main() -> int:
    test_controller_config_validation()
    test_namespaced_publisher_identity()
    test_two_controllers_with_same_device_id_do_not_collide()
    test_failed_controller_preserves_snapshot_and_marks_it_offline()
    test_removed_controller_is_retired_without_touching_current_controller()
    print("multi-controller regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
