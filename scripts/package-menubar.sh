#!/bin/bash
# Build unsigned dist/grip-menubar-unsigned-*.zip (Grip.app + README).
# Not signed / not notarized — Gatekeeper will warn. Not a Developer ID DMG.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/grip" ]]; then
  exec "$ROOT/.venv/bin/grip" package-menubar "$@"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m grip package-menubar "$@"
elif command -v grip >/dev/null 2>&1; then
  exec grip package-menubar "$@"
else
  echo "grip not found. From repo root: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,menubar]'" >&2
  exit 1
fi
