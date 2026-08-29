#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import unifi2mqtt as core

CONTROLLER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_SCOPED_DEVICE_ID_LENGTH = 128


def load_raw_options(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read app configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("App configuration must be a JSON object")
    return data


def multi_controller_enabled(data: dict[str, Any]) -> bool:
    controllers = data.get("controllers")
    return isinstance(controllers, list) and bool(controllers)


def controller_namespace(controller_id: str) -> str:
    normalized = core.slug(controller_id)
    if not normalized:
        raise RuntimeError("controller id does not produce a usable namespace")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"c_{digest}"


def scoped_device_id(namespace: str, raw_device_id: str) -> str:
    namespace = str(namespace or "").strip()
    raw_device_id = str(raw_device_id or "").strip()
    if not namespace or not raw_device_id:
        raise RuntimeError("controller namespace and raw device id are required")
    candidate = f"{namespace}__{raw_device_id}"
    if len(candidate) <= MAX_SCOPED_DEVICE_ID_LENGTH:
        return candidate
    digest = hashlib.sha256(raw_device_id.encode("utf-8")).hexdigest()
    return f"{namespace}__h_{digest}"


def parse_controller_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = data.get("controllers")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RuntimeError("controllers must contain at least one controller entry")

    entries: list[dict[str, Any]] = []
    normalized_ids: set[str] = set()
    namespaces: set[str] = set()

    for position, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"controllers entry {position} must be an object")

        controller_id = str(raw.get("id") or "").strip()
        if not CONTROLLER_ID_RE.fullmatch(controller_id):
            raise RuntimeError(
                f"controllers entry {position} id must be 1-64 letters, digits, '-' or '_' "
                "and start with a letter or digit"
            )
        normalized_id = core.slug(controller_id)
        if normalized_id in normalized_ids:
            raise RuntimeError(
                f"controllers entry {position} id collides with another controller after normalization"
            )
        normalized_ids.add(normalized_id)

        namespace = controller_namespace(controller_id)
        if namespace in namespaces:
            raise RuntimeError(
                f"controllers entry {position} identity collides with another controller"
            )
        namespaces.add(namespace)

        allow_http = core.truthy(
            raw.get("allow_insecure_http", data.get("allow_insecure_http", False))
        )
        controller_url = core.validate_controller_url(
            raw.get("controller_url"),
            allow_http,
        )

        site_id = str(raw.get("site_id") or "auto").strip()
        api_key = str(raw.get("api_key") or "").strip()
        if core._has_control_chars(site_id) or len(site_id) > 256:
            raise RuntimeError(f"controllers entry {position} site_id is invalid or too long")
        if not api_key:
            raise RuntimeError(f"controllers entry {position} api_key is required")
        if core._has_control_chars(api_key) or len(api_key) > 4096:
            raise RuntimeError(f"controllers entry {position} api_key is invalid or too long")

        entries.append(
            {
                "id": controller_id,
                "namespace": namespace,
                "controller_url": controller_url,
                "site_id": site_id,
                "api_key": api_key,
                "verify_ssl": core.truthy(raw.get("verify_ssl", data.get("verify_ssl", True))),
                "allow_insecure_http": allow_http,
            }
        )

    return entries


def _mqtt_global_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(data)

    env_overrides = {
        "SV_MQTT_HOST": "mqtt_host",
        "SV_MQTT_PORT": "mqtt_port",
        "SV_MQTT_USERNAME": "mqtt_username",
        "SV_MQTT_PASSWORD": "mqtt_password",
    }
    for env_name, key in env_overrides.items():
        if env_name in os.environ:
            cfg[key] = os.environ[env_name]

    mqtt_host = str(cfg.get("mqtt_host") or "").strip()
    if not mqtt_host:
        raise RuntimeError("Missing required configuration: mqtt_host")
    if core._has_control_chars(mqtt_host):
        raise RuntimeError("mqtt_host contains control characters")
    cfg["mqtt_host"] = mqtt_host

    try:
        mqtt_port = int(cfg.get("mqtt_port", 1883))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("mqtt_port must be an integer") from exc
    if not 1 <= mqtt_port <= 65535:
        raise RuntimeError("mqtt_port must be between 1 and 65535")
    cfg["mqtt_port"] = mqtt_port

    cfg["mqtt_tls"] = core.truthy(cfg.get("mqtt_tls", False))
    cfg["mqtt_verify_ssl"] = core.truthy(cfg.get("mqtt_verify_ssl", True))

    mqtt_ca = str(cfg.get("mqtt_ca", "") or "").strip()
    if core._has_control_chars(mqtt_ca):
        raise RuntimeError("mqtt_ca contains control characters")
    if mqtt_ca:
        ca_path = Path(mqtt_ca)
        try:
            resolved_ca = ca_path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"mqtt_ca could not be resolved: {exc}") from exc
        ssl_root = Path("/ssl").resolve()
        if resolved_ca == ssl_root or ssl_root not in resolved_ca.parents:
            raise RuntimeError("mqtt_ca must reference a file below /ssl")
        if not resolved_ca.is_file():
            raise RuntimeError("mqtt_ca must reference a regular file")
        cfg["mqtt_ca"] = str(resolved_ca)
    else:
        cfg["mqtt_ca"] = ""

    try:
        poll_interval = int(cfg.get("poll_interval", 30))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("poll_interval must be an integer") from exc
    if not 10 <= poll_interval <= 300:
        raise RuntimeError("poll_interval must be between 10 and 300 seconds")
    cfg["poll_interval"] = poll_interval

    cfg["mqtt_topic_prefix"] = core.validate_topic_prefix(
        "mqtt_topic_prefix",
        cfg.get("mqtt_topic_prefix", "switch_vision/unifi"),
    )
    cfg["mqtt_discovery_prefix"] = core.validate_topic_prefix(
        "mqtt_discovery_prefix",
        cfg.get("mqtt_discovery_prefix", "homeassistant"),
    )
    cfg["mqtt_username"] = str(cfg.get("mqtt_username", "") or "")
    cfg["mqtt_password"] = str(cfg.get("mqtt_password", "") or "")
    return cfg


def load_multi_config(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = load_raw_options(path)
    entries = parse_controller_entries(raw)
    return _mqtt_global_config(raw), entries


def runtime_config(global_cfg: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(global_cfg)
    cfg.pop("controllers", None)
    cfg.update(
        {
            "controller_url": entry["controller_url"],
            "site_id": entry["site_id"],
            "api_key": entry["api_key"],
            "verify_ssl": entry["verify_ssl"],
            "allow_insecure_http": entry["allow_insecure_http"],
        }
    )
    return cfg
