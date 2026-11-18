#!/usr/bin/env bash
# Starts QLC+ 5 on the Resident's virtual screen (display :1, which you can see
# from the Mac with Screen Sharing; the window manager makes it fill the
# screen), with its web page on and the show opened if there is one.
# --no3d: the Pi has no graphics card for the 3D view.
SHOW="${1:-}"
# QLC+ only looks for MIDI ports when it starts, so wait (up to 30 s) until
# Lumière Link's ports are there; the show's buttons listen to them.
for _ in $(seq 60); do
    grep -q "Lumiere Cues" /proc/asound/seq/clients 2>/dev/null && break
    sleep 0.5
done
ARGS=(--web --no3d)
if [ -n "$SHOW" ] && [ -f "$SHOW" ]; then
    ARGS+=(--open "$SHOW")
fi
exec qlcplus5 "${ARGS[@]}"
