#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs run
CONFIG_FILE="config/bridge.local.env"

command -v uv >/dev/null 2>&1 || {
  echo "[PixelPetBridge] uv not found. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 127
}

if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
  echo "[PixelPetBridge] Loaded config from $CONFIG_FILE"
else
  echo "[PixelPetBridge] No local config found at $CONFIG_FILE"
fi

if [[ -f run/pixelpet-bridge.pid ]]; then
  if kill -0 "$(cat run/pixelpet-bridge.pid)" 2>/dev/null; then
    echo "[PixelPetBridge] Already running (pid=$(cat run/pixelpet-bridge.pid))"
    exit 0
  else
    rm -f run/pixelpet-bridge.pid
  fi
fi

LOG_FILE="logs/pixelpet-bridge.$(date +%Y%m%d_%H%M%S).log"
nohup uv run pixelpet-bridge >"$LOG_FILE" 2>&1 &
echo $! > run/pixelpet-bridge.pid

echo "[PixelPetBridge] Started (pid=$(cat run/pixelpet-bridge.pid))"
echo "[PixelPetBridge] Log: $LOG_FILE"
