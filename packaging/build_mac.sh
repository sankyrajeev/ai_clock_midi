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
# Ad-hoc signature: required for Apple-silicon Macs to run the app at all.
codesign --force --deep --sign - "$APP"

# Disk image with a shortcut to Applications
STAGE=build/dmg; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="build/Lumiere-Link-${VERSION}-${MAC}.dmg"
hdiutil create -volname "Lumière Link" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "Built $DMG"
