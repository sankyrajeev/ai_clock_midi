#!/usr/bin/env bash
# Starts QLC+ 5 on the Resident's virtual screen (display :1, which you can see
# from the Mac with Screen Sharing; the window manager makes it fill the
# screen), with its web page on and the show opened if there is one.
# --no3d: the Pi has no graphics card for the 3D view.
SHOW="${1:-}"
ARGS=(--web --no3d)
if [ -n "$SHOW" ] && [ -f "$SHOW" ]; then
    ARGS+=(--open "$SHOW")
fi
exec qlcplus5 "${ARGS[@]}"
