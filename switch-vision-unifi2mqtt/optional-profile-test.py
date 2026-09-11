#!/usr/bin/env python3
from pathlib import Path

text = Path(__file__).with_name("config.yaml").read_text(encoding="utf-8")
options_block = text.split("options:\n", 1)[1].split("\nschema:\n", 1)[0]
schema_block = text.split("\nschema:\n", 1)[1]

for key in ("api_key", "local_api_key", "remote_api_key"):
    assert f"  {key}:" not in options_block, f"{key} must not have a default; Supervisor would require it"
    assert f"  {key}: password?" in schema_block, f"{key} must remain an optional password field"

assert "  priority_transport:" in options_block
assert "  fallback_transport:" in options_block
print("UniFi2MQTT optional Local/Remote profile schema regression: PASS")
