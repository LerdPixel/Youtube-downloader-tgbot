#!/bin/sh
# Double-click in Finder, or run ./start_web.command from a terminal.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv-web/bin/python ]; then
    python3 -m venv .venv-web
fi
if ! .venv-web/bin/python -c 'import fastapi, uvicorn, yt_dlp, yt_dlp_ejs' >/dev/null 2>&1; then
    .venv-web/bin/python -m pip install -r requirements-web.txt
fi
exec .venv-web/bin/python web.py "$@"
