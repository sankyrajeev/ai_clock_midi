#!/bin/bash
# Double-click in Finder: runs a pretend booth (2 x CDJ-3000X + DJM-V10) on this Mac and connects to it.
# Everything works as with real gear: clock, phrase cues, waveform analysis, deck changes.
# Add --simulate-dropout 60 to ARGS to pull the pretend network cable for 10 s after a minute.
# First run sets up its Python environment automatically (needs internet).
# Extra options can be added to ARGS, e.g. ARGS=(--link) or ARGS=(--source 1).
ARGS=(--simulate-booth)

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

echo "Simulated booth: deck 2 plays a track with rekordbox phrases, deck 1 mixes in one without. Lightkey works as with real gear."
echo "Close this window (or press Ctrl-C) to stop."
echo
.venv/bin/python3 prolink_sync.py "${ARGS[@]}" "$@"
read -r -p "Stopped. Press Enter to close."
