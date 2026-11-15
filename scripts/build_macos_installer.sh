#!/usr/bin/env bash
# Build a standalone Grip.app + DMG (drag to Applications).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
PIP="${ROOT}/.venv/bin/pip"
VERSION="$("$PY" -c 'from grip import __version__; print(__version__)')"

echo "==> deps"
"$PIP" install -q -e ".[menubar]" "pyinstaller>=6.0"

echo "==> pyinstaller"
rm -rf build/pyinstaller dist/Grip.app dist/Grip
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller \
  packaging/Grip.spec

APP="$ROOT/dist/Grip.app"
if [[ ! -d "$APP" ]]; then
  echo "ERROR: dist/Grip.app missing" >&2
  exit 1
fi

# Thin wrapper: dedicated GRIP_HOME for the packaged app (CLI uses ~/.grip).
MACOS="$APP/Contents/MacOS"
if [[ -f "$MACOS/Grip" && ! -f "$MACOS/Grip.bin" ]]; then
  mv "$MACOS/Grip" "$MACOS/Grip.bin"
fi
cat > "$MACOS/Grip" <<'EOF'
#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export GRIP_HOME="${GRIP_HOME:-$HOME/.grip-app}"
mkdir -p "$GRIP_HOME"
# Prefer the real Resources copy — Frameworks/certifi is often a symlink and
# OpenSSL inside the frozen binary can fail on relative symlink paths.
for _cand in \
  "$DIR/../Resources/certifi/cacert.pem" \
  "$DIR/../Frameworks/certifi/cacert.pem"
do
  if [[ -f "$_cand" ]]; then
    _resolved="$(cd "$(dirname "$_cand")" && pwd)/$(basename "$_cand")"
    export SSL_CERT_FILE="$_resolved"
    export REQUESTS_CA_BUNDLE="$_resolved"
    break
  fi
done
exec "$DIR/Grip.bin" "$@"
EOF
chmod +x "$MACOS/Grip" "$MACOS/Grip.bin"

# Optional Developer ID + notarization (Deferred without Apple credentials).
#   export GRIP_CODESIGN_IDENTITY="Developer ID Application: … (TEAMID)"
#   export GRIP_NOTARY_APPLE_ID="you@example.com"
#   export GRIP_NOTARY_TEAM_ID="TEAMID"
#   export GRIP_NOTARY_PASSWORD="app-specific-password"   # or keychain profile via GRIP_NOTARY_PROFILE
CODESIGN_IDENTITY="${GRIP_CODESIGN_IDENTITY:-}"
if [[ -n "$CODESIGN_IDENTITY" ]]; then
  echo "==> codesign ($CODESIGN_IDENTITY)"
  codesign --force --deep --options runtime --timestamp \
    --sign "$CODESIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
else
  echo "==> codesign skipped (set GRIP_CODESIGN_IDENTITY to sign)"
fi

echo "==> DMG"
STAGE="$ROOT/build/dmg-stage"
DMG="$ROOT/dist/Grip-${VERSION}-macOS.dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/Grip.app"
ln -s /Applications "$STAGE/Applications"

# UDZO compressed DMG; double-click → drag Grip.app to Applications
hdiutil create \
  -volname "Grip ${VERSION}" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$DMG"

if [[ -n "$CODESIGN_IDENTITY" ]]; then
  echo "==> codesign DMG"
  codesign --force --timestamp --sign "$CODESIGN_IDENTITY" "$DMG"
fi

if [[ -n "${GRIP_NOTARY_PROFILE:-}" ]]; then
  echo "==> notarize (keychain profile: $GRIP_NOTARY_PROFILE)"
  xcrun notarytool submit "$DMG" --keychain-profile "$GRIP_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
elif [[ -n "${GRIP_NOTARY_APPLE_ID:-}" && -n "${GRIP_NOTARY_TEAM_ID:-}" && -n "${GRIP_NOTARY_PASSWORD:-}" ]]; then
  echo "==> notarize (Apple ID)"
  xcrun notarytool submit "$DMG" \
    --apple-id "$GRIP_NOTARY_APPLE_ID" \
    --team-id "$GRIP_NOTARY_TEAM_ID" \
    --password "$GRIP_NOTARY_PASSWORD" \
    --wait
  xcrun stapler staple "$DMG"
else
  echo "==> notarize skipped (set GRIP_NOTARY_PROFILE or GRIP_NOTARY_APPLE_ID/TEAM_ID/PASSWORD)"
fi

echo "==> done"
ls -lh "$APP" "$DMG"
echo "Install: open $DMG → drag Grip.app into Applications"
