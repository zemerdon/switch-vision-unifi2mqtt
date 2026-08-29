#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import unifi2mqtt as core
from controller_config import controller_namespace, parse_controller_entries
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
    assert [row["namespace"] for row in rows] == [
        controller_namespace("home"),
        controller_namespace("remote"),
    ]
    assert rows[0]["namespace"] != "home"
    assert rows[1]["namespace"] != "remote"
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
    namespace = controller_namespace("remote")
    publisher = NamespacedPublisher.__new__(NamespacedPublisher)
    publisher.identity_namespace = namespace
    original = sample_device("Switch")
    namespaced = publisher._namespaced_device(original)
    assert namespaced["id"] == f"{namespace}__same-device-id"
    assert original["id"] == "same-device-id"


def test_two_controllers_with_same_device_id_do_not_collide() -> None:
    rows = controller_rows()
    publisher = FakePublisher()
    home_ns, remote_ns = [row["namespace"] for row in rows]

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        public_root = root / "public"
        state_root = root / "private"
        snapshot = public_root / "devices.json"

        def fake_poller(cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            name = "Home" if "10.0.0.1" in cfg["controller_url"] else "Remote"
            core.write_snapshot(controller_snapshot, [sample_device(name)], 0)

        devices = poll_multi_once(
            global_cfg(),
            rows,
            snapshot,
            state_root,
            publisher,
            poller=fake_poller,
        )

        ids = sorted(str(item["id"]) for item in devices)
        assert ids == sorted(
            [
                f"{home_ns}__same-device-id",
                f"{remote_ns}__same-device-id",
            ]
        )
        assert all("controller_id" not in item for item in devices)
        assert all("source_device_id" not in item for item in devices)

        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert sorted(item["id"] for item in payload["devices"]) == ids

        diagnostics = json.loads(
            (public_root / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["status"] == "success"
        assert diagnostics["controllers_configured"] == 2
        assert diagnostics["controllers_successful"] == 2
        assert diagnostics["switching_devices"] == 2
        serialized = json.dumps(diagnostics).lower()
        assert "home" not in serialized
        assert "remote" not in serialized
        assert "10.0.0.1" not in serialized
        assert "10.1.0.1" not in serialized

        # Private controller state must not live under the Support My Switch source tree.
        assert not (public_root / "controllers").exists()
        assert (state_root / "controllers" / home_ns / "devices.json").is_file()
        assert (state_root / "controllers" / remote_ns / "devices.json").is_file()


def test_failed_controller_preserves_snapshot_and_marks_it_offline() -> None:
    rows = controller_rows()
    publisher = FakePublisher()
    home_ns, remote_ns = [row["namespace"] for row in rows]

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        public_root = root / "public"
        state_root = root / "private"
        snapshot = public_root / "devices.json"
        remote_snapshot = state_root / "controllers" / remote_ns / "devices.json"
        core.write_snapshot(remote_snapshot, [sample_device("Remote previous")], 0)

        def fake_poller(cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            if "10.1.0.1" in cfg["controller_url"]:
                raise RuntimeError("simulated remote outage")
            core.write_snapshot(controller_snapshot, [sample_device("Home")], 0)

        devices = poll_multi_once(
            global_cfg(),
            rows,
            snapshot,
            state_root,
            publisher,
            poller=fake_poller,
        )

        assert sorted(item["id"] for item in devices) == sorted(
            [
                f"{home_ns}__same-device-id",
                f"{remote_ns}__same-device-id",
            ]
        )
        assert (remote_ns, "same-device-id") in publisher.offline

        diagnostics = json.loads(
            (public_root / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["status"] == "partial"
        assert diagnostics["controllers_successful"] == 1
        assert diagnostics["controllers_failed"] == 1


def test_removed_controller_is_retired_without_touching_current_controller() -> None:
    home = controller_rows()[:1]
    publisher = FakePublisher()
    home_ns = home[0]["namespace"]
    old_ns = controller_namespace("old")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        public_root = root / "public"
        state_root = root / "private"
        snapshot = public_root / "devices.json"
        old_snapshot = state_root / "controllers" / old_ns / "devices.json"
        core.write_snapshot(old_snapshot, [sample_device("Old")], 0)
        core.secure_directory(state_root)
        (state_root / "controller_state.json").write_text(
            json.dumps({"controllers": [old_ns]}),
            encoding="utf-8",
        )
        (state_root / "controller_state.json").chmod(0o600)

        def fake_poller(_cfg: dict, controller_snapshot: Path, _publisher: FakePublisher) -> None:
            core.write_snapshot(controller_snapshot, [sample_device("Home")], 0)

        devices = poll_multi_once(
            global_cfg(),
            home,
            snapshot,
            state_root,
            publisher,
            poller=fake_poller,
        )

        assert [item["id"] for item in devices] == [f"{home_ns}__same-device-id"]
        assert (old_ns, "same-device-id") in publisher.removed
        assert not (state_root / "controllers" / old_ns).exists()
        registry = json.loads(
            (state_root / "controller_state.json").read_text(encoding="utf-8")
        )
        assert registry["controllers"] == [home_ns]


def test_removed_controller_state_is_preserved_if_retirement_fails() -> None:
    home = controller_rows()[:1]
    old_ns = controller_namespace("old")

    class FailingPublisher(FakePublisher):
        def remove_device(self, device: dict) -> set[str]:
            raise RuntimeError("simulated retirement failure")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        public_root = root / "public"
        state_root = root / "private"
        snapshot = public_root / "devices.json"
        old_snapshot = state_root / "controllers" / old_ns / "devices.json"
        core.write_snapshot(old_snapshot, [sample_device("Old")], 0)
        core.secure_directory(state_root)
        (state_root / "controller_state.json").write_text(
            json.dumps({"controllers": [old_ns]}),
            encoding="utf-8",
        )
        (state_root / "controller_state.json").chmod(0o600)

        try:
            poll_multi_once(
                global_cfg(),
                home,
                snapshot,
                state_root,
                FailingPublisher(),
                poller=lambda *_args: None,
            )
        except RuntimeError as exc:
            assert "retirement failure" in str(exc)
        else:
            raise AssertionError("controller retirement failure was ignored")

        assert old_snapshot.is_file()
        registry = json.loads(
            (state_root / "controller_state.json").read_text(encoding="utf-8")
        )
        assert registry["controllers"] == [old_ns]


def test_unsafe_stored_controller_namespace_fails_closed() -> None:
    home = controller_rows()[:1]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        public_root = root / "public"
        state_root = root / "private"
        snapshot = public_root / "devices.json"
        core.secure_directory(state_root)
        (state_root / "controller_state.json").write_text(
            json.dumps({"controllers": ["../escape"]}),
            encoding="utf-8",
        )
        (state_root / "controller_state.json").chmod(0o600)

        try:
            poll_multi_once(
                global_cfg(),
                home,
                snapshot,
                state_root,
                FakePublisher(),
                poller=lambda *_args: None,
            )
        except RuntimeError as exc:
            assert "Invalid controller namespace" in str(exc)
        else:
            raise AssertionError("unsafe stored controller namespace was accepted")


def main() -> int:
    test_controller_config_validation()
    test_namespaced_publisher_identity()
    test_two_controllers_with_same_device_id_do_not_collide()
    test_failed_controller_preserves_snapshot_and_marks_it_offline()
    test_removed_controller_is_retired_without_touching_current_controller()
    test_removed_controller_state_is_preserved_if_retirement_fails()
    test_unsafe_stored_controller_namespace_fails_closed()
    print("multi-controller regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
