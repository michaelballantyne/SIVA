#!/usr/bin/env bash
# Launch the isolated SIVA stack (workload + egress allowlist proxy + view
# forwarder) via docker compose. SIVA and Claude Code run fully inside the
# workload container, which sits on an internal (no-egress) network; its only
# path out is the Squid proxy, which permits HTTPS to Anthropic domains only.
#
# Usage:
#   ./run.sh <workspace-dir> [data-file-or-dir]
#
#   <workspace-dir>    Host dir for SIVA's working files (view-*.py, .siva,
#                      screenshots) + generated Claude/MCP config. Created if
#                      missing; mounted read-write at /work.
#   [data-dir]         Directory of datasets to mount READ-ONLY at /work/data/.
#                      Load a file from it in SIVA as data/<name>.
#
# Env: SIVA_PORT (8900). Auth: subscription login needs nothing; to use an API
# key or alternate endpoint, export ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
# before running (and for an alternate endpoint, add its host to squid.conf).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="$HERE/docker-compose.yml"

WORKSPACE="${1:?usage: run.sh <workspace-dir> [data-file-or-dir]}"
DATA="${2:-}"
PORT="${SIVA_PORT:-8900}"

mkdir -p "$WORKSPACE/.claude"
WS_ABS="$(cd "$WORKSPACE" && pwd)"

# Data is a directory, mounted read-only at /work/data (compose).
if [ -z "$DATA" ]; then
  DATA_DIR="$WS_ABS/.nodata"; mkdir -p "$DATA_DIR"
elif [ -d "$DATA" ]; then
  DATA_DIR="$(cd "$DATA" && pwd)"
else
  echo "error: the data argument must be a directory (mounted read-only at" >&2
  echo "       /work/data); '$DATA' is not one. Pass its containing directory," >&2
  echo "       e.g. $(dirname "$DATA")" >&2
  exit 1
fi

# MCP config: single-port trame server bound 0.0.0.0 (so the forwarder reaches
# it), workdir /work. Regenerated each run to match $PORT.
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

# Pre-approve the SIVA MCP server + tools (written only if absent, so you can edit).
if [ ! -f "$WS_ABS/.claude/settings.local.json" ]; then
  cat > "$WS_ABS/.claude/settings.local.json" <<'JSON'
{
  "permissions": { "allow": ["mcp__SIVA", "mcp__SIVA__*"] },
  "enabledMcpjsonServers": ["SIVA"]
}
JSON
fi

# Env for compose (auto-loaded from .env beside the compose file, so `up`,
# `exec`, and `down` all see the same values).
cat > "$HERE/.env" <<ENV
SIVA_WORKSPACE=$WS_ABS
SIVA_DATA=$DATA_DIR
SIVA_PORT=$PORT
ENV

echo "Building + starting the SIVA stack…"
docker compose -f "$COMPOSE" up -d --build

cat <<EOF

SIVA stack is up (egress restricted to Anthropic; data read-only; login persisted).
  Drive:  docker compose -f "$COMPOSE" exec siva claude
  View:   http://localhost:$PORT/            (once a view is created)
  Data:   load it in SIVA as  data/<filename>
  Logs:   docker compose -f "$COMPOSE" logs proxy   # egress audit trail
  Stop:   docker compose -f "$COMPOSE" down
EOF
