#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from urllib.parse import quote

import classic_port_probe as probe
import unifi2mqtt as core
from controller_config import (
    load_raw_options,
    multi_controller_enabled,
    parse_controller_entries,
)


def _probe_entry(entry: dict[str, Any]) -> dict[str, Any]:
    result = probe.base_result()
    result["transport"] = str(entry.get("transport") or "local")
    try:
        client = core.client_from_config(entry)
        result["stage"] = "site_resolution"
        site = client.resolve_site()
        site_ref = str(site.get("internalReference") or "").strip()
        if not site_ref:
            raise probe.ProbeError("site_reference_unavailable")

        result["stage"] = "classic_port_statistics"
        classic_payload = client._get(
            "/proxy/network/api/s/"
            f"{quote(site_ref, safe='')}/stat/device"
        )
        rows = probe.parse_classic_devices(classic_payload)
        result.update(probe.summarize_classic_devices(rows))
        result["classic_endpoint_available"] = True
        result["status"] = "ok"
        result["stage"] = "complete"
    except probe.ProbeError as exc:
        result["status"] = "unavailable"
        result["error_type"] = exc.code
    except RuntimeError:
        result["status"] = "unavailable"
        result["error_type"] = "network_api_unavailable"
    except Exception:
        result["status"] = "unavailable"
        result["error_type"] = "unexpected_probe_error"
    return result


def run_multi_probe(config_path: Path) -> dict[str, Any]:
    raw = load_raw_options(config_path)
    if not multi_controller_enabled(raw):
        return probe.run_probe(config_path)

    entries = parse_controller_entries(raw)
    results = [_probe_entry(entry) for entry in entries]
    available = [item for item in results if item.get("status") == "ok"]

    aggregate = probe.base_result()
    aggregate.update(
        {
            "mode": "multi_controller",
            "controllers_configured": len(results),
            "controllers_available": len(available),
            "controllers_unavailable": len(results) - len(available),
            "classic_endpoint_available": bool(available),
            "status": (
                "ok"
                if len(available) == len(results)
                else "partial"
                if available
                else "unavailable"
            ),
            "stage": "complete",
            "devices_with_port_table": sum(
                int(item.get("devices_with_port_table", 0) or 0) for item in available
            ),
            "total_ports_observed": sum(
                int(item.get("total_ports_observed", 0) or 0) for item in available
            ),
            "ports_with_rx_bytes": sum(
                int(item.get("ports_with_rx_bytes", 0) or 0) for item in available
            ),
            "ports_with_tx_bytes": sum(
                int(item.get("ports_with_tx_bytes", 0) or 0) for item in available
            ),
            "per_port_byte_counters_candidate": any(
                bool(item.get("per_port_byte_counters_candidate")) for item in available
            ),
            "devices": [
                device
                for item in available
                for device in item.get("devices", [])
                if isinstance(device, dict)
            ],
            "controller_results": [
                {
                    "position": position,
                    "status": str(item.get("status") or "unavailable"),
                    "stage": str(item.get("stage") or "probe"),
                    **(
                        {"error_type": str(item.get("error_type"))[:128]}
                        if item.get("error_type")
                        else {}
                    ),
                }
                for position, item in enumerate(results, start=1)
            ],
        }
    )
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        payload = run_multi_probe(args.config)
    except Exception:
        payload = probe.base_result()
        payload["status"] = "unavailable"
        payload["error_type"] = "config_read_failed"

    try:
        probe.secure_write_json(args.output, payload)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
