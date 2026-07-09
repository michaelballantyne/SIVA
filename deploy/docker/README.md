# Running SIVA in a Docker container

An example setup that runs SIVA and the Claude Code CLI inside Docker, serves
the render to your browser, and restricts the container's outbound network to
the Anthropic API. It is a starting point rather than a hardened configuration;
whether it suits your environment is up to you. See the main README for the
security discussion.

Rendering here is software (CPU), with no GPU. That is fine for screenshots and
light interaction but slow for orbiting large grids; see [Performance](#performance).

## Contents

| File | Purpose |
|---|---|
| `Dockerfile` | Workload image: SIVA, trame, the Node/Claude CLI, and software-GL (Mesa/OSMesa) libraries. |
| `docker-compose.yml` | The three-service stack: workload, egress proxy, view forwarder. |
| `squid.conf` | The egress allowlist. |
| `run.sh` | Scaffolds a workspace and starts the stack. |
| `build.sh` | Builds the image alone. |
| `Dockerfile.dockerignore` | Keeps the build context small. |

## Prerequisites

- Docker with Compose v2.
- A Claude subscription or API key for the in-container login.

## Quick start

```bash
deploy/docker/run.sh ~/siva-work /path/to/dataset-dir

# Log in on first run, then ask it to visualize:
docker compose -f deploy/docker/docker-compose.yml exec siva claude
#   e.g. "Load data/output.vts and show temperature isosurfaces."

# Open the URL it prints (xdg-open on Linux):
open http://localhost:8900/

# Stop it; the login volume persists:
docker compose -f deploy/docker/docker-compose.yml down
```

The second argument is a directory; it is mounted read-only at `/work/data/`,
so load a file from it as `data/<filename>`.

## Workspace

The workspace is a host directory you pass to `run.sh`, mounted read-write at
`/work`. It holds the session's working files: the pipeline files the AI writes
(`view-*.py`), the `.siva` scratch directory and version history, screenshots,
and the generated `.mcp.json` and `.claude/settings.local.json`.

Use a dedicated directory; `run.sh` creates it if it does not exist. It is the
one host location the container can write to, so everything in it is readable
and writable from inside the container, including by the AI. Do not point it at
your home directory or a tree that holds other files you care about. Datasets
stay separate: pass a directory as the second argument, mounted read-only at
`/work/data`.

## How it works

Three containers:

- `siva` runs the SIVA MCP server and Claude Code. It is on an internal Docker
  network with no route to the internet, so its only way out is the proxy.
- `proxy` is a Squid forward proxy on both the internal and external networks.
  It allows HTTPS only to the domains in `squid.conf`, matching on the CONNECT
  hostname, so it needs no TLS interception. `siva` reaches it through
  `HTTPS_PROXY`.
- `viewproxy` forwards the published host port to `siva`'s trame port with
  socat. A container on an internal-only network cannot publish a port itself,
  so the forwarder does it; raw TCP passthrough keeps websockets working.

Rendering uses OSMesa (`VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow`),
software OpenGL with no X server. This avoids `xvfb-run`, which needs `xauth` in
a minimal container and can hang on the GLX path.

## What the container can reach

- Only the paths you pass it: the workspace (read-write) and the dataset
  (read-only). No host credentials are mounted.
- Outbound traffic reaches only the domains in `squid.conf`; the internal
  network blocks everything else, including attempts to bypass the proxy.
  `docker compose ... logs proxy` records the requests.
- Only `127.0.0.1:8900` is published.
- SIVA confines dataset paths to the working directory; symlinks placed inside
  it are followed.

This is a container, not a virtual machine: it shares the host kernel and does
not defend against a kernel exploit, which would need a microVM or gVisor. The
base image, dependencies, and Docker itself are also part of what you trust.

## Egress allowlist

Edit the `allowed` ACL in `squid.conf`; a leading dot matches a domain and its
subdomains. For an alternate Anthropic-compatible endpoint, add its host there
and export `ANTHROPIC_BASE_URL` before `run.sh`. On a Linux host you can instead
enforce egress outside the container, with a `DOCKER-USER` iptables rule or a
cloud security group.

## Authentication and config

The image sets `CLAUDE_CONFIG_DIR=/root/.claude`, mounted as a named volume, so
the login, theme, and onboarding survive restarts and rebuilds. Log in once, and
reset with `docker volume rm siva_siva-claude-config`. To use an API key instead
of subscription login, export `ANTHROPIC_API_KEY` before `run.sh`.

## Performance

Rendering is CPU-only (Mesa llvmpipe). Screenshots and small to medium data are
fine; orbiting a large (around 1 GB) grid is usable but slow, since each mouse
movement is a full-resolution software render streamed back.

GPU acceleration is the fix for large data, on a Linux host with an NVIDIA GPU
(`--gpus all`, `NVIDIA_DRIVER_CAPABILITIES=graphics`, and VTK's EGL backend). It
is not available under Docker Desktop on macOS. Reducing the interactive render
quality (trame's `interactive_ratio`) would help the CPU case but is not yet
exposed by SIVA.

## Notes

- On Docker Desktop (macOS and Windows), inotify events do not always cross a
  bind mount, so SIVA's file watcher may miss host-side edits to `view-*.py`.
  Driving through the MCP tools, as Claude does, is unaffected.
- Change the port with `SIVA_PORT=9000 deploy/docker/run.sh ...`.
- `run.sh` regenerates `.mcp.json` each run but writes
  `.claude/settings.local.json` only if it is absent.
