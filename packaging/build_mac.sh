#!/bin/bash
# Builds "Lumiere Link.app" and a .dmg. Runs on macOS (GitHub Actions or your own Mac).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(cat VERSION)
ARCH=$(uname -m)
# Name the download after the kind of Mac it is for, not the CPU code name.
if [ "$ARCH" = "arm64" ]; then MAC="Apple-Silicon-M1-M2-M3-M4"; else MAC="Intel"; fi

python3 -m venv build/venv
build/venv/bin/python -m pip install -q --upgrade pip
build/venv/bin/python -m pip install -q -r requirements.txt -r packaging/requirements-app.txt

# App icon: 1024px PNG -> .icns
ICONSET=build/AppIcon.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/brand/app-icon-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/brand/app-icon-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o build/AppIcon.icns

build/venv/bin/pyinstaller --noconfirm --clean --distpath build/dist --workpath build/work packaging/LumiereLink.spec

APP="build/dist/Lumiere Link.app"
if [ -n "${DEVELOPER_ID:-}" ]; then
  # Signed with the Developer ID certificate (Apple Developer Program), so macOS
  # opens it without warnings once it has also been notarized below.
  codesign --force --deep --options runtime --timestamp \
    --entitlements packaging/entitlements.plist --sign "$DEVELOPER_ID" "$APP"
  codesign --verify --deep --strict "$APP"
else
  # Ad-hoc signature: enough for Apple-silicon Macs to run it, but macOS warns.
  codesign --force --deep --sign - "$APP"
fi

# Disk image with a shortcut to Applications
STAGE=build/dmg; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="build/Lumiere-Link-${VERSION}-${MAC}.dmg"
hdiutil create -volname "Lumière Link" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null

if [ -n "${DEVELOPER_ID:-}" ] && [ -n "${APPLE_ID:-}" ]; then
  codesign --force --timestamp --sign "$DEVELOPER_ID" "$DMG"
  # Apple checks the app for malware and returns a ticket; stapling it to the
  # disk image lets macOS verify it even offline.
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$DMG"
  spctl --assess --type open --context context:primary-signature -v "$DMG"
fi
echo "Built $DMG"
