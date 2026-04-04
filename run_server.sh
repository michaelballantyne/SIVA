#!/bin/bash
# VisLang MCP server launcher
# Creates venv if needed, installs deps, and runs the server
cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..." >&2
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install deps if marker is missing or requirements changed
MARKER="$VENV_DIR/.deps_installed"
if [ ! -f "$MARKER" ] || [ requirements.txt -nt "$MARKER" ]; then
    echo "Installing dependencies..." >&2
    pip install -q -r requirements.txt
    touch "$MARKER"
fi

exec python -m vislang.server "$@"
