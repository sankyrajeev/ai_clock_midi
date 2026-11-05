#!/usr/bin/env bash
# Builds and installs QLC+ 5 (the QML version, program name "qlcplus5") from
# QLC+'s own source code, at the same version as the Mac. There is no ready-made
# QLC+ 5 package for the Pi. Takes 1-2 hours on a Pi 4; only needed once, or
# when changing version. Called by install.sh; can also be run on its own:
#
#   bash resident/build-qlcplus5.sh            # version 5.3.0
#   QLC_VERSION=5.3.1 bash resident/build-qlcplus5.sh
set -euo pipefail
QLC_VERSION="${QLC_VERSION:-5.3.0}"
SRC="$HOME/src/qlcplus"

if command -v qlcplus5 >/dev/null && qlcplus5 --version 2>/dev/null | grep -q "$QLC_VERSION"; then
    echo "QLC+ $QLC_VERSION is already installed."
    exit 0
fi

echo "Building QLC+ $QLC_VERSION. This takes 1-2 hours; the Pi can stay as it is meanwhile."
# QLC+ 4 from Debian shares fixture files with QLC+ 5: remove it to avoid clashes.
sudo apt-get remove -y -q qlcplus qlcplus-data 2>/dev/null || true
sudo apt-get install -y -q git cmake g++ make pkg-config libxml2-utils shared-mime-info \
    libasound2-dev libfftw3-dev libftdi1-dev libsndfile1-dev libudev-dev libusb-1.0-0-dev \
    qt6-base-dev qt6-multimedia-dev qt6-serialport-dev qt6-websockets-dev qt6-declarative-dev \
    qt6-3d-dev qt6-tools-dev qt6-tools-dev-tools qt6-svg-dev qt6-l10n-tools \
    qml6-module-qtquick qml6-module-qtquick-layouts qml6-module-qtquick-controls \
    qml6-module-qtquick-templates qml6-module-qtquick-dialogs qml6-module-qtquick-window \
    qml6-module-qtqml-models qml6-module-qtqml-workerscript qml6-module-qtmultimedia \
    qml6-module-qt3d-core qml6-module-qt3d-render qml6-module-qt3d-extras qml6-module-qt3d-input \
    qml6-module-qt3d-logic qml6-module-qtquick-scene3d libqt6svg6

if [ ! -d "$SRC/.git" ]; then
    mkdir -p "$(dirname "$SRC")"
    git clone --filter=blob:none https://github.com/mcallegari/qlcplus.git "$SRC"
fi
cd "$SRC"
git fetch -q --tags origin
git checkout -q "QLC+_$QLC_VERSION"
rm -rf build && mkdir build && cd build
cmake -Dqmlui=ON -DCMAKE_BUILD_TYPE=Release ..
# 3 jobs: a 4 GB Pi 4 runs out of memory with 4 on QLC+'s bigger files.
make -j3
sudo make install
sudo ldconfig
echo "QLC+ $QLC_VERSION installed: $(command -v qlcplus5)"
