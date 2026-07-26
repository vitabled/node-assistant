FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    ca-certificates \
    curl \
    gnupg \
    traceroute \
    && rm -rf /var/lib/apt/lists/*
# `traceroute` powers the «Анализ подписки» actual-geo-by-trace (Wave-8). Docker's
# default capability set includes NET_RAW and the backend runs as root, so no
# extra cap_add is needed; the code falls back to IP-DB geo if it's ever missing.

# ── Docker CLI (client only) ──────────────────────────────────
# node-assistant manages the sibling xray-checker container via the host Docker
# daemon (socket mounted in docker-compose). Only the CLI is needed — it talks
# to the host's dockerd; no engine runs inside this container.
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
