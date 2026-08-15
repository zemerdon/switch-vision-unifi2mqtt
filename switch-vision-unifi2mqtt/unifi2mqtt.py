#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import logging
import os
import re
import signal
import ssl
import stat
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt

VERSION = "2.0.43"
STOP = False
EMPTY_SWITCH_CONFIRM_POLLS = 3
MAX_API_RESPONSE_BYTES = 4 * 1024 * 1024


def handle_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def validate_controller_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text or _has_control_chars(text):
        raise RuntimeError("controller_url is empty or contains control characters")
    parsed = urlsplit(text)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise RuntimeError("controller_url must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise RuntimeError("controller_url must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise RuntimeError("controller_url must not contain a query string or fragment")
    if parsed.path not in {"", "/"}:
        raise RuntimeError("controller_url must identify the controller origin without an extra path")
    if parsed.scheme == "http":
        logging.warning("UniFi controller uses plaintext HTTP; the API key is not protected in transit.")
    return text


def validate_topic_prefix(name: str, value: Any) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        raise RuntimeError(f"{name} must not be empty")
    if len(text) > 200 or _has_control_chars(text) or "#" in text or "+" in text:
        raise RuntimeError(f"{name} contains an invalid MQTT topic prefix")
    if any(not part for part in text.split("/")):
        raise RuntimeError(f"{name} contains an empty MQTT topic level")
    return text


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_") or "unifi_switch"


def make_mqtt_client() -> Any:
    """Create a Paho client that works with both 1.x and 2.x packages."""
    callback_api = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api is not None:
        # No callbacks are registered by this bridge. VERSION1 keeps the
        # constructor compatible with the callback contract used by Paho 1.x.
        return mqtt.Client(callback_api.VERSION1)
    return mqtt.Client()


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read app configuration: {exc}") from exc

    # run.sh resolves Home Assistant's MQTT service and exports the effective
    # broker values. Explicit custom brokers are exported unchanged.
    env_overrides = {
        "SV_MQTT_HOST": "mqtt_host",
        "SV_MQTT_PORT": "mqtt_port",
        "SV_MQTT_USERNAME": "mqtt_username",
        "SV_MQTT_PASSWORD": "mqtt_password",
    }
    for env_name, key in env_overrides.items():
        if env_name in os.environ:
            data[key] = os.environ[env_name]

    required = ["controller_url", "site_id", "api_key"]
    missing = [
        key
        for key in required
        if data.get(key) is None or not str(data.get(key, "")).strip()
    ]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))

    if data.get("mqtt_host") is None or not str(data.get("mqtt_host", "")).strip():
        raise RuntimeError("Missing required configuration: mqtt_host")

    data["controller_url"] = validate_controller_url(data["controller_url"])
    data["site_id"] = str(data["site_id"]).strip()
    data["api_key"] = str(data["api_key"]).strip()
    if _has_control_chars(data["site_id"]) or len(data["site_id"]) > 256:
        raise RuntimeError("site_id contains invalid characters or is too long")
    if _has_control_chars(data["api_key"]) or len(data["api_key"]) > 4096:
        raise RuntimeError("api_key contains invalid characters or is too long")
    data["mqtt_host"] = str(data["mqtt_host"]).strip()
    if _has_control_chars(data["mqtt_host"]):
        raise RuntimeError("mqtt_host contains control characters")
    try:
        mqtt_port = int(data.get("mqtt_port", 1883))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("mqtt_port must be an integer") from exc
    if not 1 <= mqtt_port <= 65535:
        raise RuntimeError("mqtt_port must be between 1 and 65535")
    try:
        poll_interval = int(data.get("poll_interval", 30))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("poll_interval must be an integer") from exc
    if not 10 <= poll_interval <= 300:
        raise RuntimeError("poll_interval must be between 10 and 300 seconds")
    data["mqtt_topic_prefix"] = validate_topic_prefix(
        "mqtt_topic_prefix", data.get("mqtt_topic_prefix", "switch_vision/unifi")
    )
    data["mqtt_discovery_prefix"] = validate_topic_prefix(
        "mqtt_discovery_prefix", data.get("mqtt_discovery_prefix", "homeassistant")
    )
    return data

class UniFiClient:
    def __init__(self, base_url: str, site_id: str, api_key: str, verify_ssl: bool) -> None:
        self.base = validate_controller_url(base_url)
        self.site_id = site_id.strip()
        self.api_key = api_key.strip()
        self.context = ssl.create_default_context()
        if not verify_ssl:
            if self.base.startswith("https://"):
                logging.warning("UniFi TLS certificate verification is disabled by configuration.")
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE

    def _get(self, path: str) -> Any:
        req = Request(
            self.base + path,
            headers={"Accept": "application/json", "X-API-KEY": self.api_key, "User-Agent": f"Switch-Vision-UniFi2MQTT/{VERSION}"},
            method="GET",
        )
        try:
            with urlopen(req, context=self.context, timeout=15) as response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
                if len(raw) > MAX_API_RESPONSE_BYTES:
                    raise RuntimeError("UniFi API response exceeded the 4 MiB safety limit")
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("UniFi API returned invalid JSON") from exc
        except HTTPError as exc:
            # Do not copy controller response bodies into logs; they may contain
            # operational or credential-adjacent information.
            raise RuntimeError(f"UniFi API HTTP {exc.code}: request failed") from exc
        except URLError as exc:
            raise RuntimeError(f"UniFi API connection failed: {exc.reason}") from exc

    def list_devices(self) -> list[dict[str, Any]]:
        payload = self._get(f"/proxy/network/integration/v1/sites/{quote(self.site_id, safe='')}/devices")
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("data", "devices"):
                if isinstance(payload.get(key), list):
                    return [x for x in payload[key] if isinstance(x, dict)]
        raise RuntimeError("Unexpected List Adopted Devices response")

    def detail(self, device_id: str) -> dict[str, Any]:
        payload = self._get(f"/proxy/network/integration/v1/sites/{quote(self.site_id, safe='')}/devices/{quote(device_id, safe='')}")
        return payload if isinstance(payload, dict) else {}

    def stats(self, device_id: str) -> dict[str, Any]:
        payload = self._get(f"/proxy/network/integration/v1/sites/{quote(self.site_id, safe='')}/devices/{quote(device_id, safe='')}/statistics/latest")
        return payload if isinstance(payload, dict) else {}


NON_SWITCH_MODEL_PREFIXES = (
    "UPS ",
    "UPS-",
    "PDU ",
    "PDU-",
    "USP ",
    "USP-",
    "RPS ",
    "RPS-",
    "POWER BACKUP",
)

KNOWN_GATEWAY_SWITCH_MODELS = {
    "UDM PRO",
    "UDM SE",
    "UDM PRO SE",
}


def canonical_unifi_model(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def switch_classification(
    device: dict[str, Any],
) -> tuple[bool, str]:
    model = str(
        device.get("model", "") or ""
    ).strip().upper()

    if model.startswith(
        NON_SWITCH_MODEL_PREFIXES
    ):
        return False, "managed_power_device"

    model_key = canonical_unifi_model(model)

    if model_key in KNOWN_GATEWAY_SWITCH_MODELS:
        return True, "known_gateway_switch_hybrid"

    if model.startswith(
        ("USW ", "USW-", "US ", "US-")
    ):
        return True, "unifi_switch_model"

    features = device.get("features")

    if isinstance(features, list) and features:
        names = {
            str(x).strip().lower()
            for x in features
        }
        if "switching" in names:
            return True, "switching_feature"
        return False, "no_switching_feature"

    if isinstance(features, dict) and features:
        if "switching" not in features:
            return False, "no_switching_feature"

        switching = features.get("switching")

        if (
            switching is not None
            and switching is not False
        ):
            return True, "switching_feature"

        return False, "switching_feature_disabled"

    return False, "unsupported_model"


def is_switch(device: dict[str, Any]) -> bool:
    accepted, _reason = switch_classification(
        device
    )
    return accepted


def diagnostic_feature_names(
    device: dict[str, Any],
) -> list[str]:
    features = device.get("features")

    if isinstance(features, list):
        values = features
    elif isinstance(features, dict):
        values = list(features)
    else:
        values = []

    safe: set[str] = set()

    for value in values:
        text = str(value or "").strip()

        if (
            text
            and len(text) <= 64
            and re.fullmatch(
                r"[A-Za-z0-9_.:+-]+",
                text,
            )
        ):
            safe.add(text)

    return sorted(
        safe,
        key=str.casefold,
    )


def diagnostic_device_classification(
    device: dict[str, Any],
) -> dict[str, Any]:
    model = str(
        device.get("model", "") or "Unknown"
    ).strip()

    if (
        not model
        or len(model) > 128
        or _has_control_chars(model)
    ):
        model = "Unknown"

    accepted, reason = switch_classification(
        device
    )

    return {
        "model": model,
        "features": diagnostic_feature_names(
            device
        ),
        "accepted": accepted,
        "reason": reason,
    }


def extract_ports(detail: dict[str, Any]) -> list[dict[str, Any]]:
    interfaces = detail.get("interfaces")
    if not isinstance(interfaces, dict) or not isinstance(interfaces.get("ports"), list):
        return []
    out = []
    for raw in interfaces["ports"]:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        poe = raw.get("poe") if isinstance(raw.get("poe"), dict) else {}
        state = str(raw.get("state", "UNKNOWN")).upper()
        # Some UniFi gateway ports report a nominal speed even while DOWN.
        # Treat speedMbps as negotiated speed only when the Ethernet link is UP.
        negotiated_speed = raw.get("speedMbps") if state == "UP" else None
        out.append({
            "idx": idx,
            "state": state,
            "connector": str(raw.get("connector", "UNKNOWN")).upper(),
            "max_speed_mbps": raw.get("maxSpeedMbps"),
            "speed_mbps": negotiated_speed,
            "poe": {
                "available": bool(poe),
                "standard": poe.get("standard"),
                "type": poe.get("type"),
                "enabled": poe.get("enabled"),
                "state": str(poe.get("state", "UNKNOWN")).upper() if poe else None,
            },
        })
    return sorted(out, key=lambda x: x["idx"])


def normalize_device(summary: dict[str, Any], detail: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    source = dict(summary)
    source.update({k: v for k, v in detail.items() if v is not None})
    uplink = stats.get("uplink") if isinstance(stats.get("uplink"), dict) else {}
    ports = extract_ports(detail)
    return {
        "id": str(source.get("id", "")),
        "name": str(source.get("name") or source.get("model") or "UniFi Switch"),
        "model": str(source.get("model", "Unknown")),
        "firmware": str(source.get("firmwareVersion", "")),
        "state": str(source.get("state", "UNKNOWN")).upper(),
        "ports": ports,
        "system": {
            "uptime_sec": stats.get("uptimeSec"),
            "cpu_utilization_pct": stats.get("cpuUtilizationPct"),
            "memory_utilization_pct": stats.get("memoryUtilizationPct"),
            "uplink_tx_rate_bps": uplink.get("txRateBps"),
            "uplink_rx_rate_bps": uplink.get("rxRateBps"),
        },
        "api_capabilities": {
            "port_detail": bool(ports),
            "per_port_traffic": bool(stats.get("interfaces")),
        },
    }


def retained_topics_for_device(
    d: dict[str, Any],
    topic_prefix: str,
    discovery_prefix: str,
) -> set[str]:
    # Return every retained state/discovery topic owned by a normalized device.
    did = slug(d.get("id") or d.get("name"))
    base = f"{topic_prefix.strip('/')}/{did}"
    discovery_base = discovery_prefix.strip("/")
    topics: set[str] = {f"{base}/available"}

    def sensor(key: str, value: Any) -> None:
        if value is None:
            return
        uid = f"switch_vision_unifi_{did}_{slug(key)}"
        topics.add(f"{base}/{key}")
        topics.add(f"{discovery_base}/sensor/{uid}/config")

    def binary(key: str) -> None:
        uid = f"switch_vision_unifi_{did}_{slug(key)}"
        topics.add(f"{base}/{key}")
        topics.add(f"{discovery_base}/binary_sensor/{uid}/config")

    sensor("model", d.get("model"))
    sensor("firmware", d.get("firmware"))
    binary("online")

    sysd = d.get("system") if isinstance(d.get("system"), dict) else {}
    sensor("cpu", sysd.get("cpu_utilization_pct"))
    sensor("memory", sysd.get("memory_utilization_pct"))
    sensor("uptime", sysd.get("uptime_sec"))
    sensor("uplink_rx_rate", sysd.get("uplink_rx_rate_bps"))
    sensor("uplink_tx_rate", sysd.get("uplink_tx_rate_bps"))

    ports = d.get("ports") if isinstance(d.get("ports"), list) else []
    for p in ports:
        if not isinstance(p, dict) or p.get("idx") is None:
            continue
        n = p["idx"]
        prefix = f"port/{n}"
        binary(f"{prefix}/status")
        sensor(f"{prefix}/speed", p.get("speed_mbps"))
        sensor(f"{prefix}/max_speed", p.get("max_speed_mbps"))
        sensor(f"{prefix}/connector", p.get("connector"))
        poe = p.get("poe") if isinstance(p.get("poe"), dict) else {}
        if poe.get("available"):
            binary(f"{prefix}/poe_enabled")
            binary(f"{prefix}/poe_active")
            sensor(f"{prefix}/poe_standard", poe.get("standard"))

    return topics


class Publisher:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.topic_prefix = validate_topic_prefix(
            "mqtt_topic_prefix", cfg.get("mqtt_topic_prefix", "switch_vision/unifi")
        )
        self.discovery_prefix = validate_topic_prefix(
            "mqtt_discovery_prefix", cfg.get("mqtt_discovery_prefix", "homeassistant")
        )
        self.client = make_mqtt_client()
        self._pending: list[Any] = []
        user = str(cfg.get("mqtt_username", "") or "")
        if user:
            self.client.username_pw_set(user, str(cfg.get("mqtt_password", "") or ""))
        self.client.connect(str(cfg["mqtt_host"]), int(cfg.get("mqtt_port", 1883)), 60)
        self.client.loop_start()

    def _queue(self, topic: str, payload: str) -> None:
        info = self.client.publish(topic, payload, qos=0, retain=True)
        if info is not None:
            self._pending.append(info)

    def _flush_pending(self, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        for info in self._pending:
            is_published = getattr(info, "is_published", None)
            if not callable(is_published):
                continue
            while time.monotonic() < deadline:
                try:
                    if is_published():
                        break
                except Exception:
                    break
                time.sleep(0.01)
        self._pending.clear()

    def close(self) -> None:
        try:
            self._flush_pending()
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    def publish(self, topic: str, value: Any) -> None:
        if value is not None:
            self._queue(topic, str(value))

    def discovery(self, component: str, uid: str, payload: dict[str, Any]) -> None:
        self._queue(
            f"{self.discovery_prefix}/{component}/{uid}/config",
            json.dumps(payload, separators=(",", ":")),
        )

    def publish_availability(self, d: dict[str, Any], status: str) -> None:
        did = slug(d.get("id") or d.get("name"))
        self.publish(f"{self.topic_prefix}/{did}/available", status)

    def cleanup_retired_topics(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> set[str]:
        old_topics = retained_topics_for_device(previous, self.topic_prefix, self.discovery_prefix)
        new_topics = retained_topics_for_device(current, self.topic_prefix, self.discovery_prefix)
        stale = old_topics - new_topics
        for topic in sorted(stale):
            self._queue(topic, "")
        return stale

    def remove_device(self, d: dict[str, Any]) -> set[str]:
        topics = retained_topics_for_device(d, self.topic_prefix, self.discovery_prefix)
        for topic in sorted(topics):
            self._queue(topic, "")
        return topics

    def publish_device(self, d: dict[str, Any]) -> set[str]:
        did = slug(d.get("id") or d.get("name"))
        base = f"{self.topic_prefix}/{did}"
        device = {
            "identifiers": [f"switch_vision_unifi_{did}"],
            "name": d["name"],
            "manufacturer": "Ubiquiti",
            "model": d["model"],
            "sw_version": d.get("firmware") or None,
        }
        self.publish(f"{base}/available", "online")

        def sensor(key, name, value, unit=None):
            if value is None:
                return
            topic = f"{base}/{key}"
            uid = f"switch_vision_unifi_{did}_{slug(key)}"
            payload = {"name": name, "unique_id": uid, "state_topic": topic, "device": device,
                       "availability_topic": f"{base}/available", "payload_available": "online", "payload_not_available": "offline"}
            if unit:
                payload["unit_of_measurement"] = unit
            self.discovery("sensor", uid, payload)
            self.publish(topic, value)

        def binary(key, name, on):
            topic = f"{base}/{key}"
            uid = f"switch_vision_unifi_{did}_{slug(key)}"
            payload = {"name": name, "unique_id": uid, "state_topic": topic, "payload_on": "ON", "payload_off": "OFF",
                       "device": device, "availability_topic": f"{base}/available",
                       "payload_available": "online", "payload_not_available": "offline"}
            self.discovery("binary_sensor", uid, payload)
            self.publish(topic, "ON" if on else "OFF")

        sensor("model", "Model", d["model"])
        sensor("firmware", "Firmware", d.get("firmware"))
        binary("online", "Online", d.get("state") == "ONLINE")

        sysd = d["system"]
        sensor("cpu", "CPU", sysd.get("cpu_utilization_pct"), "%")
        sensor("memory", "Memory", sysd.get("memory_utilization_pct"), "%")
        sensor("uptime", "Uptime", sysd.get("uptime_sec"), "s")
        sensor("uplink_rx_rate", "Uplink RX Rate", sysd.get("uplink_rx_rate_bps"), "bit/s")
        sensor("uplink_tx_rate", "Uplink TX Rate", sysd.get("uplink_tx_rate_bps"), "bit/s")

        for p in d["ports"]:
            n = p["idx"]
            prefix = f"port/{n}"
            binary(f"{prefix}/status", f"Port {n} Status", p["state"] == "UP")
            sensor(f"{prefix}/speed", f"Port {n} Speed", p.get("speed_mbps"), "Mbit/s")
            sensor(f"{prefix}/max_speed", f"Port {n} Max Speed", p.get("max_speed_mbps"), "Mbit/s")
            sensor(f"{prefix}/connector", f"Port {n} Connector", p.get("connector"))
            poe = p.get("poe", {})
            if poe.get("available"):
                binary(f"{prefix}/poe_enabled", f"Port {n} PoE Enabled", bool(poe.get("enabled")))
                binary(f"{prefix}/poe_active", f"Port {n} PoE Active", poe.get("state") == "UP")
                sensor(f"{prefix}/poe_standard", f"Port {n} PoE Standard", poe.get("standard"))

        return retained_topics_for_device(d, self.topic_prefix, self.discovery_prefix)



def _verify_permissions(path: Path, expected: int, label: str) -> None:
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != expected:
        raise RuntimeError(
            f"{label} permissions are {actual:04o}; expected {expected:04o}"
        )


def secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink state directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    _verify_permissions(path, 0o700, "UniFi state directory")


def write_snapshot(snapshot: Path, devices: list[dict[str, Any]], empty_switch_polls: int = 0) -> None:
    secure_directory(snapshot.parent)
    if snapshot.is_symlink():
        raise RuntimeError(f"Refusing symlink snapshot path: {snapshot}")
    payload = {
        "schema_version": 1,
        "product": "Switch Vision UniFi2MQTT",
        "version": VERSION,
        "generated_at": int(time.time()),
        "empty_switch_polls": max(0, int(empty_switch_polls)),
        "devices": devices,
    }
    temp = snapshot.parent / f".{snapshot.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(temp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, indent=2)
            handle.write(chr(10))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        _verify_permissions(temp, 0o600, "Temporary UniFi snapshot")
        os.replace(temp, snapshot)
        os.chmod(snapshot, 0o600)
        _verify_permissions(snapshot, 0o600, "UniFi snapshot")
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def diagnostics_path_for_snapshot(
    snapshot: Path,
) -> Path:
    return snapshot.parent / "diagnostics.json"


def write_diagnostics(
    snapshot: Path,
    *,
    status: str,
    stage: str,
    devices: list[dict[str, Any]] | None = None,
    empty_switch_polls: int = 0,
    error_type: str | None = None,
) -> None:
    path = diagnostics_path_for_snapshot(
        snapshot
    )

    secure_directory(path.parent)

    if path.is_symlink():
        raise RuntimeError(
            "Refusing symlink diagnostics path: "
            f"{path}"
        )

    source_devices = (
        devices
        if isinstance(devices, list)
        else []
    )

    classifications = [
        diagnostic_device_classification(d)
        for d in source_devices
        if isinstance(d, dict)
    ]

    accepted = sum(
        1
        for item in classifications
        if item["accepted"]
    )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "product":
            "Switch Vision UniFi2MQTT",
        "version": VERSION,
        "generated_at": int(time.time()),
        "status": str(status),
        "stage": str(stage),
        "adopted_devices":
            len(classifications),
        "switching_devices":
            accepted,
        "rejected_devices":
            len(classifications) - accepted,
        "empty_switch_polls":
            max(
                0,
                int(empty_switch_polls),
            ),
        "device_classification":
            classifications,
    }

    if error_type:
        payload["error_type"] = str(
            error_type
        )[:128]

    temp = path.parent / (
        f".{path.name}.{os.getpid()}."
        f"{time.monotonic_ns()}.tmp"
    )

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
    )

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = None

    try:
        fd = os.open(
            temp,
            flags,
            0o600,
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            fd = None
            json.dump(
                payload,
                handle,
                indent=2,
            )
            handle.write(chr(10))
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.chmod(temp, 0o600)

        _verify_permissions(
            temp,
            0o600,
            "Temporary UniFi diagnostics",
        )

        os.replace(
            temp,
            path,
        )

        os.chmod(path, 0o600)

        _verify_permissions(
            path,
            0o600,
            "UniFi diagnostics",
        )

    finally:
        if fd is not None:
            os.close(fd)

        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


@contextmanager
def snapshot_operation_lock(snapshot: Path):
    secure_directory(snapshot.parent)
    lock_path = snapshot.parent / f".{snapshot.name}.lock"
    if lock_path.is_symlink():
        raise RuntimeError(f"Refusing symlink snapshot lock: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        _verify_permissions(lock_path, 0o600, "UniFi snapshot lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another UniFi2MQTT process is already updating this snapshot"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _poll_once_unlocked(cfg: dict[str, Any], snapshot: Path) -> None:
    api = UniFiClient(str(cfg["controller_url"]), str(cfg["site_id"]), str(cfg["api_key"]), truthy(cfg.get("verify_ssl", True)))

    try:
        pub = Publisher(cfg)
    except Exception as exc:
        write_diagnostics(
            snapshot,
            status="error",
            stage="mqtt_connect",
            error_type=type(exc).__name__,
        )
        raise

    normalized = []
    previous_by_id: dict[str, dict[str, Any]] = {}
    previous_empty_switch_polls = 0
    try:
        if snapshot.is_file():
            try:
                previous = json.loads(snapshot.read_text(encoding="utf-8"))
                previous_devices = previous.get("devices", []) if isinstance(previous, dict) else []
                if isinstance(previous, dict):
                    try:
                        previous_empty_switch_polls = max(
                            0, int(previous.get("empty_switch_polls", 0))
                        )
                    except (TypeError, ValueError):
                        previous_empty_switch_polls = 0
                for item in previous_devices:
                    if isinstance(item, dict):
                        did = str(item.get("id", "")).strip()
                        if did:
                            previous_by_id[did] = item
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                logging.warning("Existing UniFi snapshot could not be read; continuing with a fresh snapshot.")

        try:
            devices = api.list_devices()
        except Exception as exc:
            write_diagnostics(
                snapshot,
                status="error",
                stage="list_devices",
                error_type=type(exc).__name__,
            )
            raise

        switches = [
            d
            for d in devices
            if is_switch(d)
        ]

        write_diagnostics(
            snapshot,
            status="in_progress",
            stage="devices_classified",
            devices=devices,
        )
        switch_ids = {
            str(d.get("id", "")).strip()
            for d in switches
            if str(d.get("id", "")).strip()
        }
        logging.info("Found %d adopted devices; %d switching devices.", len(devices), len(switches))

        empty_switch_polls = 0
        if previous_by_id and not switch_ids:
            empty_switch_polls = previous_empty_switch_polls + 1
            if empty_switch_polls < EMPTY_SWITCH_CONFIRM_POLLS:
                normalized = list(previous_by_id.values())
                for previous_item in normalized:
                    pub.publish_availability(previous_item, "offline")
                logging.warning(
                    "UniFi returned no switching devices (%d/%d confirmation polls); "
                    "preserving %d previous device(s), marking them offline, and deferring retirement.",
                    empty_switch_polls,
                    EMPTY_SWITCH_CONFIRM_POLLS,
                    len(normalized),
                )
                write_snapshot(
                    snapshot,
                    normalized,
                    empty_switch_polls,
                )
                write_diagnostics(
                    snapshot,
                    status="success",
                    stage="complete",
                    devices=devices,
                    empty_switch_polls=
                        empty_switch_polls,
                )
                return
            logging.warning(
                "UniFi returned no switching devices for %d consecutive polls; "
                "whole-set retirement is now confirmed.",
                empty_switch_polls,
            )

        for summary in switches:
            did = str(summary.get("id", "")).strip()
            if not did:
                continue
            try:
                item = normalize_device(summary, api.detail(did), api.stats(did))
                normalized.append(item)
                pub.publish_device(item)
                previous_item = previous_by_id.get(did)
                if previous_item is not None:
                    stale = pub.cleanup_retired_topics(previous_item, item)
                    if stale:
                        logging.info("%s: cleared %d retired MQTT topic(s).", item["name"], len(stale))
                logging.info("%s (%s): %d API port(s)", item["name"], item["model"], len(item["ports"]))
            except Exception as exc:
                previous_item = previous_by_id.get(did)
                if previous_item is not None:
                    normalized.append(previous_item)
                    pub.publish_availability(previous_item, "offline")
                    logging.warning(
                        "Device %s refresh failed; preserving previous snapshot data and marking MQTT availability offline: %s",
                        summary.get("name") or did,
                        exc,
                    )
                else:
                    logging.warning("Device %s failed and has no previous snapshot data: %s", summary.get("name") or did, exc)

        for did, previous_item in previous_by_id.items():
            if did not in switch_ids:
                removed = pub.remove_device(previous_item)
                logging.info(
                    "Removed stale MQTT retained data for retired device %s (%d topic(s)).",
                    previous_item.get("name") or did,
                    len(removed),
                )

        write_snapshot(
            snapshot,
            normalized,
            0,
        )
        write_diagnostics(
            snapshot,
            status="success",
            stage="complete",
            devices=devices,
            empty_switch_polls=0,
        )
    finally:
        pub.close()


def poll_once(cfg: dict[str, Any], snapshot: Path) -> None:
    with snapshot_operation_lock(snapshot):
        _poll_once_unlocked(cfg, snapshot)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        logging.error("%s", exc)
        try:
            write_diagnostics(
                args.snapshot,
                status="error",
                stage="configuration",
                error_type=type(exc).__name__,
            )
        except Exception:
            logging.warning(
                "Could not persist UniFi "
                "configuration diagnostics."
            )
        return 2
    interval = max(10, min(300, int(cfg.get("poll_interval", 30))))
    while not STOP:
        started = time.monotonic()
        try:
            poll_once(cfg, args.snapshot)
        except Exception as exc:
            logging.error("Poll failed: %s", exc)
        deadline = time.monotonic() + max(1.0, interval - (time.monotonic() - started))
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    logging.info("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
