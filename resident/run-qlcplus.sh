#!/usr/bin/env bash
# Starts QLC+ with no screen attached, its web page on, and the show if there is one.
SHOW="${1:-}"
ARGS=(--web)
# Options differ between QLC+ versions: use the ones this one has.
HELP="$(qlcplus --help 2>&1 || true)"
grep -q -- "--nogui" <<<"$HELP" && ARGS+=(--nogui)
if [ -n "$SHOW" ] && [ -f "$SHOW" ]; then
    ARGS+=(--open "$SHOW")
    grep -q -- "--operate" <<<"$HELP" && ARGS+=(--operate)
fi
exec qlcplus "${ARGS[@]}"
