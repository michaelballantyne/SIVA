#!/bin/bash
# VisLang MCP server launcher
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate the venv if one exists in the VisLang directory
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

python -c "import vislang" 2>/dev/null || {
    echo "Error: VisLang is not installed." >&2
    echo "Run:" >&2
    echo "  cd $SCRIPT_DIR" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  source .venv/bin/activate" >&2
    echo "  pip install -e ." >&2
    exit 1
}

exec python -m vislang.server "$@"
