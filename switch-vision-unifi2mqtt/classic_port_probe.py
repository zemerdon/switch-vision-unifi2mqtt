#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import stat
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

VERSION = "2.0.50"
MAX_API_RESPONSE_BYTES = 4 * 1024 * 1024
COUNTER_FIELDS = (
    "rx_bytes",
    "tx_bytes",
    "rx_packets",
    "tx_packets",
    "rx_errors",
    "tx_errors",
    "rx_bytes-r",
    "tx_bytes-r",
)


class ProbeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def validate_controller_url(value: Any, allow_insecure_http: bool = False) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text or _has_control_chars(text):
        raise ProbeError("invalid_controller_url")
    parsed = urlsplit(text)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ProbeError("invalid_controller_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProbeError("invalid_controller_url")
    if parsed.path not in {"", "/"}:
        raise ProbeError("invalid_controller_url")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ProbeError("plaintext_controller_not_allowed")
    return text


def _read_json_response(response: Any) -> Any:
    raw = response.read(MAX_API_RESPONSE_BYTES + 1)
    if len(raw) > MAX_API_RESPONSE_BYTES:
        raise ProbeError("response_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("invalid_json") from exc


def request_json(
    base_url: str,
    path: str,
    api_key: str,
    context: ssl.SSLContext,
) -> Any:
    req = Request(
        base_url + path,
        headers={
            "Accept": "application/json",
            "X-API-KEY": api_key,
            "User-Agent": f"Switch-Vision-UniFi2MQTT-Port-Probe/{VERSION}",
        },
        method="GET",
    )
    try:
        with urlopen(req, context=context, timeout=15) as response:
            return _read_json_response(response)
    except HTTPError as exc:
        raise ProbeError(f"http_{exc.code}") from exc
    except URLError as exc:
        raise ProbeError("connection_failed") from exc


def parse_sites(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        for key in ("data", "sites"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        if rows is None:
            raise ProbeError("unexpected_sites_response")
    else:
        raise ProbeError("unexpected_sites_response")
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    ]


def select_site(sites: list[dict[str, Any]], requested: str) -> dict[str, Any]:
    if not sites:
        raise ProbeError("no_sites")
    requested = str(requested or "auto").strip() or "auto"
    requested_key = requested.casefold()

    def internal_ref(site: dict[str, Any]) -> str:
        return str(site.get("internalReference") or "").strip()

    def name(site: dict[str, Any]) -> str:
        return str(site.get("name") or "").strip()

    if requested_key in {"auto", "default"}:
        defaults = [
            site
            for site in sites
            if internal_ref(site).casefold() == "default"
            or name(site).casefold() == "default"
        ]
        if len(defaults) == 1:
            selected = defaults[0]
        elif len(sites) == 1:
            selected = sites[0]
        else:
            raise ProbeError("ambiguous_site")
    else:
        matches = [
            site
            for site in sites
            if str(site.get("id") or "").strip() == requested
            or internal_ref(site).casefold() == requested_key
            or name(site).casefold() == requested_key
        ]
        if len(matches) != 1:
            raise ProbeError("site_not_uniquely_resolved")
        selected = matches[0]

    reference = internal_ref(selected)
    if not reference or len(reference) > 256 or _has_control_chars(reference):
        raise ProbeError("site_reference_unavailable")
    return selected


def parse_classic_devices(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    else:
        raise ProbeError("unexpected_classic_response")
    return [row for row in rows if isinstance(row, dict)]


def safe_model(value: Any) -> str:
    text = " ".join(str(value or "Unknown").strip().split())
    if (
        not text
        or len(text) > 128
        or _has_control_chars(text)
        or not re.fullmatch(r"[A-Za-z0-9._+() /-]+", text)
    ):
        return "Unknown"
    return text


def is_counter(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value >= 0
    if isinstance(value, str):
        value = value.strip()
        return bool(value) and value.isdigit()
    return False


def summarize_classic_devices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    total_ports = 0
    total_rx_bytes = 0
    total_tx_bytes = 0

    for row in rows:
        port_table = row.get("port_table")
        if not isinstance(port_table, list):
            continue
        ports = [port for port in port_table if isinstance(port, dict)]
        counts = {
            field: sum(1 for port in ports if is_counter(port.get(field)))
            for field in COUNTER_FIELDS
        }
        port_count = len(ports)
        total_ports += port_count
        total_rx_bytes += counts["rx_bytes"]
        total_tx_bytes += counts["tx_bytes"]
        devices.append(
            {
                "model": safe_model(row.get("model")),
                "port_count": port_count,
                "counter_field_counts": counts,
                "per_port_byte_counters_candidate": bool(
                    port_count
                    and counts["rx_bytes"] > 0
                    and counts["tx_bytes"] > 0
                ),
            }
        )

    return {
        "devices_with_port_table": len(devices),
        "total_ports_observed": total_ports,
        "ports_with_rx_bytes": total_rx_bytes,
        "ports_with_tx_bytes": total_tx_bytes,
        "per_port_byte_counters_candidate": bool(
            total_ports and total_rx_bytes and total_tx_bytes
        ),
        "devices": devices,
    }


def base_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product": "Switch Vision UniFi2MQTT",
        "version": VERSION,
        "generated_at": int(time.time()),
        "purpose": "read_only_per_port_traffic_capability_probe",
        "endpoint_family": "classic_local_network_api",
        "endpoint_template": "/proxy/network/api/s/<site>/stat/device",
        "authentication": "existing_integration_api_key",
        "raw_controller_payload_stored": False,
        "private_identifiers_included": False,
        "status": "unavailable",
        "stage": "startup",
        "classic_endpoint_available": False,
    }


def secure_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise ProbeError("output_is_symlink")
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
        os.replace(temp, path)
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ProbeError("output_permissions_invalid")
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def run_probe(config_path: Path) -> dict[str, Any]:
    result = base_result()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ProbeError("invalid_config")
        api_key = str(config.get("api_key") or "").strip()
        if not api_key or _has_control_chars(api_key):
            raise ProbeError("api_key_unavailable")
        allow_http = truthy(config.get("allow_insecure_http", False))
        base_url = validate_controller_url(config.get("controller_url"), allow_http)
        context = ssl.create_default_context()
        if not truthy(config.get("verify_ssl", True)):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        result["stage"] = "site_resolution"
        sites_payload = request_json(
            base_url,
            "/proxy/network/integration/v1/sites",
            api_key,
            context,
        )
        site = select_site(parse_sites(sites_payload), str(config.get("site_id") or "auto"))
        site_ref = str(site.get("internalReference") or "").strip()

        result["stage"] = "classic_port_statistics"
        classic_payload = request_json(
            base_url,
            "/proxy/network/api/s/"
            f"{quote(site_ref, safe='')}/stat/device",
            api_key,
            context,
        )
        rows = parse_classic_devices(classic_payload)
        result.update(summarize_classic_devices(rows))
        result["classic_endpoint_available"] = True
        result["status"] = "ok"
        result["stage"] = "complete"
    except (OSError, json.JSONDecodeError):
        result["status"] = "unavailable"
        result["error_type"] = "config_read_failed"
    except ProbeError as exc:
        result["status"] = "unavailable"
        result["error_type"] = exc.code
    except Exception:
        result["status"] = "unavailable"
        result["error_type"] = "unexpected_probe_error"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = run_probe(args.config)
    try:
        secure_write_json(args.output, payload)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
