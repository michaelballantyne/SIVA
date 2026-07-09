#!/usr/bin/env bash
# Cloud environment setup---installs system deps and Python packages.
set -e

PROJECT_DIR="/home/user/SIVA"

apt-get update -qq && apt-get install -y -qq xvfb

if command -v uv >/dev/null 2>&1; then
    # Creates .venv/ and installs the project + dev group from uv.lock.
    (cd "$PROJECT_DIR" && uv sync --quiet)
else
    # Fallback for environments without uv.
    VENV_DIR="$PROJECT_DIR/.venv"
    if [ ! -f "$VENV_DIR/bin/activate" ]; then
        rm -rf "$VENV_DIR"
        python -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    # Dev tooling (pytest) so cloud sessions can run the test suite.
    pip install --quiet -e "$PROJECT_DIR" pytest
fi
