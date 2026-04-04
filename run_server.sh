#!/bin/bash
# VisLang MCP server launcher
cd "$(dirname "$0")"

python -c "import vislang" 2>/dev/null || {
    echo "Error: VisLang is not installed." >&2
    echo "Run:  pip install -e /path/to/VisLang" >&2
    exit 1
}

exec python -m vislang.server "$@"
