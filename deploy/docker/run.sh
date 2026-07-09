#!/usr/bin/env bash
# Launch the SIVA container: SIVA + Claude Code run fully inside it; the trame
# view server is published to host loopback so you view renders in your browser.
#
# Usage:
#   ./run.sh <workspace-dir> [data-file-or-dir]
#
#   <workspace-dir>   Host dir for SIVA's working files (view-*.py, .siva,
#                     screenshots) and the generated Claude/MCP config. Created
#                     if missing. Mounted read-write at /work.
#   [data-file-or-dir] Optional dataset to mount READ-ONLY: a single file lands
#                     at /work/<name>; a directory lands at /work/data/.
#
# Env overrides: SIVA_IMAGE (siva:latest), SIVA_CONTAINER (siva), SIVA_PORT (8900).
#
# After it starts:
#   docker exec -it siva claude      # drive it; ask it to load + show your data
#   open http://localhost:8900/      # the live trame view (once one exists)
#   docker rm -f siva                # tear down (login volume survives)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${1:?usage: run.sh <workspace-dir> [data-file-or-dir]}"
DATA="${2:-}"
NAME="${SIVA_CONTAINER:-siva}"
IMAGE="${SIVA_IMAGE:-siva:latest}"
PORT="${SIVA_PORT:-8900}"

mkdir -p "$WORKSPACE/.claude"
WS_ABS="$(cd "$WORKSPACE" && pwd)"

# MCP config: single-port trame server, bound 0.0.0.0 so the published port
# reaches it, workdir = /work. Regenerated each run to match $PORT.
cat > "$WS_ABS/.mcp.json" <<JSON
{
  "mcpServers": {
    "SIVA": {
      "command": "/opt/venv/bin/python",
      "args": [
        "-m", "siva.server",
        "--trame",
        "--trame-host", "0.0.0.0",
        "--trame-port", "$PORT",
        "--workdir", "/work"
      ]
    }
  }
}
JSON

# Pre-approve the SIVA MCP server + all its tools so the in-container Claude
# doesn't prompt per tool. (Written only if absent, so you can customize it.)
if [ ! -f "$WS_ABS/.claude/settings.local.json" ]; then
  cat > "$WS_ABS/.claude/settings.local.json" <<'JSON'
{
  "permissions": {
    "allow": ["mcp__SIVA", "mcp__SIVA__*"]
  },
  "enabledMcpjsonServers": ["SIVA"]
}
JSON
fi

DATA_MOUNT=()
if [ -n "$DATA" ]; then
  ABS="$(cd "$(dirname "$DATA")" && pwd)/$(basename "$DATA")"
  if [ -d "$ABS" ]; then
    DATA_MOUNT=(-v "$ABS:/work/data:ro")
  else
    DATA_MOUNT=(-v "$ABS:/work/$(basename "$ABS"):ro")
  fi
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  -p "127.0.0.1:$PORT:$PORT" \
  -v "$WS_ABS:/work" \
  "${DATA_MOUNT[@]}" \
  -v "siva-claude-config:/root/.claude" \
  "$IMAGE" >/dev/null

echo "SIVA container '$NAME' is up (image: $IMAGE)."
echo "  Drive:  docker exec -it $NAME claude"
echo "  View:   http://localhost:$PORT/   (once a view is created)"
echo "  Stop:   docker rm -f $NAME"
