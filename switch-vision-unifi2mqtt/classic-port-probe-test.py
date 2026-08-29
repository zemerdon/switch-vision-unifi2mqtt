#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULE_PATH = ROOT / "classic_port_probe.py"
spec = importlib.util.spec_from_file_location("classic_port_probe", MODULE_PATH)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_version() -> None:
    expected_version = (ROOT.parent / "VERSION").read_text(encoding="utf-8").strip()
    assert probe.VERSION == expected_version


def test_site_resolution() -> None:
    sites = [
        {"id": "site-uuid", "internalReference": "default", "name": "Default"},
    ]
    assert probe.select_site(sites, "auto")["id"] == "site-uuid"
    assert probe.select_site(sites, "default")["internalReference"] == "default"
    assert probe.select_site(sites, "site-uuid")["id"] == "site-uuid"


def test_counter_summary_is_private_and_truthful() -> None:
    rows = [
        {
            "model": "USW-Pro-XG-24",
            "name": "PRIVATE-SWITCH-NAME",
            "mac": "aa:bb:cc:dd:ee:ff",
            "ip": "192.0.2.44",
            "serial": "PRIVATE-SERIAL",
            "port_table": [
                {
                    "port_idx": 1,
                    "rx_bytes": 0,
                    "tx_bytes": 123,
                    "rx_packets": 1,
                    "tx_packets": 2,
                    "rx_bytes-r": 0,
                    "tx_bytes-r": 88,
                    "name": "PRIVATE PORT DESCRIPTION",
                    "vlan": "PRIVATE VLAN",
                },
                {
                    "port_idx": 2,
                    "rx_bytes": 456,
                    "tx_bytes": 789,
                    "rx_packets": 3,
                    "tx_packets": 4,
                },
            ],
        },
        {
            "model": "UCG-Ultra",
            "port_table": [
                {"port_idx": 1, "rx_bytes": "10", "tx_bytes": "20"},
                {"port_idx": 2, "rx_bytes": None, "tx_bytes": None},
            ],
        },
        {"model": "UAP", "name": "PRIVATE-AP-NAME"},
    ]
    summary = probe.summarize_classic_devices(rows)
    assert summary["devices_with_port_table"] == 2
    assert summary["total_ports_observed"] == 4
    assert summary["ports_with_rx_bytes"] == 3
    assert summary["ports_with_tx_bytes"] == 3
    assert summary["per_port_byte_counters_candidate"] is True
    assert summary["devices"][0]["port_count"] == 2
    assert summary["devices"][0]["counter_field_counts"]["rx_bytes-r"] == 1

    serialized = json.dumps(summary)
    for forbidden in (
        "PRIVATE-SWITCH-NAME",
        "aa:bb:cc:dd:ee:ff",
        "192.0.2.44",
        "PRIVATE-SERIAL",
        "PRIVATE PORT DESCRIPTION",
        "PRIVATE VLAN",
        "PRIVATE-AP-NAME",
    ):
        assert forbidden not in serialized


def test_no_counter_synthesis() -> None:
    summary = probe.summarize_classic_devices(
        [{"model": "USW-TEST", "port_table": [{"port_idx": 1}]}]
    )
    assert summary["ports_with_rx_bytes"] == 0
    assert summary["ports_with_tx_bytes"] == 0
    assert summary["per_port_byte_counters_candidate"] is False
    assert summary["devices"][0]["per_port_byte_counters_candidate"] is False


def test_secure_output_permissions() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "unifi" / "classic_port_traffic_probe.json"
        probe.secure_write_json(path, probe.base_result())
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["raw_controller_payload_stored"] is False
        assert payload["private_identifiers_included"] is False


def test_invalid_classic_payload_fails_closed() -> None:
    try:
        probe.parse_classic_devices({"unexpected": []})
    except probe.ProbeError as exc:
        assert exc.code == "unexpected_classic_response"
    else:
        raise AssertionError("unexpected classic payload was accepted")


if __name__ == "__main__":
    test_probe_version()
    test_site_resolution()
    test_counter_summary_is_private_and_truthful()
    test_no_counter_synthesis()
    test_secure_output_permissions()
    test_invalid_classic_payload_fails_closed()
    print("UniFi2MQTT classic port-stat capability probe: PASS")
