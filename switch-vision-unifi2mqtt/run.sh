#!/usr/bin/with-contenv bashio
set -eu
umask 077

VERSION="2.0.49"
bashio::log.info "Switch Vision UniFi2MQTT v${VERSION} starting."

mkdir -p /share/switch_vision/unifi
chmod 0700 /share/switch_vision/unifi

MQTT_CONFIG_HOST="$(bashio::config 'mqtt_host' 2>/dev/null || true)"
MQTT_CONFIG_PORT="$(bashio::config 'mqtt_port' 2>/dev/null || true)"
MQTT_CONFIG_USERNAME="$(bashio::config 'mqtt_username' 2>/dev/null || true)"
MQTT_CONFIG_PASSWORD="$(bashio::config 'mqtt_password' 2>/dev/null || true)"

[ "${MQTT_CONFIG_HOST}" = "null" ] && MQTT_CONFIG_HOST=""
[ "${MQTT_CONFIG_PORT}" = "null" ] && MQTT_CONFIG_PORT=""
[ "${MQTT_CONFIG_USERNAME}" = "null" ] && MQTT_CONFIG_USERNAME=""
[ "${MQTT_CONFIG_PASSWORD}" = "null" ] && MQTT_CONFIG_PASSWORD=""

USE_SUPERVISOR_MQTT=false
case "${MQTT_CONFIG_HOST}" in
  ""|localhost|127.0.0.1|core-mosquitto)
    USE_SUPERVISOR_MQTT=true
    ;;
esac

if [ "${USE_SUPERVISOR_MQTT}" = "true" ]; then
  SERVICE_MQTT_HOST="$(bashio::services mqtt 'host' 2>/dev/null || true)"
  SERVICE_MQTT_PORT="$(bashio::services mqtt 'port' 2>/dev/null || true)"
  SERVICE_MQTT_USERNAME="$(bashio::services mqtt 'username' 2>/dev/null || true)"
  SERVICE_MQTT_PASSWORD="$(bashio::services mqtt 'password' 2>/dev/null || true)"

  [ "${SERVICE_MQTT_HOST}" = "null" ] && SERVICE_MQTT_HOST=""
  [ "${SERVICE_MQTT_PORT}" = "null" ] && SERVICE_MQTT_PORT=""
  [ "${SERVICE_MQTT_USERNAME}" = "null" ] && SERVICE_MQTT_USERNAME=""
  [ "${SERVICE_MQTT_PASSWORD}" = "null" ] && SERVICE_MQTT_PASSWORD=""

  if [ -z "${SERVICE_MQTT_HOST}" ]; then
    bashio::log.fatal "Home Assistant MQTT service is required but Supervisor returned no MQTT service host."
    bashio::exit.nok
  fi

  SV_MQTT_HOST="${SERVICE_MQTT_HOST}"
  SV_MQTT_PORT="${SERVICE_MQTT_PORT:-1883}"
  SV_MQTT_USERNAME="${SERVICE_MQTT_USERNAME}"
  SV_MQTT_PASSWORD="${SERVICE_MQTT_PASSWORD}"
  bashio::log.info "Using Home Assistant Supervisor MQTT service."
else
  SV_MQTT_HOST="${MQTT_CONFIG_HOST}"
  SV_MQTT_PORT="${MQTT_CONFIG_PORT:-1883}"
  SV_MQTT_USERNAME="${MQTT_CONFIG_USERNAME}"
  SV_MQTT_PASSWORD="${MQTT_CONFIG_PASSWORD}"
  bashio::log.info "Using explicitly configured MQTT broker."
fi

export SV_MQTT_HOST SV_MQTT_PORT SV_MQTT_USERNAME SV_MQTT_PASSWORD

bashio::log.info "MQTT broker host: ${SV_MQTT_HOST}"
exec python3 /unifi2mqtt.py --config /data/options.json --snapshot /share/switch_vision/unifi/devices.json
