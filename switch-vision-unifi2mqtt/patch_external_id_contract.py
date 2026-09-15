#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT_ACTIVITY = ROOT / "port_activity.py"
ACTIVITY_TEST = ROOT / "activity-test.py"
README = ROOT.parent / "README.md"
PACKAGE_README = ROOT / "README.md"
CHANGELOG = ROOT.parent / "CHANGELOG.md"
PACKAGE_CHANGELOG = ROOT / "CHANGELOG.md"

text = PORT_ACTIVITY.read_text(encoding="utf-8")
old_doc = '''    The join is intentionally strict: normalized hardware MAC is mandatory,\n    classic external_id must agree when present, and every official port needs\n    exactly one classic port_idx with valid cumulative RX/TX byte counters before\n    the device-level per_port_traffic capability becomes true.\n'''
new_doc = '''    The join is intentionally strict: normalized hardware MAC is the sole\n    device join key. Classic external_id is not assumed to share the official\n    Integration API ID namespace and is therefore advisory only. Every official\n    port needs exactly one classic port_idx with valid cumulative RX/TX byte\n    counters before the device-level per_port_traffic capability becomes true.\n'''
if old_doc in text:
    text = text.replace(old_doc, new_doc, 1)
elif new_doc not in text:
    raise SystemExit("port_activity join-contract docstring marker missing")

old_guard = '''    external_id = str(classic.get("external_id") or "").strip()\n    device_id = str(clone.get("id") or "").strip()\n    if external_id and device_id and external_id != device_id:\n        return mark_traffic_unavailable(clone, previous)\n\n'''
if old_guard in text:
    text = text.replace(old_guard, "", 1)
elif "external_id and device_id" in text:
    raise SystemExit("unexpected external_id identity guard remains")
PORT_ACTIVITY.write_text(text, encoding="utf-8", newline="\n")

text = ACTIVITY_TEST.read_text(encoding="utf-8")
old_test = '''    # Device identity and port joins fail closed.\n    wrong_id = classic_device()\n    wrong_id["external_id"] = "another-device"\n    failed = activity.enrich_device(official_device(), wrong_id, first)\n    assert failed["api_capabilities"]["per_port_traffic"] is False\n    assert port(failed, 4)["traffic"]["available"] is False\n\n'''
new_test = '''    # Device identity is MAC-authoritative. Classic external_id is advisory\n    # because its namespace is not proven to match the Integration API UUID.\n    advisory_id = classic_device()\n    advisory_id["external_id"] = "another-device"\n    matched = activity.enrich_device(official_device(), advisory_id, first)\n    assert matched["api_capabilities"]["per_port_traffic"] is True\n    assert port(matched, 4)["traffic"]["available"] is True\n\n    wrong_mac = classic_device()\n    wrong_mac["mac"] = "58:d6:1f:14:6e:41"\n    failed = activity.enrich_device(official_device(), wrong_mac, first)\n    assert failed["api_capabilities"]["per_port_traffic"] is False\n    assert port(failed, 4)["traffic"]["available"] is False\n\n'''
if old_test in text:
    text = text.replace(old_test, new_test, 1)
elif new_test not in text:
    raise SystemExit("activity-test identity-contract marker missing")
ACTIVITY_TEST.write_text(text, encoding="utf-8", newline="\n")

for path in (README, PACKAGE_README):
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    old = "A device is enriched only when its normalized hardware MAC matches exactly one classic device row. Physical ports are then joined by official `idx` to classic `port_idx`."
    new = "A device is enriched only when its normalized hardware MAC matches exactly one classic device row. Classic `external_id` is advisory only because its namespace is not assumed to match the Integration API device UUID. Physical ports are then joined by official `idx` to classic `port_idx`."
    if old in text:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")

bullet = "- Keep normalized hardware MAC as the sole deterministic device join key; classic `external_id` remains advisory because its namespace is not assumed to match the Integration API device UUID.\n"
for path in (CHANGELOG, PACKAGE_CHANGELOG):
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    if bullet not in text:
        lines = text.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("## "):
                insert_at = i + 1
                while insert_at < len(lines) and not lines[insert_at].startswith("-"):
                    insert_at += 1
                break
        lines.insert(insert_at, bullet)
        text = "".join(lines)
    path.write_text(text, encoding="utf-8", newline="\n")

print("UniFi2MQTT 4.0 MAC-authoritative identity contract applied")
