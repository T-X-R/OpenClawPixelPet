#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[PixelPetAll] Starting PixelPet..."
./scripts/start.sh

# Give the local REST API a moment to come up before starting the bridge.
sleep 2

echo "[PixelPetAll] Starting bridge..."
./scripts/start_bridge.sh

echo "[PixelPetAll] Startup complete"
