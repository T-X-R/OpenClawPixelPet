#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[PixelPetAll] Stopping bridge..."
./scripts/stop_bridge.sh

echo "[PixelPetAll] Stopping PixelPet..."
./scripts/stop.sh

echo "[PixelPetAll] Shutdown complete"
