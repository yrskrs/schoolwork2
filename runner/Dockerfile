FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-tk python3-pil xvfb openbox xdotool \
    fonts-dejavu-core libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 \
    libxrender1 libxi6 libsm6 libxext6 libasound2t64 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/broker-venv \
    && /opt/broker-venv/bin/pip install --no-cache-dir docker==7.1.0 \
    && mkdir /work /wheels && chown 1000:1000 /work /wheels
COPY runner /opt/runner
WORKDIR /opt/runner
CMD ["/opt/broker-venv/bin/python", "/opt/runner/service.py"]
ARG APP_VERSION=4.5.7
ARG VCS_REF=local
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.source="https://github.com/yrskrs/schoolwork2"
