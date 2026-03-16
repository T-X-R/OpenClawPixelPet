#!/usr/bin/env bash
set -euo pipefail

# PixelPet start script
# - Uses uv to run the app inside the project environment
# - Writes logs to ./logs/

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs

# If uv isn't installed, fail with a helpful message.
command -v uv >/dev/null 2>&1 || {
  echo "[PixelPet] uv not found. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 127
}

# Start in background via nohup so it survives terminal close.
# PID is saved to ./run/pixelpet.pid
mkdir -p run

if [[ -f run/pixelpet.pid ]]; then
  if kill -0 "$(cat run/pixelpet.pid)" 2>/dev/null; then
    echo "[PixelPet] Already running (pid=$(cat run/pixelpet.pid))"
    exit 0
  else
    rm -f run/pixelpet.pid
  fi
fi

LOG_FILE="logs/pixelpet.$(date +%Y%m%d_%H%M%S).log"

nohup uv run pixelpet >"$LOG_FILE" 2>&1 &
echo $! > run/pixelpet.pid

echo "[PixelPet] Started (pid=$(cat run/pixelpet.pid))"
echo "[PixelPet] Log: $LOG_FILE"
