#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable

import unifi2mqtt as core
from controller_config import (
    load_multi_config,
    load_raw_options,
    multi_controller_enabled,
    runtime_config,
    scoped_device_id,
)

VERSION = core.VERSION
REGISTRY_NAME = "controller_state.json"
CONTROLLER_DIR = "controllers"
DEFAULT_STATE_ROOT = Path("/data/multi_controller_state")


class NamespacedPublisher(core.Publisher):
    """Reuse the existing publisher while making HA/MQTT device identities controller-safe."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.identity_namespace = ""
        super().__init__(cfg)

    def set_identity_namespace(self, namespace: str) -> None:
        text = core.slug(namespace)
        if not text:
            raise RuntimeError("controller identity namespace must not be empty")
        self.identity_namespace = text

    def _namespaced_device(self, device: dict[str, Any]) -> dict[str, Any]:
        if not self.identity_namespace:
            return dict(device)
        clone = dict(device)
        raw_id = str(clone.get("id") or clone.get("name") or "").strip()
        clone["id"] = scoped_device_id(self.identity_namespace, raw_id)
        return clone

    def publish_availability(self, d: dict[str, Any], status: str) -> None:
        super().publish_availability(self._namespaced_device(d), status)

    def cleanup_retired_topics(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> set[str]:
        return super().cleanup_retired_topics(
            self._namespaced_device(previous),
            self._namespaced_device(current),
        )

    def remove_device(self, d: dict[str, Any]) -> set[str]:
        return super().remove_device(self._namespaced_device(d))

    def publish_device(self, d: dict[str, Any]) -> set[str]:
        return super().publish_device(self._namespaced_device(d))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_devices(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    rows = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _secure_write_json(path: Path, payload: dict[str, Any]) -> None:
    core.secure_directory(path.parent)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink state path: {path}")

    temp = path.parent / f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = None
    try:
        fd = os.open(temp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        if stat.S_IMODE(temp.stat().st_mode) != 0o600:
            raise RuntimeError("Temporary multi-controller state permissions are invalid")
        os.replace(temp, path)
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise RuntimeError("Multi-controller state permissions are invalid")
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def _controller_snapshot(state_root: Path, namespace: str) -> Path:
    return state_root / CONTROLLER_DIR / namespace / "devices.json"


def _registry_path(state_root: Path) -> Path:
    return state_root / REGISTRY_NAME


def _read_registry(state_root: Path) -> list[str]:
    payload = _read_json(_registry_path(state_root))
    rows = payload.get("controllers") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [str(value) for value in rows if isinstance(value, str) and value]


def _write_registry(state_root: Path, namespaces: list[str]) -> None:
    _secure_write_json(
        _registry_path(state_root),
        {
            "schema_version": 1,
            "product": "Switch Vision UniFi2MQTT",
            "version": VERSION,
            "generated_at": int(time.time()),
            "controllers": list(namespaces),
        },
    )


def aggregate_device(namespace: str, device: dict[str, Any]) -> dict[str, Any]:
    clone = dict(device)
    source_id = str(clone.get("id") or "").strip()
    if not source_id:
        raise RuntimeError("Cannot aggregate a UniFi device without an id")
    clone["id"] = scoped_device_id(namespace, source_id)
    return clone


def _aggregate_current_snapshots(
    state_root: Path,
    controllers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in controllers:
        namespace = str(entry["namespace"])
        for device in _read_devices(_controller_snapshot(state_root, namespace)):
            item = aggregate_device(namespace, device)
            device_id = str(item["id"])
            if device_id in seen:
                raise RuntimeError("Duplicate aggregate UniFi device identity")
            seen.add(device_id)
            combined.append(item)

    return combined


def _write_multi_diagnostics(
    public_root: Path,
    *,
    controller_results: list[dict[str, Any]],
    devices: list[dict[str, Any]],
) -> None:
    successes = sum(1 for item in controller_results if item.get("status") == "success")
    failures = len(controller_results) - successes
    if controller_results and failures == 0:
        status = "success"
    elif successes:
        status = "partial"
    else:
        status = "error"

    safe_results = []
    for position, item in enumerate(controller_results, start=1):
        row = {
            "position": position,
            "status": str(item.get("status") or "error"),
            "stage": str(item.get("stage") or "poll"),
        }
        if item.get("error_type"):
            row["error_type"] = str(item["error_type"])[:128]
        safe_results.append(row)

    _secure_write_json(
        public_root / "diagnostics.json",
        {
            "schema_version": 1,
            "product": "Switch Vision UniFi2MQTT",
            "version": VERSION,
            "generated_at": int(time.time()),
            "status": status,
            "stage": "complete",
            "mode": "multi_controller",
            "controllers_configured": len(controller_results),
            "controllers_successful": successes,
            "controllers_failed": failures,
            "switching_devices": len(devices),
            "private_identifiers_included": False,
            "controller_results": safe_results,
        },
    )


def _mark_previous_offline(
    publisher: NamespacedPublisher,
    namespace: str,
    snapshot: Path,
) -> None:
    publisher.set_identity_namespace(namespace)
    for device in _read_devices(snapshot):
        publisher.publish_availability(device, "offline")
    publisher.flush()


def _cleanup_removed_controllers(
    state_root: Path,
    current_namespaces: set[str],
    publisher: NamespacedPublisher,
) -> int:
    removed_topics = 0
    for namespace in sorted(set(_read_registry(state_root)) - current_namespaces):
        publisher.set_identity_namespace(namespace)
        for device in _read_devices(_controller_snapshot(state_root, namespace)):
            removed_topics += len(publisher.remove_device(device))
        publisher.flush()
        logging.info(
            "Retired MQTT state for a controller removed from configuration (%d topic(s)).",
            removed_topics,
        )
    return removed_topics


def poll_multi_once(
    global_cfg: dict[str, Any],
    controllers: list[dict[str, Any]],
    snapshot: Path,
    state_root: Path,
    publisher: NamespacedPublisher,
    poller: Callable[[dict[str, Any], Path, Any], None] = core.poll_once,
) -> list[dict[str, Any]]:
    public_root = snapshot.parent
    core.secure_directory(public_root)
    core.secure_directory(state_root)
    core.secure_directory(state_root / CONTROLLER_DIR)

    namespaces = [str(entry["namespace"]) for entry in controllers]
    _cleanup_removed_controllers(state_root, set(namespaces), publisher)

    results: list[dict[str, Any]] = []
    for entry in controllers:
        namespace = str(entry["namespace"])
        controller_snapshot = _controller_snapshot(state_root, namespace)
        core.secure_directory(controller_snapshot.parent)
        publisher.set_identity_namespace(namespace)
        cfg = runtime_config(global_cfg, entry)

        try:
            poller(cfg, controller_snapshot, publisher)
            results.append({"status": "success", "stage": "complete"})
        except Exception as exc:
            logging.error("Controller poll failed: %s", exc)
            try:
                _mark_previous_offline(publisher, namespace, controller_snapshot)
            except Exception as offline_exc:
                logging.warning("Could not mark failed controller snapshot offline: %s", offline_exc)
            results.append(
                {
                    "status": "error",
                    "stage": "poll",
                    "error_type": type(exc).__name__,
                }
            )

    devices = _aggregate_current_snapshots(state_root, controllers)
    with core.snapshot_operation_lock(snapshot):
        core.write_snapshot(snapshot, devices, 0)
    _write_multi_diagnostics(public_root, controller_results=results, devices=devices)
    _write_registry(state_root, namespaces)
    return devices


def _exec_legacy(config: Path, snapshot: Path) -> None:
    script = Path(__file__).with_name("unifi2mqtt.py")
    os.execv(
        sys.executable,
        [
            sys.executable,
            str(script),
            "--config",
            str(config),
            "--snapshot",
            str(snapshot),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    args = parser.parse_args()

    try:
        raw = load_raw_options(args.config)
    except Exception:
        _exec_legacy(args.config, args.snapshot)
        return 0

    if not multi_controller_enabled(raw):
        _exec_legacy(args.config, args.snapshot)
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, core.handle_stop)
    signal.signal(signal.SIGINT, core.handle_stop)

    try:
        global_cfg, controllers = load_multi_config(args.config)
    except Exception as exc:
        logging.error("%s", exc)
        try:
            _write_multi_diagnostics(
                args.snapshot.parent,
                controller_results=[
                    {
                        "status": "error",
                        "stage": "configuration",
                        "error_type": type(exc).__name__,
                    }
                ],
                devices=[],
            )
        except Exception:
            logging.warning("Could not persist multi-controller configuration diagnostics.")
        return 2

    logging.info("Multi-controller mode enabled for %d controller/site entries.", len(controllers))

    try:
        publisher = NamespacedPublisher(global_cfg)
    except Exception as exc:
        logging.error("MQTT initialization failed: %s", exc)
        try:
            _write_multi_diagnostics(
                args.snapshot.parent,
                controller_results=[
                    {
                        "status": "error",
                        "stage": "mqtt_connect",
                        "error_type": type(exc).__name__,
                    }
                ],
                devices=[],
            )
        except Exception:
            logging.warning("Could not persist multi-controller MQTT diagnostics.")
        return 2

    interval = int(global_cfg.get("poll_interval", 30))
    try:
        while not core.STOP:
            started = time.monotonic()
            try:
                poll_multi_once(
                    global_cfg,
                    controllers,
                    args.snapshot,
                    args.state_root,
                    publisher,
                )
            except Exception as exc:
                logging.error("Multi-controller aggregation failed: %s", exc)

            deadline = time.monotonic() + max(
                1.0,
                interval - (time.monotonic() - started),
            )
            while not core.STOP and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    finally:
        publisher.close()

    logging.info("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
