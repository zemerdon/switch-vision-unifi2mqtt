FROM ghcr.io/home-assistant/base:3.22@sha256:8f937e0b89e108f432d3cb140900f06e568f8f4fbdb7987472447f54d8b24495

ARG BUILD_VERSION
ARG BUILD_ARCH

RUN apk add --no-cache python3 py3-paho-mqtt

COPY run.sh /run.sh
COPY unifi2mqtt.py /unifi2mqtt.py
COPY classic_port_probe.py /classic_port_probe.py
RUN chmod a+x /run.sh /unifi2mqtt.py /classic_port_probe.py

CMD [ "/run.sh" ]
