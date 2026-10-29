#!/usr/bin/env bash
# Установка агента expsched на машину с контейнерами (Linux): пакет из выпуска на GitHub, образ встроенной
# нагрузки из GHCR, настройки, по желанию — служба systemd (как в docs/agent.md, «Служба»).
#
#   EXPSCHED_AGENT_TOKEN=… sudo -E bash install-agent.sh --repo owner/expsched --id gpu-box \
#       --scheduler http://10.0.0.5:8000 --service
#   bash install-agent.sh --repo owner/expsched --id laptop --scheduler http://127.0.0.1:8000 --runtime fake
#
# --dry-run печатает команды, ничего не делая. Нужны uv (https://docs.astral.sh/uv/) и, кроме fake, Docker
# или Podman. Токен — EXPSCHED_AGENT_TOKEN или --token-file; в настройках его нет.
set -euo pipefail

VERSION="1.0.0"
REPO="${EXPSCHED_REPO:-}"
ID=""
SCHEDULER=""
RUNTIME="docker"
TOKEN_FILE=""
SERVICE=0
DRY=0

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
fail() { echo "install-agent: $*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then printf '+ %s\n' "$*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --id) ID="$2"; shift 2 ;;
    --scheduler) SCHEDULER="$2"; shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --service) SERVICE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
done

[ -n "$REPO" ] || fail "--repo owner/name: where the release is (or EXPSCHED_REPO)"
[ -n "$ID" ] || fail "--id: the agent id, as in the agent field of cluster workers"
[ -n "$SCHEDULER" ] || fail "--scheduler: the scheduler address, e.g. http://10.0.0.5:8000"
[[ "$ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]] || fail "--id: letters, digits, '_', '.', '-'"
case "$RUNTIME" in docker|podman|fake) ;; *) fail "--runtime: docker, podman or fake" ;; esac
command -v uv >/dev/null || [ "$DRY" = 1 ] || fail "uv is needed: https://docs.astral.sh/uv/getting-started/installation/"
if [ "$SERVICE" = 1 ] && [ "$(id -u)" != 0 ] && [ "$DRY" = 0 ]; then fail "--service needs root (sudo -E)"; fi

WHEEL="https://github.com/${REPO}/releases/download/v${VERSION}/expsched-${VERSION}-py3-none-any.whl"
OWNER="$(printf '%s' "${REPO%%/*}" | tr '[:upper:]' '[:lower:]')"
IMAGE="ghcr.io/${OWNER}/expsched-workload:${VERSION}"

if [ "$SERVICE" = 1 ]; then
  PREFIX=/opt/expsched CONF=/etc/expsched STATE=/var/lib/expsched-agent
else
  PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/expsched" CONF="${XDG_CONFIG_HOME:-$HOME/.config}/expsched"
  STATE="$PREFIX/agent"
fi

echo "expsched agent $VERSION: $ID → $SCHEDULER ($RUNTIME), into $PREFIX"
run uv venv --allow-existing "$PREFIX/.venv"
run uv pip install --python "$PREFIX/.venv" "$WHEEL"

# Встроенная нагрузка: образ выпуска под именем, которое агент ждёт по умолчанию
if [ "$RUNTIME" != fake ]; then
  run "$RUNTIME" pull "$IMAGE"
  run "$RUNTIME" tag "$IMAGE" expsched-workload:local
fi

run install -d -m 0750 "$CONF"
if [ -z "$TOKEN_FILE" ]; then
  TOKEN_FILE="$CONF/agent.token"
  if [ -n "${EXPSCHED_AGENT_TOKEN:-}" ]; then
    if [ "$DRY" = 1 ]; then echo "+ write the token to $TOKEN_FILE"; else
      (umask 077; printf '%s\n' "$EXPSCHED_AGENT_TOKEN" > "$TOKEN_FILE")
    fi
  else
    echo "warning: no EXPSCHED_AGENT_TOKEN; put the scheduler's token into $TOKEN_FILE" >&2
  fi
fi

CONFIG="[agent]
id = \"$ID\"
scheduler = \"$SCHEDULER\"
runtime = \"$RUNTIME\"
state = \"$STATE\"
token_file = \"$TOKEN_FILE\"
log_format = \"json\"
max_containers = 4
allow_images = []
"
if [ "$DRY" = 1 ]; then printf '+ write %s:\n%s' "$CONF/agent.toml" "$CONFIG"; else
  printf '%s' "$CONFIG" > "$CONF/agent.toml"
fi

if [ "$SERVICE" = 1 ]; then
  id -u expsched >/dev/null 2>&1 || run useradd --system --home "$STATE" expsched
  run chgrp expsched "$CONF/agent.toml" "$TOKEN_FILE"
  run chmod 0640 "$CONF/agent.toml" "$TOKEN_FILE"
  UNIT="https://raw.githubusercontent.com/${REPO}/v${VERSION}/containers/agent/expsched-agent.service"
  run curl -fsSL -o /etc/systemd/system/expsched-agent.service "$UNIT"
  [ "$RUNTIME" = podman ] && run sed -i 's/^SupplementaryGroups=docker$/SupplementaryGroups=/' \
    /etc/systemd/system/expsched-agent.service
  run systemctl daemon-reload
  run systemctl enable --now expsched-agent
  echo "running: journalctl -u expsched-agent -f"
else
  run "$PREFIX/.venv/bin/python" -m expsched.agent check --config "$CONF/agent.toml"
  echo "start: $PREFIX/.venv/bin/python -m expsched.agent --config $CONF/agent.toml"
fi
