#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import unifi2mqtt as core
from controller_config import (
    controller_namespace,
    parse_controller_entries,
    parse_single_connection_profiles,
)
from multi_controller import NamespacedPublisher, poll_multi_once, poll_single_with_failover


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


def test_remote_controller_entry_uses_site_manager_connector_transport() -> None:
    rows = parse_controller_entries(
        {
            "verify_ssl": "false",
            "allow_insecure_http": "true",
            "controllers": [
                {
                    "id": "cloud",
                    "transport": "remote",
                    "controller_url": "http://must-be-ignored.invalid",
                    "host_id": "console-host",
                    "site_id": "auto",
                    "api_key": "cloud-secret",
                }
            ],
        }
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["transport"] == "remote"
    assert row["controller_url"] == core.REMOTE_API_BASE
    assert row["host_id"] == "console-host"
    assert row["site_id"] == "auto"
    assert row["verify_ssl"] is True
    assert row["allow_insecure_http"] is False

    from controller_config import runtime_config
    cfg = runtime_config(global_cfg(), row)
    assert cfg["transport"] == "remote"
    assert cfg["host_id"] == "console-host"
    assert cfg["controller_url"] == core.REMOTE_API_BASE
    assert cfg["api_key"] == "cloud-secret"


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


def test_single_priority_profiles_keep_local_and_remote_credentials_separate() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_controller_url": "https://192.0.2.10:11443",
            "local_site_id": "auto",
            "local_api_key": "local-secret",
            "local_verify_ssl": "false",
            "remote_host_id": "console-1",
            "remote_site_id": "auto",
            "remote_api_key": "remote-secret",
        }
    )
    assert priority == "local"
    assert fallback == "remote"
    assert [row["transport"] for row in profiles] == ["local", "remote"]
    assert profiles[0]["controller_url"] == "https://192.0.2.10:11443"
    assert profiles[0]["api_key"] == "local-secret"
    assert profiles[1]["controller_url"] == core.REMOTE_API_BASE
    assert profiles[1]["host_id"] == "console-1"
    assert profiles[1]["api_key"] == "remote-secret"


def test_fresh_local_profile_defaults_tls_verification_off() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_controller_url": "https://192.0.2.10:11443",
            "local_site_id": "auto",
            "local_api_key": "local-secret",
        }
    )
    assert priority == "local"
    assert fallback == "remote"
    assert len(profiles) == 1
    assert profiles[0]["transport"] == "local"
    assert profiles[0]["verify_ssl"] is False


def test_legacy_local_profile_beats_injected_new_defaults() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "transport": "local",
            "controller_url": "https://10.20.30.40:11443",
            "site_id": "legacy-site",
            "api_key": "legacy-local-key",
            "verify_ssl": "false",
            "allow_insecure_http": "true",
            # These values match the new 4.x schema defaults and may be injected
            # by Home Assistant during an upgrade even though the user never
            # intentionally configured the new Local profile fields.
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_controller_url": "https://192.168.1.1:11443",
            "local_site_id": "auto",
            "local_api_key": "",
            "local_verify_ssl": "true",
            "local_allow_insecure_http": "false",
            "remote_host_id": "auto",
            "remote_site_id": "auto",
            "remote_api_key": "",
        }
    )
    assert priority == "local"
    assert fallback == "remote"
    assert len(profiles) == 1
    local = profiles[0]
    assert local["transport"] == "local"
    assert local["controller_url"] == "https://10.20.30.40:11443"
    assert local["site_id"] == "legacy-site"
    assert local["api_key"] == "legacy-local-key"
    assert local["verify_ssl"] is False
    assert local["allow_insecure_http"] is True


def test_legacy_local_profile_beats_current_injected_defaults() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "transport": "local",
            "controller_url": "https://10.20.30.40:11443",
            "site_id": "legacy-site",
            "api_key": "legacy-local-key",
            "verify_ssl": "true",
            "allow_insecure_http": "false",
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_controller_url": "https://192.168.1.1:11443",
            "local_site_id": "auto",
            "local_api_key": "",
            "local_verify_ssl": "false",
            "local_allow_insecure_http": "false",
            "remote_host_id": "auto",
            "remote_site_id": "auto",
            "remote_api_key": "",
        }
    )
    assert priority == "local"
    assert fallback == "remote"
    assert len(profiles) == 1
    local = profiles[0]
    assert local["controller_url"] == "https://10.20.30.40:11443"
    assert local["site_id"] == "legacy-site"
    assert local["verify_ssl"] is True
    assert local["api_key"] == "legacy-local-key"


def test_explicit_new_local_profile_wins_as_a_whole() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "transport": "local",
            "controller_url": "https://10.20.30.40:11443",
            "site_id": "legacy-site",
            "api_key": "legacy-local-key",
            "verify_ssl": "false",
            "allow_insecure_http": "true",
            "priority_transport": "local",
            "fallback_transport": "remote",
            # One non-default field proves that this is an intentional new
            # profile, so the remaining new-profile defaults must not be
            # back-filled piecemeal from legacy state.
            "local_controller_url": "https://10.99.0.5:11443",
            "local_site_id": "auto",
            "local_api_key": "",
            "local_verify_ssl": "true",
            "local_allow_insecure_http": "false",
            "remote_host_id": "auto",
            "remote_site_id": "auto",
            "remote_api_key": "",
        }
    )
    assert priority == "local"
    assert fallback == "remote"
    assert len(profiles) == 1
    local = profiles[0]
    assert local["controller_url"] == "https://10.99.0.5:11443"
    assert local["site_id"] == "auto"
    assert local["verify_ssl"] is True
    assert local["allow_insecure_http"] is False
    assert local["api_key"] == "legacy-local-key"


def test_legacy_remote_profile_beats_injected_new_defaults() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "transport": "remote",
            "host_id": "legacy-console",
            "site_id": "legacy-remote-site",
            "api_key": "legacy-remote-key",
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_api_key": "",
            "remote_host_id": "auto",
            "remote_site_id": "auto",
            "remote_api_key": "",
        }
    )
    assert priority == "remote"
    assert fallback == "none"
    assert len(profiles) == 1
    remote = profiles[0]
    assert remote["transport"] == "remote"
    assert remote["host_id"] == "legacy-console"
    assert remote["site_id"] == "legacy-remote-site"
    assert remote["api_key"] == "legacy-remote-key"


def test_single_configured_profile_becomes_effective_priority() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "transport": "local",
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_api_key": "",
            "remote_api_key": "fixture-value",
            "remote_host_id": "fixture-host",
            "remote_site_id": "auto",
        }
    )
    assert [row["transport"] for row in profiles] == ["remote"], profiles
    assert priority == "remote", priority
    assert fallback == "none", fallback


def test_single_priority_fails_over_and_returns_to_priority() -> None:
    profiles, priority, fallback = parse_single_connection_profiles(
        {
            "priority_transport": "local",
            "fallback_transport": "remote",
            "local_controller_url": "https://192.0.2.10:11443",
            "local_api_key": "local-secret",
            "remote_api_key": "remote-secret",
            "remote_host_id": "auto",
        }
    )
    publisher = FakePublisher()
    attempts: list[str] = []
    local_available = False

    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "devices.json"

        def fake_poller(cfg: dict, _snapshot: Path, _publisher: FakePublisher) -> None:
            nonlocal local_available
            transport = str(cfg["transport"])
            attempts.append(transport)
            if transport == "local" and not local_available:
                raise core.UniFiDiagnosticError(
                    "tls_verification_failed",
                    "local unavailable",
                )
            core.write_snapshot(_snapshot, [sample_device("Switch")], 0)
            core.write_diagnostics(
                _snapshot,
                status="success",
                stage="complete",
                devices=[],
            )

        active = poll_single_with_failover(
            global_cfg(),
            profiles,
            snapshot,
            publisher,
            priority=priority,
            fallback=fallback,
            poller=fake_poller,
        )
        assert active == "remote"
        assert attempts == ["local", "remote"]
        diagnostics = json.loads(
            (snapshot.parent / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["active_transport"] == "remote"
        assert diagnostics["failover_active"] is True
        assert diagnostics["connection_results"] == [
            {
                "transport": "local",
                "status": "error",
                "error_type": "tls_verification_failed",
            },
            {"transport": "remote", "status": "success"},
        ]
        assert "local-secret" not in json.dumps(diagnostics)
        assert "remote-secret" not in json.dumps(diagnostics)

        attempts.clear()
        local_available = True
        active = poll_single_with_failover(
            global_cfg(),
            profiles,
            snapshot,
            publisher,
            priority=priority,
            fallback=fallback,
            poller=fake_poller,
        )
        assert active == "local"
        assert attempts == ["local"]
        diagnostics = json.loads(
            (snapshot.parent / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["active_transport"] == "local"
        assert diagnostics["failover_active"] is False
        assert diagnostics["connection_results"] == [
            {"transport": "local", "status": "success"},
        ]

        def fail_both(cfg: dict, _snapshot: Path, _publisher: FakePublisher) -> None:
            transport = str(cfg["transport"])
            if transport == "local":
                raise core.UniFiDiagnosticError(
                    "tls_verification_failed",
                    "local unavailable",
                )
            raise core.UniFiDiagnosticError(
                "authentication_or_authorization_failed",
                "remote key rejected",
            )

        try:
            poll_single_with_failover(
                global_cfg(),
                profiles,
                snapshot,
                publisher,
                priority=priority,
                fallback=fallback,
                poller=fail_both,
            )
        except RuntimeError as exc:
            assert "All configured UniFi connection paths failed" in str(exc)
        else:
            raise AssertionError("all-path failure did not fail the poll")

        diagnostics = json.loads(
            (snapshot.parent / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["active_transport"] == "none"
        assert diagnostics["error_type"] == "authentication_or_authorization_failed"
        assert diagnostics["connection_results"] == [
            {
                "transport": "local",
                "status": "error",
                "error_type": "tls_verification_failed",
            },
            {
                "transport": "remote",
                "status": "error",
                "error_type": "authentication_or_authorization_failed",
            },
        ]
        serialized = json.dumps(diagnostics)
        assert "local-secret" not in serialized
        assert "remote-secret" not in serialized


def main() -> int:
    test_controller_config_validation()
    test_remote_controller_entry_uses_site_manager_connector_transport()
    test_namespaced_publisher_identity()
    test_two_controllers_with_same_device_id_do_not_collide()
    test_failed_controller_preserves_snapshot_and_marks_it_offline()
    test_removed_controller_is_retired_without_touching_current_controller()
    test_removed_controller_state_is_preserved_if_retirement_fails()
    test_unsafe_stored_controller_namespace_fails_closed()
    test_single_priority_profiles_keep_local_and_remote_credentials_separate()
    test_fresh_local_profile_defaults_tls_verification_off()
    test_legacy_local_profile_beats_injected_new_defaults()
    test_legacy_local_profile_beats_current_injected_defaults()
    test_explicit_new_local_profile_wins_as_a_whole()
    test_legacy_remote_profile_beats_injected_new_defaults()
    test_single_configured_profile_becomes_effective_priority()
    test_single_priority_fails_over_and_returns_to_priority()
    print("multi-controller and priority/fallback regression tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
