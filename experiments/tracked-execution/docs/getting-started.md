# Getting Started

The tracked execution server lets you build scientific visualizations by writing
PyVista pipeline files. An AI agent writes and edits the files; the server
watches them, re-executes on every save, and caches results so only the changed
parts of the pipeline recompute.

---

## Prerequisites

- Python 3.11+
- `pyvista >= 0.44`
- `numpy >= 1.24`
- `watchdog >= 3.0`
- `mcp` (Model Context Protocol package)
- For headless/offscreen rendering: Xvfb (`xvfb-run`)

---

## Installation

```bash
cd experiments/tracked-execution
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install mcp   # MCP package (if not already installed)
```

---

## Starting the Server

**Headless (recommended for agents and CI):**

```bash
xvfb-run -a python -m mcp_server.run --offscreen
```

`xvfb-run` provides a virtual X display required by VTK's OpenGL renderer even
in offscreen mode. Without it, VTK will segfault when it tries to create an
OpenGL context.

**Interactive (opens a live VTK window):**

```bash
python -m mcp_server.run
```

In interactive mode, the VTK window updates in real time as the agent edits
pipeline files. A main-thread event loop pumps VTK events at ~60 fps.

Run the server from the directory where your data files live, or set the
working directory via the `set_working_directory` tool after connecting.

---

## Configuring Claude Code

Add the server to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "tracked-execution": {
      "command": "xvfb-run",
      "args": [
        "-a",
        "/path/to/experiments/tracked-execution/.venv/bin/python",
        "-m", "mcp_server.run",
        "--offscreen"
      ],
      "cwd": "/path/to/your/session/directory"
    }
  }
}
```

Replace `/path/to/experiments/tracked-execution` with the actual path. Set
`cwd` to the directory where your data files live — the server will look for
`.vts`, `.vti`, `.vtk`, `.vtp`, `.vtu`, `.nhdr`, and `.nrrd` files there.

For interactive mode (live window), omit `xvfb-run -a` and `--offscreen`.

---

## First Session: Walkthrough

This walkthrough uses the synthetic dataset included with the library. No
download required.

### Step 1: Generate the synthetic data

```bash
cd /path/to/VisLang/datasets/synthetic
python generate.py
```

This creates a 64x64x64 test volume with `temperature`, `density`, and
`velocity` fields.

### Step 2: Set the working directory

After connecting to the MCP server, call:

```
set_working_directory("/path/to/datasets/synthetic/data")
```

The server responds with a list of data files it found:

```
Working directory set to: /path/to/datasets/synthetic/data
Data files found:
  - synthetic.vti (2.1 MB)
```

### Step 3: Write a pipeline file

Create `view-temp.py` in the working directory:

```python
mesh = read("synthetic.vti")
show(mesh, colormap="viridis")
```

### Step 4: Create a view

```
create_view("view-temp.py")
```

The server executes the pipeline, renders it, and returns a description of the
data:

```
View 'view-temp' created watching view-temp.py

Data: mesh
Type: ImageData
Points: 262,144
Cells: 250,047
Dimensions: (64, 64, 64)
Bounds: (0.0, 63.0, 0.0, 63.0, 0.0, 63.0)
Fields (3):
  temperature: float32, range=[20.1, 980.2]
  density: float32, range=[0.01, 4.99]
  velocity: float32, range=[-9.97, 9.99]

Cache stats: hits=0, misses=1
Pipeline variables: mesh
```

### Step 5: Explore the data

Use `inspect` to query field statistics without modifying the pipeline:

```
inspect("view-temp.py", "
temp = mesh['temperature']
print(f'Temperature range: {temp.min():.1f} - {temp.max():.1f}')
p10 = np.percentile(temp, 10)
p90 = np.percentile(temp, 90)
print(f'10th-90th pct: {p10:.1f} - {p90:.1f}')
")
```

Output:
```
Temperature range: 20.1 - 980.2
10th-90th pct: 118.3 - 862.7
```

### Step 6: Refine the pipeline

Edit `view-temp.py` to threshold and colorize:

```python
mesh = read("synthetic.vti")
hot = mesh.threshold(value=700, scalars="temperature")
surface = hot.extract_surface()
show(surface, scalars="temperature", colormap="inferno",
     scalar_bar_args={"title": "Temperature"})
```

The watcher detects the file change and re-executes automatically. Call
`list_views()` to confirm:

```
Active views:
  view-temp (view-temp.py) — 3 hits, 2 misses, no errors
```

The `mesh` was a cache hit (file unchanged); only `hot` and `surface` recomputed.

### Step 7: Take a screenshot

```
screenshot("view-temp.py")
```

Returns a PNG image of the current render.

### Step 8: Close when done

```
close_view("view-temp.py")
```

---

## Key Rules

**Always specify `scalars=`** in `threshold()`, `contour()`, and similar calls.
Omitting it uses hidden internal state that is not captured in the cache hash,
causing stale cache hits with wrong data:

```python
# Wrong — which field? unknown, may return cached result from a different field
hot = mesh.threshold(value=700)

# Right — explicit field is hashed
hot = mesh.threshold(value=700, scalars="temperature")
```

**Use `inspect` for data exploration**, not `print()` in the pipeline file. The
pipeline re-executes on every file save — exploratory prints would fire every
time and clutter the output.

**Display parameter changes are free.** Changing `colormap`, `opacity`, `clim`,
or `scalar_bar_args` in `show()` does not recompute any mesh data.

---

## Further Reading

- [mcp-reference.md](mcp-reference.md) — Full reference for all MCP tools
- [pipeline-reference.md](pipeline-reference.md) — Pipeline file namespace, patterns, and whitelisted operations
- [architecture.md](architecture.md) — How caching, proxies, and the DAG work
