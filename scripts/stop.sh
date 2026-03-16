#!/usr/bin/env bash
set -euo pipefail

# PixelPet stop script

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f run/pixelpet.pid ]]; then
  echo "[PixelPet] Not running (no pid file)"
  exit 0
fi

PID="$(cat run/pixelpet.pid)"

if ! kill -0 "$PID" 2>/dev/null; then
  echo "[PixelPet] Not running (stale pid file: $PID)"
  rm -f run/pixelpet.pid
  exit 0
fi

echo "[PixelPet] Stopping pid=$PID ..."
kill "$PID"

# wait up to 5s
for _ in {1..50}; do
  if kill -0 "$PID" 2>/dev/null; then
    sleep 0.1
  else
    rm -f run/pixelpet.pid
    echo "[PixelPet] Stopped"
    exit 0
  fi
done

echo "[PixelPet] Still running after 5s, force killing..."
kill -9 "$PID" || true
rm -f run/pixelpet.pid
echo "[PixelPet] Stopped (SIGKILL)"
