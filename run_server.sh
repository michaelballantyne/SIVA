#!/bin/bash
# VisLang MCP server launcher
# Activates the venv and runs the server
cd "$(dirname "$0")"
if [ -d "venv" ]; then
    source venv/bin/activate
fi
exec python -m vislang.server "$@"
