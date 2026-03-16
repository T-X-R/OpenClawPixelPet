#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f run/pixelpet-bridge.pid ]]; then
  echo "[PixelPetBridge] Not running (no pid file)"
  exit 0
fi

PID="$(cat run/pixelpet-bridge.pid)"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "[PixelPetBridge] Not running (stale pid file: $PID)"
  rm -f run/pixelpet-bridge.pid
  exit 0
fi

echo "[PixelPetBridge] Stopping pid=$PID ..."
kill "$PID"
for _ in {1..50}; do
  if kill -0 "$PID" 2>/dev/null; then
    sleep 0.1
  else
    rm -f run/pixelpet-bridge.pid
    echo "[PixelPetBridge] Stopped"
    exit 0
  fi
done

echo "[PixelPetBridge] Still running after 5s, force killing..."
kill -9 "$PID" || true
rm -f run/pixelpet-bridge.pid
echo "[PixelPetBridge] Stopped (SIGKILL)"
