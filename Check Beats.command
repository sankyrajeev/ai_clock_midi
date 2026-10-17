#!/bin/bash
# Double-click in Finder to check beats are arriving from the CDJs / DJM.
# First run sets up its Python environment automatically (needs internet).
ARGS=(--monitor)

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

echo "Showing beat packets. Press play on a deck; lines should appear below."
echo "Close this window (or press Ctrl-C) to stop."
echo
.venv/bin/python3 prolink_sync.py "${ARGS[@]}" "$@"
read -r -p "Stopped. Press Enter to close."
