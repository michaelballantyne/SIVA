# How to Try the Tracked Execution MCP Server

## Quick Start (5 minutes)

### 1. Install dependencies

```bash
cd experiments/tracked-execution
pip install -e ".[dev]"
pip install mcp watchdog trame trame-vtk trame-vuetify
```

### 2. Configure Claude Code

Add to your Claude Code MCP settings (`.claude/mcp_settings.json`):

```json
{
  "mcpServers": {
    "tracked-viz": {
      "command": "xvfb-run",
      "args": ["-a", "python", "-m", "mcp_server.run", "--offscreen"],
      "cwd": "/path/to/experiments/tracked-execution"
    }
  }
}
```

For interactive VTK windows (requires display):
```json
{
  "mcpServers": {
    "tracked-viz": {
      "command": "python",
      "args": ["-m", "mcp_server.run"],
      "cwd": "/path/to/experiments/tracked-execution"
    }
  }
}
```

For Trame (browser-based):
```json
{
  "mcpServers": {
    "tracked-viz": {
      "command": "xvfb-run",
      "args": ["-a", "python", "-m", "mcp_server.run", "--trame", "--port", "8080"],
      "cwd": "/path/to/experiments/tracked-execution"
    }
  }
}
```

### 3. Start a session

```
You: "Let's explore the wildfire simulation data"

Claude: [calls set_working_directory("/path/to/datasets/wildfire/data")]
        [writes view-fire.py with: mesh = read("output.30000.vts") / show(mesh)]
        [calls create_view("view-fire.py")]
        → Gets back: 18.3M points, fields: theta, u, v, w, O2, ...

Claude: [calls inspect("view-fire.py", "print(mesh['theta'].min(), mesh['theta'].max())")]
        → 298.75  1183.94

Claude: [edits view-fire.py to threshold on theta > 400]
        → Watcher re-executes; read() is cached, only threshold runs

Claude: [calls screenshot("view-fire.py")]
        → Gets the rendered image
```

## What the Agent Can Do

### MCP Tools (6 total)

| Tool | What it does |
|------|-------------|
| `set_working_directory(path)` | Point at your data directory |
| `create_view(pipeline_file)` | Watch a pipeline file, return data description |
| `inspect(pipeline_file, code)` | Query cached data (read-only) |
| `screenshot(pipeline_file)` | Capture current render |
| `list_views()` | See all views with status |
| `close_view(pipeline_file)` | Clean up a view |

### Pipeline File Syntax

The agent writes standard PyVista code:

```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
show(surface, colormap="inferno")
```

Available names: `read`, `show`, `np`, `vtk_escape`, `pv`, `print`.

### Caching Behavior

- First run: everything executes (~3s for wildfire data)
- Same code again: instant (fully cached)
- Change threshold: read cached, threshold re-runs (~0.5s)
- Change colormap: everything cached (~0ms)

## Example Sessions

### Wildfire exploration
```
1. set_working_directory("/path/to/datasets/wildfire/data")
2. Write view-fire.py: mesh = read("output.30000.vts") / show(mesh)
3. create_view("view-fire.py")
4. inspect: check theta range → 298-1184K
5. Edit pipeline: threshold(value=500, scalars="theta")
6. screenshot → see the fire region
7. Edit: change to value=700 for hot core only
8. screenshot → see refined view (read cached, fast!)
```

### CT scan segmentation
```
1. set_working_directory("/path/to/datasets/bonsai/data")
2. Write view-bone.py: mesh = read("bonsai.vti") / show(mesh)
3. create_view("view-bone.py")
4. inspect: check density distribution
5. Edit: threshold(value=[30, 145], scalars="density") for wood
6. Edit: contour(isosurfaces=[50, 100], scalars="density") for isosurfaces
```

## Troubleshooting

- **"not in the whitelist"** — use `vtk_escape(proxy, func)` for unlisted methods
- **"always specify scalars="** — the system enforces this for caching correctness
- **Slow first run** — large files take time to read; cached on second run
- **No display** — use `--offscreen` mode with `xvfb-run`
