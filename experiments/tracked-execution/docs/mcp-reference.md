# MCP Tool Reference

This page documents every tool exposed by the tracked execution MCP server.
All tools communicate via the Model Context Protocol over stdio.

---

## set_working_directory

Set the working directory for all file operations.

**Must be called before creating any views.** Cannot be changed after the
first view has been created.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Absolute path to the working directory. |

### Returns

A string confirming the directory and listing any data files found (`.vts`,
`.vti`, `.vtk`, `.vtp`, `.vtu`, `.nhdr`, `.nrrd`), with file sizes in MB.

### Example

```
set_working_directory("/home/user/sessions/wildfire")
```

```
Working directory set to: /home/user/sessions/wildfire
Data files found:
  - output.30000.vts (1,089.2 MB)
```

### Errors

- `Error: directory does not exist: <path>` — the path doesn't exist or isn't a directory.
- `Error: cannot change working directory after views have been created.` — call this before any `create_view`.

---

## create_view

Create a visualization view that watches a pipeline file.

The pipeline file is executed immediately. The server watches the file for
changes and re-executes automatically whenever it is saved. The view name is
derived from the filename (e.g., `fire.py` becomes view `fire`).

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `pipeline_file` | `str` | Path to the pipeline `.py` file, relative to the working directory or absolute. |

### Returns

A string containing:
- Confirmation of the view name and file being watched
- A description of the first mesh loaded (type, points, cells, dimensions, bounds, fields with ranges)
- Cache stats (hits and misses) from the initial execution
- Names of pipeline variables bound to mesh objects
- Any captured `print()` output from the pipeline
- Any runtime error (the view is still created so the watcher can pick up fixes)

### Example

```
create_view("fire.py")
```

```
View 'fire' created watching fire.py

Data: mesh
Type: StructuredGrid
Points: 12,648,000
Cells: 12,400,480
Bounds: (0.0, 2000.0, 0.0, 2000.0, 0.0, 500.0)
Fields (6):
  theta: float32, range=[298.1, 1842.3]
  u: float32, range=[-15.2, 22.7]
  v: float32, range=[-18.1, 19.4]
  w: float32, range=[-3.2, 18.9]

Cache stats: hits=0, misses=1
Pipeline variables: mesh
```

### Errors

- `Error: call set_working_directory first.` — working directory not set.
- `Error: file not found: <path>` — the pipeline file doesn't exist.
- `Error: view 'X' already exists. Close it first or use a different filename.` — duplicate view name.
- `Error: syntax error in pipeline file — view not created.\n<SyntaxError>` — the file has a Python syntax error; fix it and call `create_view` again.
- Runtime errors (NameError, AttributeError, etc.) are reported but the view is still created, so the watcher can pick up fixes.

---

## inspect

Run a read-only inspection snippet against a view's cached data.

All named pipeline variables from the last execution are available (meshes,
arrays, filtered results). Use `print()` to emit output. The snippet cannot
modify the pipeline or trigger rendering.

This is the right tool for data exploration: checking field ranges, computing
percentiles, counting points in filtered regions, examining mesh properties.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `pipeline_file` | `str` | The pipeline file name identifying the view. |
| `code` | `str` | Python snippet to execute. Use `print()` for output. |

### Returns

The captured `print()` output from the snippet. If no output was produced,
returns `(no output — use print() to see results)`.

### Example

```
inspect("fire.py", "
temp = mesh['theta']
print(f'Range: {temp.min():.1f} - {temp.max():.1f}')
p95 = np.percentile(temp, 95)
print(f'95th percentile: {p95:.1f}')
print(f'Hot cells: {fire.n_points} points after threshold')
")
```

```
Range: 298.1 - 1842.3
95th percentile: 1204.7
Hot cells: 84,231 points after threshold
```

### Available names in inspect snippets

- All pipeline variables bound to `TrackedProxy` values (meshes, arrays, filtered results)
- `np` — tracked numpy namespace
- `print()` — captured output
- Standard Python builtins (`int`, `float`, `str`, `len`, `range`, etc.)

### Not available in inspect snippets

- `read()`, `show()`, `screenshot()` — raise descriptive errors if called
- `import` statements
- `open()`, `os`, `sys`, or any I/O

### Errors

- `Error: no view 'X'. Call create_view('X.py') first.` — view doesn't exist.
- `Error in inspection code:\n<ExceptionType>: <message>` — snippet raised an exception.
- `NameError: name 'X' is not defined. Pipeline variables available: [...]` — variable not in scope; includes hint listing what is available.

---

## screenshot

Capture a screenshot of a view's current render.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `pipeline_file` | `str` | The pipeline file name identifying the view. |

### Returns

A PNG image of the current render (MCP `Image` type with `format="png"`).

### Example

```
screenshot("fire.py")
```

Returns a PNG image.

### Errors

- `Error: no view 'X'. Call create_view('X.py') first.` — view doesn't exist.

---

## list_views

List all active visualization views.

### Parameters

None.

### Returns

A formatted list of active views, each showing the view name, pipeline
filename, cache stats (hits and misses from the most recent execution), and
any error status.

### Example

```
list_views()
```

```
Active views:
  fire (fire.py) — 12 hits, 3 misses, no errors
  velocity (velocity.py) — 5 hits, 8 misses, error: NameError: name 'u' is not defined
```

### When no views exist

```
No views. Call create_view(pipeline_file) to create one.
```

---

## close_view

Close a visualization view and free its resources.

Stops the file watcher, closes the PyVista plotter, and removes the view from
the active views list.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `pipeline_file` | `str` | The pipeline file name identifying the view. |

### Returns

Confirmation string: `View 'X' closed.`

### Example

```
close_view("fire.py")
```

```
View 'fire' closed.
```

### Errors

- `Error: no view 'X'. Use list_views() to see active views.` — view doesn't exist.

## View Names

All tools that take a `pipeline_file` parameter derive the view name from the
file's basename without extension:

| `pipeline_file` | View name |
|-----------------|-----------|
| `fire.py` | `fire` |
| `view-main.py` | `view-main` |
| `/abs/path/velocity.py` | `velocity` |

Both the filename and the derived view name are accepted interchangeably.
