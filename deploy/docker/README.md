# Running SIVA in a Docker container (example deployment)

This directory is **one example** of running SIVA and the Claude Code CLI
together inside Docker, with the render viewable in your browser and outbound
network restricted to the Anthropic API. It is a starting point, not a blessed
or guaranteed-secure configuration — whether it fits your threat model, and how
to harden it for your environment, is the deploying user's responsibility. See
the main project README for the security discussion.

> **Rendering here is software (CPU), no GPU.** Fine for screenshots and modest
> interaction; orbiting large grids is sluggish. GPU acceleration needs a Linux
> + NVIDIA host — Docker Desktop on macOS can't pass the Mac GPU through. See
> [Performance](#performance).

## What's here

| File | Purpose |
| --- | --- |
| `Dockerfile` | The workload image: SIVA + trame + Node/Claude CLI + software-GL (Mesa/OSMesa) deps. |
| `Dockerfile.dockerignore` | Keeps the build context small (BuildKit prefers this over a repo-root `.dockerignore`). |
| `docker-compose.yml` | The 3-service stack (workload + egress proxy + view forwarder). |
| `squid.conf` | The egress allowlist (Anthropic domains). |
| `run.sh` | Scaffolds a workspace and brings the stack up with one command. |
| `build.sh` | Build just the image (context = repo root). |

## Prerequisites

- Docker with Compose v2 (`docker compose …`).
- A Claude subscription or API key for the in-container Claude Code login.

## Quick start

```bash
deploy/docker/run.sh ~/siva-work /path/to/output.vts

# Drive it — log in on first run, then ask it to visualize:
docker compose -f deploy/docker/docker-compose.yml exec siva claude
#   e.g. "Load data/output.vts and show temperature isosurfaces."

# Open the URL it reports:
open http://localhost:8900/          # macOS  (xdg-open on Linux)

# Tear down (your login persists in a named volume):
docker compose -f deploy/docker/docker-compose.yml down
```

`run.sh` writes into your workspace: `.mcp.json` (points Claude at the SIVA MCP
server with the single-port trame flags) and `.claude/settings.local.json`
(pre-approves the SIVA tools so you aren't prompted per call). Your dataset is
mounted read-only at `/work/data/`, so load it as `data/<filename>`.

## Architecture (why three containers)

```
                    ┌───────────────────────── host ─────────────────────────┐
  browser ──▶ 127.0.0.1:8900 ──▶ viewproxy(socat) ──▶ siva:8900  (trame view)
                                        │  (web+internal nets)      ▲
                                        └───────────────────────────┘
                                                                    │  HTTPS_PROXY
   siva  ── internal net only, no route out ── proxy(squid) ──▶ api.anthropic.com
   (SIVA + Claude)                              (allowlist)     (everything else denied)
```

- **`siva`** — the workload (SIVA MCP server + Claude Code). It sits on an
  **internal** Docker network with *no route to the internet*, so it can only
  reach out through the proxy and can't be reconfigured to bypass it (there's no
  gateway to add — enforcement is the network topology, not in-container rules).
- **`proxy`** (Squid) — a forward proxy, dual-homed on the internal + web
  networks, that allows HTTPS only to the domains in `squid.conf`. `siva` uses
  it via `HTTPS_PROXY`. HTTPS is filtered by the `CONNECT` hostname (no TLS
  interception / no CA needed).
- **`viewproxy`** (socat) — forwards the published host port to `siva`'s trame
  port over raw TCP (so websockets work). Needed only because a container on an
  internal-only network can't publish a port itself.

Rendering uses **OSMesa** (`VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow`)
— software OpenGL with no X server (simpler and more reliable in a container
than `xvfb-run`).

## What the setup does mechanically

Descriptively (not a security guarantee — see the top note):

- The workload gets **only** the host resources you pass it: the workspace
  (read-write) and your dataset (read-only). No `~/.ssh`, cloud creds, or other
  host paths are mounted.
- Outbound traffic is **allowlisted to Anthropic domains**; because the workload
  is on an internal network, other destinations (and attempts to bypass the
  proxy) fail closed. `docker compose … logs proxy` is the egress audit trail.
- Only `127.0.0.1:8900` is published, to your own machine.
- SIVA additionally confines dataset file paths to the working directory
  (symlinks placed inside it are followed).

Things this does **not** do (know your context): it is a container, not a VM —
it shares the host kernel, so it is not a defense against a kernel-level exploit
(that tier is microVM/gVisor). The one allowed endpoint is still a network
channel. The base image, dependencies, and Docker itself are also trust
surface. Evaluate all of this against your own requirements.

## Egress: changing the allowlist / using an alternate endpoint

Edit the `allowed` ACL in `squid.conf` (leading dot = domain + subdomains). To
use an alternate Anthropic-compatible endpoint, add its host there **and**
export `ANTHROPIC_BASE_URL` before `run.sh` (it's forwarded to Claude). On a
Linux host you can also/instead enforce egress at the host layer (a
`DOCKER-USER` iptables rule) or in the cloud (security group) — those live
outside the container entirely.

## Auth & config persistence

The image sets `CLAUDE_CONFIG_DIR=/root/.claude`, mounted as a named volume, so
Claude's login/theme/onboarding survive restarts and rebuilds — log in once. To
reset: `docker volume rm siva_siva-claude-config`. Subscription login needs
nothing extra; to use an API key instead, export `ANTHROPIC_API_KEY` before
`run.sh` (forwarded to the container).

## Performance

Rendering is CPU-only (Mesa `llvmpipe`). Screenshots and small/medium data are
fine; interactively orbiting a large (~1 GB) grid is usable but sluggish —
every mouse-move is a full-resolution software render streamed back.

- **GPU acceleration** is the real fix for large data, on a **Linux + NVIDIA
  host** (`--gpus all` + `NVIDIA_DRIVER_CAPABILITIES=graphics`, VTK's EGL
  backend). Not possible under Docker Desktop on macOS. Natural split: software
  container locally, GPU render server on the lab box.
- **Reduced interactive quality** (trame `interactive_ratio`) would help the CPU
  case; not yet exposed by SIVA (tracked in the backlog).

## Notes / gotchas

- **Hot reload over bind mounts on Docker Desktop (macOS/Windows):** inotify
  events don't always cross the mount, so SIVA's watcher may not fire on
  host-side edits to `view-*.py`. Driving through the MCP tools (as Claude does)
  is unaffected.
- **Change the port:** `SIVA_PORT=9000 deploy/docker/run.sh …`.
- **`run.sh` regenerates `.mcp.json`** each run (to match the port) but writes
  `.claude/settings.local.json` only if absent, so you can customize it.
