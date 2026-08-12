# Contributing

Hardware support is evidence-driven. When reporting a UniFi model, include sanitized normalized device data and the exact model/firmware where possible. Never include API keys, MQTT passwords, private controller credentials or other secrets.

Changes should preserve these rules:

- UniFi API access remains read-only.
- Do not invent per-port traffic when the API does not expose it.
- DOWN ports must not report a negotiated speed from nominal/default values.
- A transient failure for one known device must not erase its last good snapshot entry.
- Run `python3 switch-vision-unifi2mqtt/self-test.py` before submitting changes.
