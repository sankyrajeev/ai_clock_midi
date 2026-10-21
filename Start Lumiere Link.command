#!/bin/bash
# Double-click in Finder to start Lumiere Link: MIDI clock + lighting cues for Lightkey, over the DJ Link network.
# Add --cues to ARGS to also use the USB-audio detector for tracks without phrase data.
# First run sets up its Python environment automatically (needs internet).
# Extra options can be added to ARGS, e.g. ARGS=(--link) or ARGS=(--source 1).
ARGS=(--phrases)

cd "$(dirname "$0")" || exit 1

# Set up on first run, and again whenever requirements.txt changes (after a git pull).
if [ ! -x .venv/bin/python3 ] || [ requirements.txt -nt .venv/.installed ]; then
    echo "Setting up (this takes a minute)..."
    { [ -x .venv/bin/python3 ] || python3 -m venv .venv; } \
        && .venv/bin/python3 -m pip install -q -r requirements.txt \
        && touch .venv/.installed || {
        echo; echo "Setup failed. If macOS asked to install developer tools, accept, then run this again."
        read -r -p "Press Enter to close."; exit 1; }
fi

echo "Lumiere Link is running. Lightkey: tempo = MIDI Clock \"Lumiere Clock\"; MIDI-learn cues from \"Lumiere Cues\"."
echo "Close this window (or press Ctrl-C) to stop."
echo
.venv/bin/python3 prolink_sync.py "${ARGS[@]}" "$@"
read -r -p "Stopped. Press Enter to close."
