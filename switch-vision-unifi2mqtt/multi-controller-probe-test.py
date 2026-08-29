#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import multi_controller_probe as multi_probe


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "options.json"
        config.write_text(
            json.dumps(
                {
                    "controllers": [
                        {
                            "id": "home",
                            "controller_url": "https://10.0.0.1",
                            "site_id": "auto",
                            "api_key": "home-secret",
                        },
                        {
                            "id": "remote",
                            "controller_url": "https://10.1.0.1",
                            "site_id": "Branch",
                            "api_key": "remote-secret",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        original = multi_probe._probe_entry
        calls = 0

        def fake_probe(_entry: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "ok",
                    "stage": "complete",
                    "classic_endpoint_available": True,
                    "devices_with_port_table": 2,
                    "total_ports_observed": 32,
                    "ports_with_rx_bytes": 30,
                    "ports_with_tx_bytes": 29,
                    "per_port_byte_counters_candidate": True,
                    "devices": [
                        {
                            "model": "USW Pro 24 PoE",
                            "port_count": 28,
                            "counter_field_counts": {},
                            "per_port_byte_counters_candidate": True,
                        }
                    ],
                }
            return {
                "status": "unavailable",
                "stage": "classic_port_statistics",
                "classic_endpoint_available": False,
                "error_type": "http_404",
            }

        multi_probe._probe_entry = fake_probe
        try:
            result = multi_probe.run_multi_probe(config)
        finally:
            multi_probe._probe_entry = original

        assert result["mode"] == "multi_controller"
        assert result["status"] == "partial"
        assert result["controllers_configured"] == 2
        assert result["controllers_available"] == 1
        assert result["controllers_unavailable"] == 1
        assert result["total_ports_observed"] == 32
        assert result["ports_with_rx_bytes"] == 30
        assert result["ports_with_tx_bytes"] == 29
        assert result["per_port_byte_counters_candidate"] is True
        assert result["controller_results"][1]["error_type"] == "http_404"

        serialized = json.dumps(result).lower()
        assert "home-secret" not in serialized
        assert "remote-secret" not in serialized
        assert "10.0.0.1" not in serialized
        assert "10.1.0.1" not in serialized
        assert '"home"' not in serialized
        assert '"remote"' not in serialized

    print("multi-controller capability probe tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
