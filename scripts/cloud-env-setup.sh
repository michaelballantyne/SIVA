#!/usr/bin/env bash
# Cloud environment setup---installs system deps and Python packages.
set -e

PROJECT_DIR="/home/user/VisLang"
VENV_DIR="$PROJECT_DIR/.venv"

apt-get update -qq && apt-get install -y -qq xvfb

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    rm -rf "$VENV_DIR"
    python -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --quiet -e "$PROJECT_DIR"
