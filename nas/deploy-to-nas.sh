#!/bin/bash
# Deploy the 0DTE PAPER agent to the Synology NAS as an ISOLATED container.
#
# Credentials: reuses the NAS host / SSH key / DSM sudo password already stored
# for the trading project by SOURCING that env file at run time (NAS_ENV). The
# sudo password is piped to `sudo -S` over the encrypted SSH channel from LOCAL
# stdin, so it never appears on a command line, in `ps` on the NAS, or in a log.
# Nothing secret is copied or duplicated.
#
# Isolation from ~/trading: separate NAS dir, image, and container name; no
# shared volumes or ports. A guard refuses any NAS_PATH under a `trading` tree.
#
# Usage:
#   nas/deploy-to-nas.sh                 # arm A (default): config.json, .env
#   ARM=B nas/deploy-to-nas.sh           # arm B: config.armB.json, .env.armB, own container
#   NAS_ENV=/path/to/nas.env ARM=B nas/deploy-to-nas.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAS_ENV="${NAS_ENV:-/Users/kiba/trading/nas/nas.env}"
[[ -f "$NAS_ENV" ]] || { echo "FATAL: NAS creds not found at $NAS_ENV (set NAS_ENV=...)"; exit 1; }
# shellcheck source=/dev/null
source "$NAS_ENV"

: "${NAS_HOST:?NAS_HOST missing from $NAS_ENV}"
: "${NAS_USER:?NAS_USER missing from $NAS_ENV}"
: "${NAS_SUDO_PASSWORD:?NAS_SUDO_PASSWORD missing from $NAS_ENV (Synology docker needs sudo)}"
NAS_SSH_KEY="${NAS_SSH_KEY:-$HOME/.ssh/id_ed25519_nas}"; SSH_KEY="${NAS_SSH_KEY/#\~/$HOME}"

# --- which arm -----------------------------------------------------------------
# Arm A (default) and arm B are fully isolated siblings: own NAS dir, image,
# container, .env, and config. Same codebase; arm B just loads config.armB.json.
ARM="${ARM:-A}"
# OTHER_ENVS = every arm env EXCEPT this arm's, so a deploy ships ONLY its own
# account keys to its own NAS dir (never another arm's credentials).
case "$ARM" in
  A|a) SUFFIX="";      COMPOSE_FILE="docker-compose.yml";      ENV_FILE=".env";        CONFIG_FILE="config.json";      OTHER_ENVS=".env.armB .env.armC .env.armD"; PROJECT="alpaca-hackathon";;
  B|b) SUFFIX="-armB"; COMPOSE_FILE="docker-compose.armB.yml"; ENV_FILE=".env.armB";   CONFIG_FILE="config.armB.json"; OTHER_ENVS=".env .env.armC .env.armD";      PROJECT="alpaca-hackathon-armb";;
  C|c) SUFFIX="-armC"; COMPOSE_FILE="docker-compose.armC.yml"; ENV_FILE=".env.armC";   CONFIG_FILE="config.armC.json"; OTHER_ENVS=".env .env.armB .env.armD";      PROJECT="alpaca-hackathon-armc";;
  D|d) SUFFIX="-armD"; COMPOSE_FILE="docker-compose.armD.yml"; ENV_FILE=".env.armD";   CONFIG_FILE="config.armD.json"; OTHER_ENVS=".env .env.armB .env.armC";      PROJECT="alpaca-hackathon-armd";;
  *)   echo "FATAL: ARM must be A, B, C or D (got '$ARM')"; exit 1;;
esac
NAME="alpaca-hackathon${SUFFIX}"
# EXPLICIT, per-arm compose project name (lowercase; must not be 'nas').
# Without this, compose derives the project from the dir basename ('nas') — the
# SAME as every trading-* stack AND the other hackathon arm. Both hackathon arms
# also use service name 'agent', so a shared project made `up` for one RECREATE
# the other's container (arm B once clobbered arm A this way). A distinct project
# per arm makes each `up`/`down` touch ONLY that arm. The trading arms were never
# at risk (distinct service names) but this is belt-and-suspenders isolation.
case "$PROJECT" in nas|*trading*) echo "FATAL: refusing compose project '$PROJECT'"; exit 1;; esac

# Guard: never deploy an arm whose local env still has placeholder keys, or is
# missing entirely — that would launch a container that can't authenticate (or,
# worse, silently reuse a stale key). Checked LOCALLY before anything is synced.
[[ -f "$PROJECT_DIR/$ENV_FILE" ]] || { echo "FATAL: $ENV_FILE not found — create it from .env.example first"; exit 1; }
if grep -vE '^[[:space:]]*#' "$PROJECT_DIR/$ENV_FILE" | grep -qE '^(ALPACA_API_KEY|ALPACA_SECRET_KEY)=.*REPLACE_ME'; then
  echo "FATAL: $ENV_FILE still has REPLACE_ME placeholder keys — fill in the real paper key/secret first"; exit 1
fi

NAS_PATH="/volume1/docker/alpaca-hackathon${SUFFIX}"   # ISOLATED from /volume1/docker/trading
DC="/usr/local/bin/docker-compose"
DOCKER="/usr/local/bin/docker"
ENVPATH="env PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REMOTE="${NAS_USER}@${NAS_HOST}"
SSH=(ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -i "$SSH_KEY")

case "$NAS_PATH" in
  *trading*) echo "FATAL: NAS_PATH ($NAS_PATH) resembles the trading tree — refusing."; exit 1;;
esac

root() {   # run "$1" as root on the NAS; password via LOCAL stdin (never in ps)
  printf '%s\n' "$NAS_SUDO_PASSWORD" | "${SSH[@]}" "$REMOTE" "sudo -S -p '' sh -c '$1'"
}

echo "==> [1/5] NAS reachable? (arm $ARM -> $NAME)"
"${SSH[@]}" "$REMOTE" "echo '  NAS:' \$(hostname) '| target:' $NAS_PATH"

echo "==> [2/5] Sync project (tar over SSH; Synology blocks rsync/scp)"
( cd "$PROJECT_DIR" && tar czf - \
    --exclude=.git --exclude='__pycache__' --exclude='*/__pycache__' \
    --exclude='.pytest_cache' --exclude='logs' \
    --exclude='*.bak' --exclude='config.json.bak2' \
    $(for e in $OTHER_ENVS; do printf ' --exclude=./%s' "$e"; done) \
    . ) | "${SSH[@]}" "$REMOTE" "mkdir -p $NAS_PATH/logs && cd $NAS_PATH && tar xzf -"

echo "==> [3/5] Verify keys + config + Dockerfile landed on NAS"
"${SSH[@]}" "$REMOTE" "test -f $NAS_PATH/$ENV_FILE && test -f $NAS_PATH/$CONFIG_FILE && test -f $NAS_PATH/nas/$COMPOSE_FILE && test -f $NAS_PATH/nas/Dockerfile" \
  || { echo "FATAL: $ENV_FILE / $CONFIG_FILE / nas/$COMPOSE_FILE / Dockerfile missing on NAS after sync"; exit 1; }

# The bind-mounted logs dir is created by the SSH user (a NAS uid), but the
# container runs as uid 1000 (appuser). Without this the agent's FIRST journal
# write is EACCES and it crash-loops all session while the healthcheck stays
# green. Align owner + add the write bit before the container ever starts.
echo "==> [4/5] Align logs dir to container uid 1000, then build + (re)start (sudo) — a few min on the Celeron"
root "mkdir -p $NAS_PATH/logs && chown -R 1000:1000 $NAS_PATH/logs && chmod -R 0775 $NAS_PATH/logs"
root "cd $NAS_PATH/nas && $ENVPATH $DC -p $PROJECT -f $COMPOSE_FILE up -d --build"

echo "==> [5/5] Status + recent logs"
root "cd $NAS_PATH/nas && $ENVPATH $DC -p $PROJECT -f $COMPOSE_FILE ps"
# Verify the config is IN THE IMAGE, not just the host build context. The
# host-side test in [3] passes even when the Dockerfile forgot to COPY it, which
# would crash-loop the agent all session behind a green healthcheck.
root "$ENVPATH $DOCKER exec $NAME test -f /app/$CONFIG_FILE" \
  && echo "  verified: /app/$CONFIG_FILE is present inside the running container" \
  || { echo "FATAL: $CONFIG_FILE is missing INSIDE the $NAME image (Dockerfile COPY?) — agent would crash-loop"; exit 1; }
root "$ENVPATH $DOCKER logs --tail 25 $NAME 2>&1" || true

cat <<EOF

Deploy complete — container '$NAME' (arm $ARM, isolated at $NAS_PATH).
It idles until 09:20 ET weekdays, then runs 'src.agent --loop --mode paper${SUFFIX:+ --config $CONFIG_FILE}' to EOD.

  Tail decisions : ssh -i $SSH_KEY $REMOTE "tail -f $NAS_PATH/logs/decisions-\$(TZ=America/New_York date +%Y-%m-%d).jsonl"
  Container logs : root "$DOCKER logs -f $NAME"
  Stop           : root "cd $NAS_PATH/nas && $DC -p $PROJECT -f $COMPOSE_FILE down"
EOF
