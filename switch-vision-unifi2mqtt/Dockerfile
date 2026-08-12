FROM ghcr.io/home-assistant/base:3.22

ARG BUILD_VERSION
ARG BUILD_ARCH

RUN apk add --no-cache python3 py3-paho-mqtt

COPY run.sh /run.sh
COPY unifi2mqtt.py /unifi2mqtt.py
RUN chmod a+x /run.sh /unifi2mqtt.py

CMD [ "/run.sh" ]
