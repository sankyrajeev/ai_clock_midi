#!/bin/bash
# Double-click in Finder to start the Pro DJ Link -> Lightkey MIDI clock.
# First run sets up its Python environment automatically (needs internet once).
# Extra options can be added to ARGS, e.g. ARGS=(--link) or ARGS=(--source 1).
ARGS=()

cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "First run: setting up (this takes a minute)..."
    python3 -m venv .venv && .venv/bin/python3 -m pip install -q -r requirements.txt || {
        echo; echo "Setup failed. If macOS asked to install developer tools, accept, then run this again."
        read -r -p "Press Enter to close."; exit 1; }
fi

echo "Pro DJ Link -> Lightkey clock. In Lightkey pick MIDI Clock input \"ProLink Clock\"."
echo "Close this window (or press Ctrl-C) to stop."
echo
.venv/bin/python3 prolink_sync.py "${ARGS[@]}" "$@"
read -r -p "Stopped. Press Enter to close."
