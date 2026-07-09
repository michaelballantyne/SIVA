# Running SIVA in a Docker container

This runs **SIVA and the Claude Code CLI fully inside a Docker container**, with
the trame view server published to your machine so you view renders in a normal
browser. The container is an isolation boundary: it gets no host credentials you
don't explicitly hand it, your data is mounted **read-only**, and only the one
trame port is published (to loopback).

Use this when you want real isolation — e.g. exploring data of uncertain
provenance, or keeping AI-driven work away from your host credentials. For
convenience-first local use, running SIVA directly on your machine is simpler;
see the repo `README.md`.

> **Rendering is software (CPU) here.** VTK renders headlessly via Mesa/OSMesa —
> no GPU. That's fine for screenshots and modest interaction, but orbiting large
> grids is laggy. GPU acceleration needs a Linux + NVIDIA host (Docker Desktop on
> macOS cannot pass the Mac GPU through); see [Performance](#performance).

## What's here

| File | Purpose |
| --- | --- |
| `Dockerfile` | The image: SIVA + trame + Node/Claude CLI + software-GL deps. |
| `Dockerfile.dockerignore` | Keeps the build context small (BuildKit uses this in preference to a root `.dockerignore`). |
| `build.sh` | Build the image (context = repo root). |
| `run.sh` | Scaffold a workspace + launch the container. |

## Prerequisites

- Docker (Desktop on macOS/Windows, or Engine on Linux).
- A Claude subscription or API key for the in-container Claude Code login.

## Quick start

```bash
# From the repo root (or anywhere):
deploy/docker/build.sh

# Start it with a workspace dir and a dataset (mounted read-only):
deploy/docker/run.sh ~/siva-work /path/to/output.vts

# Drive it — log in on first run, then ask it to visualize:
docker exec -it siva claude
#   e.g. "Load output.vts and show temperature isosurfaces."

# Open the URL it reports:
open http://localhost:8900/        # macOS  (xdg-open on Linux)

# Tear down when done (your login persists in a named volume):
docker rm -f siva
```

`run.sh` writes two files into your workspace:

- `.mcp.json` — points Claude at the SIVA MCP server with the single-port trame
  flags (`--trame --trame-host 0.0.0.0 --trame-port 8900 --workdir /work`).
- `.claude/settings.local.json` — pre-approves the SIVA MCP server and all its
  tools so the in-container Claude doesn't prompt per tool.

## How it works

- **One container, everything inside it.** SIVA's MCP server is launched by the
  in-container Claude via `.mcp.json`; Claude runs in the container's terminal.
- **Single trame port.** Every view is served by one shared trame server on one
  port, bound `0.0.0.0` *inside* the container and published to `127.0.0.1:8900`
  on your host. `http://localhost:8900/` shows the `main` view;
  `http://localhost:8900/views` lists all views.
- **Software rendering via OSMesa.** The image sets
  `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow`, so VTK renders offscreen
  with no X server. (This is simpler and more reliable than `xvfb-run`, which in
  a minimal container also needs `xauth` and can hang on the GLX path.)

## Security posture

- **Fail-safe credentials.** A plain container shares *nothing* from the host
  unless you mount/pass it. `run.sh` mounts only your workspace and (read-only)
  data — no `~/.ssh`, no cloud credentials. Nothing to disable.
- **Read-only data.** Datasets are mounted `:ro`; the pipeline can't modify or
  delete your source files.
- **One published port, on loopback.** Only `127.0.0.1:8900` is exposed, and
  only to your own machine.
- **In-container file access is confined.** SIVA confines dataset paths to the
  working directory (`/work`); symlinks placed inside it are followed.

What this does **not** do yet: restrict the container's outbound network. A
plain container has unrestricted egress. For a strict egress boundary, add a
firewall (e.g. the allowlist approach from Anthropic's reference devcontainer).

## Config & auth persistence

The image sets `CLAUDE_CONFIG_DIR=/root/.claude`, and `run.sh` mounts a named
volume (`siva-claude-config`) there — so Claude's login, theme, and onboarding
state survive container recreation. Log in once; subsequent `docker exec … claude`
(and even rebuilds) won't prompt again. To reset auth: `docker volume rm
siva-claude-config`.

## Performance

Rendering is CPU-only (Mesa `llvmpipe`). Screenshots and small/medium data are
fine; interactively orbiting large grids (e.g. a ~1 GB structured grid) is
usable but sluggish, because every mouse-move triggers a full-resolution
software render that's then streamed back.

Two ways to improve it:

- **GPU acceleration** — the real fix for large data. Needs a **Linux host with
  an NVIDIA GPU** (`--gpus all` + `NVIDIA_DRIVER_CAPABILITIES=graphics`, VTK's
  EGL backend). Docker Desktop on macOS cannot pass the Mac GPU into a Linux
  container, so this only pays off on a lab workstation / node — the natural
  split is *software container locally, GPU render server on the lab box*.
- **Reduced interactive quality** — trame can drop resolution while you drag and
  snap back when you stop. (Not yet exposed by SIVA; tracked in the backlog.)

## Notes / gotchas

- **Hot reload over bind mounts on Docker Desktop (macOS/Windows).** inotify
  events don't always cross the bind mount, so SIVA's file watcher may not fire
  when you edit `view-*.py` on the host. Driving through the MCP tools (as Claude
  does) is unaffected.
- **`run.sh` regenerates `.mcp.json`** each run (to match `SIVA_PORT`) but only
  writes `.claude/settings.local.json` if absent, so you can customize
  permissions.
- **Change the port** with `SIVA_PORT=9000 deploy/docker/run.sh …`.
