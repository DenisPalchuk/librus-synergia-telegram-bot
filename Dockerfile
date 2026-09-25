FROM python:3.12-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATE_PATH=/data/state.sqlite3

WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && architecture="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$architecture" in \
         amd64) asset=amd64; checksum=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1 ;; \
         arm64) asset=arm64; checksum=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5 ;; \
         arm|armhf) asset=arm; checksum=d961592036e9f87a75e18cf4a7fcb78d1caa5732f57ada3a952906d2d3a4076c ;; \
         *) echo "Unsupported architecture: $architecture" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${asset}" -o /usr/local/bin/supercronic \
    && echo "$checksum  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod +x /usr/local/bin/supercronic \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --system app \
    && useradd --system --gid app app \
    && mkdir /data \
    && chown app:app /data

COPY bot.py .
COPY crontab .
RUN supercronic -test /app/crontab
USER app
CMD ["supercronic", "-split-logs", "/app/crontab"]
