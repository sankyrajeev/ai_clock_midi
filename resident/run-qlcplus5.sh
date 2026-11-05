#!/usr/bin/env bash
# Starts QLC+ 5 on the Resident's virtual screen (display :1, which you can see
# from the Mac with Screen Sharing), full screen, with its web page on and the
# show opened if there is one. --no3d: the Pi has no graphics card for 3D view.
SHOW="${1:-}"
ARGS=(--web --no3d --fullscreen)
if [ -n "$SHOW" ] && [ -f "$SHOW" ]; then
    ARGS+=(--open "$SHOW")
fi
exec qlcplus5 "${ARGS[@]}"
