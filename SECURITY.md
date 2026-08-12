# Security

Switch Vision UniFi2MQTT uses the UniFi Network Integration API in read-only mode.

- The UniFi API key is sent only as the `X-API-KEY` request header.
- API keys and MQTT passwords are not written into the normalized device snapshot.
- No UniFi write/action endpoints are used.
- Support bundles and diagnostics should never include raw API keys or MQTT passwords.

Please report security issues privately to the Switch Vision maintainer rather than publishing credentials or sensitive controller data in a public issue.
