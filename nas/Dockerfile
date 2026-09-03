# 0DTE PAPER agent — Synology DS920+ (x86-64), Docker 20.10.3 / compose v1.
# Mirrors the trading image (python:3.11-slim-bookworm, TZ America/New_York).
# Secrets are NEVER baked: .env is injected at runtime via compose (env_file +
# read-only mount). Only application code + deps live in the image.
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata procps \
    && rm -rf /var/lib/apt/lists/*

# --- Alpaca CLI (linux/amd64 prebuilt release, checksum-verified) -------------
# The hackathon requires placing orders through Alpaca's CLI. The Mac binary is
# arm64 (Mach-O), useless here, so we fetch the official linux/amd64 build and
# verify its sha256 against the release checksums file. Pinned for reproducibility.
ARG ALPACA_CLI_VERSION=0.0.14
RUN set -eux; \
    cd /tmp; \
    ver="${ALPACA_CLI_VERSION}"; \
    file="cli_${ver}_linux_amd64.tar.gz"; \
    base="https://github.com/alpacahq/cli/releases/download/v${ver}"; \
    curl -fsSL -o "$file" "${base}/${file}"; \
    curl -fsSL -o checksums.txt "${base}/checksums.txt"; \
    grep "  ${file}\$" checksums.txt | sha256sum -c -; \
    mkdir -p x && tar xzf "$file" -C x; \
    bin="$(find x -type f \( -name alpaca -o -name cli \) | head -1)"; \
    test -n "$bin"; \
    install -m 0755 "$bin" /usr/local/bin/alpaca; \
    rm -rf /tmp/*; \
    /usr/local/bin/alpaca --help >/dev/null 2>&1 || true

ENV TZ=America/New_York
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code only. NO .env here (see .dockerignore) — keys are injected at
# runtime so they never enter an image layer.
COPY src/ ./src/
COPY tests/ ./tests/
COPY config.json ./config.json
# Arm B/C load config.armB.json / config.armC.json via AGENT_CONFIG; each MUST be
# in the image (the container mounts only .env + logs, not the config). Harmless
# in images that never read the other arm's config. Without these, that arm
# crash-loops on FileNotFoundError.
COPY config.armB.json ./config.armB.json
COPY config.armC.json ./config.armC.json
COPY config.armD.json ./config.armD.json
COPY smoke_trade.py ./smoke_trade.py
COPY nas/run_daily.py ./nas/run_daily.py

# DEPLOY GATE: the full network-free suite must pass under these exact pinned
# versions, or the image does not build. This is what makes "the container runs
# what was tested" a guarantee rather than a hope.
RUN python -m pytest -q

# Unprivileged user with a writable HOME (the CLI may cache config under $HOME).
RUN useradd -m -u 1000 appuser && mkdir -p /app/logs && chown -R appuser /app
USER appuser
ENV HOME=/home/appuser

# Healthy = the daily supervisor process is alive.
HEALTHCHECK --interval=10m --timeout=15s --start-period=30s --retries=3 \
  CMD pgrep -f run_daily.py >/dev/null || exit 1

CMD ["python", "-u", "nas/run_daily.py"]
