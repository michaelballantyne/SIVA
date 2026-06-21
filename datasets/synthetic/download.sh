#!/bin/bash
# Generate a synthetic 64x64x64 VTK ImageData dataset.
# Unlike the wildfire dataset this doesn't download anything --
# it creates the file locally using VTK's Python bindings.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Error: venv Python not found at $VENV_PYTHON" >&2
    echo "Create it first: python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
    exit 1
fi

cd "$SCRIPT_DIR"

OUTPUT="data/output.vti"

if [ -f "$OUTPUT" ]; then
    echo "Already have $OUTPUT"
else
    echo "Generating synthetic dataset ..."
    "$VENV_PYTHON" generate.py
fi

echo "Done. Files in $(pwd)/data/"
