from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0.49"
APP = ROOT / "switch-vision-unifi2mqtt"


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement target, found {count}")
    write(path, text.replace(old, new, 1))


# Preserve the management IP already returned by the official UniFi Network
# Integration API in the normalized shared snapshot consumed by Switch Vision.
bridge = APP / "unifi2mqtt.py"
replace_once(bridge, 'VERSION = "2.0.48"', f'VERSION = "{VERSION}"')
replace_once(
    bridge,
    '''        "firmware": str(source.get("firmwareVersion", "")),\n        "state": str(source.get("state", "UNKNOWN")).upper(),''',
    '''        "firmware": str(source.get("firmwareVersion", "")),\n        "ip_address": str(source.get("ipAddress") or ""),\n        "state": str(source.get("state", "UNKNOWN")).upper(),''',
)

write(ROOT / "VERSION", VERSION + "\n")
replace_once(APP / "config.yaml", 'version: "2.0.48"', f'version: "{VERSION}"')
replace_once(APP / "run.sh", 'VERSION="2.0.48"', f'VERSION="{VERSION}"')

hardening = APP / "hardening-test.py"
replace_once(hardening, 'assert m.VERSION == "2.0.48"', f'assert m.VERSION == "{VERSION}"')
replace_once(
    hardening,
    'print("Switch Vision UniFi2MQTT v2.0.48 hardening regression: PASS")',
    f'print("Switch Vision UniFi2MQTT v{VERSION} hardening regression: PASS")',
)

# Add regression evidence to both the focused self-test and the realistic fixture.
self_test = APP / "self-test.py"
replace_once(
    self_test,
    '''        "id": "device-1", "name": "Lab Switch", "model": "USW Enterprise 8 PoE",\n        "state": "ONLINE", "firmwareVersion": "7.4.1",''',
    '''        "id": "device-1", "name": "Lab Switch", "model": "USW Enterprise 8 PoE",\n        "state": "ONLINE", "firmwareVersion": "7.4.1", "ipAddress": "192.0.2.10",''',
)
replace_once(
    self_test,
    '''    assert n["model"] == "USW Enterprise 8 PoE"\n    assert len(n["ports"]) == 2''',
    '''    assert n["model"] == "USW Enterprise 8 PoE"\n    assert n["ip_address"] == "192.0.2.10"\n    assert len(n["ports"]) == 2''',
)

fixture_path = APP / "fixtures/unifi_devices_fixture.json"
fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
fixture.setdefault("detail", {})["ipAddress"] = "192.0.2.10"
write(fixture_path, json.dumps(fixture, indent=2) + "\n")

entry = f'''## v{VERSION}\n\n- Preserve the official UniFi Network Integration API `ipAddress` value in the normalized Switch Vision snapshot as `ip_address`.\n- Make the live management IP available to Switch Vision Core without requiring a manually configured `switch_ip` or `management_ip`.\n- Keep privacy-safe diagnostics unchanged: management IPs remain excluded from `diagnostics.json` and are only present in the existing owner-only normalized device snapshot.\n- Preserve the current API capability boundary: no per-port RX/TX traffic, VLAN, description, temperature, or other unavailable telemetry is synthesized.\n- Add fixture and normalization regression coverage for management-IP preservation.\n\n'''
for changelog_path in (ROOT / "CHANGELOG.md", APP / "CHANGELOG.md"):
    text = changelog_path.read_text(encoding="utf-8")
    if f"## v{VERSION}" in text:
        raise SystemExit(f"{changelog_path}: v{VERSION} changelog entry already exists")
    write(changelog_path, text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1))

# Guard the privacy contract directly in source and tests.
source = bridge.read_text(encoding="utf-8")
assert '"ip_address": str(source.get("ipAddress") or "")' in source
assert '"ip_address"' not in source[source.find("def write_diagnostics"):source.find("def extract_ports")]
assert (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") == (APP / "CHANGELOG.md").read_text(encoding="utf-8")

subprocess.run(["python3", "-m", "py_compile", str(bridge), str(self_test), str(hardening), str(APP / "mqtt-lifecycle-test.py")], cwd=ROOT, check=True)
subprocess.run(["bash", "-n", str(APP / "run.sh")], cwd=ROOT, check=True)
subprocess.run(["python3", str(self_test)], cwd=ROOT, check=True)
subprocess.run(["python3", str(hardening)], cwd=ROOT, check=True)
subprocess.run(["python3", str(APP / "mqtt-lifecycle-test.py")], cwd=ROOT, check=True)

print(f"UniFi2MQTT {VERSION} preparation and regression suite: PASS")
