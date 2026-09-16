#!/usr/bin/env python3
"""Read the shared Switch Vision device-control state for UniFi polling."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(
    os.environ.get("SV_DEVICE_CONTROL_PATH", "/share/switch_vision/device-control.json")
)


def load(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"schema_version": 1, "states": {}}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {"schema_version": 1, "states": {}}
    states = payload.get("states") if isinstance(payload.get("states"), dict) else {}
    safe: dict[str, str] = {}
    for key, value in states.items():
        text = str(key or "").strip()
        state = str(value or "").strip().casefold()
        if text.startswith("unifi:") and state in {"enabled", "disabled"}:
            safe[text] = state
    return {"schema_version": 1, "states": safe}


def enabled(device_id: str, path: Path = DEFAULT_PATH) -> bool:
    key = f"unifi:{str(device_id or '').strip()}"
    return load(path).get("states", {}).get(key) != "disabled"
