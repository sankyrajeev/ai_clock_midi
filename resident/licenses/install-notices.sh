#!/usr/bin/env bash
# Puts the open-source notices, the license texts and the complete source code of
# the installed QLC+ in ~/Lumiere/Licenses on the Resident. Run by install.sh;
# can also be run on its own:  bash resident/licenses/install-notices.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
QLC_VERSION="${QLC_VERSION:-5.3.0}"
SRC="$HOME/src/qlcplus"
DEST="$HOME/Lumiere/Licenses"

mkdir -p "$DEST/source"
cp "$REPO/resident/licenses/THIRD-PARTY-NOTICES.md" "$DEST/"
rm -rf "$DEST/texts" && cp -r "$REPO/resident/licenses/texts" "$DEST/texts"
cp "$REPO/resident/build-qlcplus5.sh" "$DEST/source/"

ARCHIVE="$DEST/source/qlcplus-$QLC_VERSION-source.tar.gz"
if [ -s "$ARCHIVE" ]; then
    echo "QLC+ $QLC_VERSION source already saved."
elif [ -d "$SRC/.git" ] && git -C "$SRC" rev-parse -q --verify "refs/tags/QLC+_$QLC_VERSION" >/dev/null; then
    # the exact source QLC+ was built from (fetches any files the clone skipped)
    git -C "$SRC" archive --format=tar.gz --prefix="qlcplus-$QLC_VERSION/" -o "$ARCHIVE" "QLC+_$QLC_VERSION"
    echo "Saved the QLC+ $QLC_VERSION source: $ARCHIVE"
else
    echo "WARNING: the QLC+ source isn't in $SRC; build QLC+ first (resident/build-qlcplus5.sh)." >&2
fi
echo "Open-source notices are in $DEST"
