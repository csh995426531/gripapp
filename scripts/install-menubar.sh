#!/bin/bash
# Local (unsigned) Grip menu-bar install: refresh apps/Grip.app, optional
# ~/Applications symlink + LaunchAgent. Not a notarized DMG.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/grip" ]]; then
  exec "$ROOT/.venv/bin/grip" install-menubar "$@"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m grip install-menubar "$@"
elif command -v grip >/dev/null 2>&1; then
  exec grip install-menubar "$@"
else
  echo "grip not found. From repo root: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,menubar]'" >&2
  exit 1
fi
