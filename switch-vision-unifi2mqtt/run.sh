#!/usr/bin/env sh
set -eu

VERSION="2.0.37"
echo "Switch Vision UniFi2MQTT v${VERSION} starting."
mkdir -p /share/switch_vision/unifi
exec python3 /unifi2mqtt.py --config /data/options.json --snapshot /share/switch_vision/unifi/devices.json
