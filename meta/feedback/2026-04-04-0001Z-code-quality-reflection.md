# Code Quality Reflection -- 2026-04-04

## 1. Codebase Snapshot

| File | Lines |
|---|---|
| `vislang/server.py` | 1,696 |
| `vislang/queries.py` | 1,266 |
| `vislang/filters.py` | 1,040 |
| `vislang/dsl.py` | 715 |
| `vislang/renderer.py` | 251 |
| `vislang/colormaps.py` | 244 |
| `vislang/__init__.py` | 2 |
| `vislang/__main__.py` | 4 |
| **Source total** | **5,218** |

| Test file | Tests |
|---|---|
| `tests/test_error_paths.py` | 50 |
| `tests/test_integration.py` | 46 |
| `tests/test_sample_points.py` | 29 |
| `tests/test_query_stats.py` | 29 |
| `tests/test_load_convenience.py` | 27 |
| `tests/test_make_vector_curl.py` | 26 |
| `tests/test_line_probe.py` | 25 |
| `tests/test_coordinate_extract.py` | 22 |
| `tests/test_extract_component.py` | 19 |
| `tests/test_describe_data.py` | 16 |
| `tests/test_component_coloring.py` | 11 |
| `tests/test_describe_data_no_pipeline.py` | 10 |
| `tests/test_auto_screenshot.py` | 5 |
| **Test total** | **315** |

Test-to-source ratio: ~6 test lines per 5 source lines.  Good coverage breadth.

---

## 2. Strengths

**Clean DSL design.** `dsl.py` has a coherent `PipelineBuilder` class with a
single `build()` entry point. The namespace-restriction pattern in `interpret()`
(line 634-701) sandboxes user code effectively while exposing a rich API.

**Layered architecture.** The source splits into clear layers: DSL
interpretation (`dsl.py`), VTK filter mechanics (`filters.py`), data queries
(`queries.py`), rendering (`renderer.py`), colormap data (`colormaps.py`), and
MCP protocol glue (`server.py`). Each module has a well-defined responsibility.

**Defensive diagnostics.** `create_vtk_filter()` in `filters.py` (lines
390-437) produces specific diagnostic messages when filters produce empty
output -- checking contour value ranges, threshold overlap, and seed
placement. This is hard-won domain knowledge preserved in code.

**Test quality.** 315 tests exercise real VTK objects (not mocking internals),
which catches actual integration failures. The `conftest.py` auto-starts Xvfb
for headless environments, which is a nice touch.

**Reader caching.** `filters.py` lines 7-11, 271-296 cache VTK readers to
avoid re-reading large files on pipeline rebuild. Simple and effective.

**Rich data description.** `queries.get_rich_field_stats()` provides
percentiles, distribution shape classification, and per-component stats in one
call. This eliminates multiple round-trips that earlier feedback identified as
the biggest exploration bottleneck.

---

## 3. Concerns

### 3.1 Dead code: unreachable return in `queries.py`

`queries.py:1266` has `return "\n".join(lines)` after the `query_stats()`
function already returned on line 1252-1264. This line is unreachable. It
appears to be a leftover from an earlier version that built a `lines` list.

### 3.2 Ghost MCP tool: `load()` referenced but not implemented

The MCP server instructions (server.py lines 49-50, 86) reference a `load()`
tool, and `BACKLOG.md` marks it as done. However, there is no `@mcp.tool()`
decorated `load` function anywhere in `server.py`. The 37 registered tools do
not include `load`. Users following the instructions will get an error. This
is a functional bug, not just a style issue.

### 3.3 `server.py` is 1,696 lines mixing five concerns

`server.py` contains: MCP server setup (lines 1-95), global state management
(lines 97-180), 37 tool handler functions, pipeline execution logic (lines
203-316), and a 125-line get_examples() string literal (lines 1559-1679).
The backlog already flags this (`[~] server.py module split`), but it is now
the largest source file and every new tool makes it worse. Key risks:

- All tool handlers share mutable globals (`_vtk_objects`, `_current_code`,
  `_version`, `_renderer`) without any encapsulation.
- Testing tool handlers requires importing the module, which triggers
  `_parse_args()` and creates a Renderer at import time (lines 40-98).

### 3.4 Repeated node-lookup boilerplate (16 occurrences)

Sixteen MCP tool functions contain this identical pattern:

```python
data = _get_data(node)
if data is None:
    if node:
        return f"Node '{node}' not found. {_available_nodes_hint()}"
    return _available_nodes_hint()
```

This is ~4 lines per tool, 64 lines total. A decorator or helper that returns
early on lookup failure would eliminate duplication and ensure consistent error
messages.

### 3.5 `_scalar_type_map` defined in three places

The mapping from string names ("unsigned_char", "float", etc.) to VTK type
constants is duplicated at:
- `dsl.py:323-332` (inside `raw_source`)
- `filters.py:608-617` (inside `_apply_properties`)

These could diverge silently. Should be a single constant in `filters.py` or
`colormaps.py`.

### 3.6 `_component_name_map` defined in four places

The `{"x": 0, "y": 1, "z": 2}` mapping appears at:
- `filters.py:72` (in `extract_component`)
- `filters.py:959` (in `create_show`)
- `dsl.py:161` (in `PipelineBuilder.extract_component`)
- `server.py:403` (in `extract_component` tool handler)

Each is a local variable. If one changes (e.g., adding "magnitude" -> 3), the
others won't follow. Should be a module-level constant.

### 3.7 `get_statistics()` uses a Python loop over all tuples

`queries.py:315-318` iterates over every tuple in a Python for-loop to compute
mean and std:

```python
for i in range(n):
    v = arr.GetComponent(i, comp) if ncomp > 1 else arr.GetValue(i)
    total += v
    total_sq += v * v
```

On the wildfire dataset (18M points), this takes seconds per field. Meanwhile,
`get_rich_field_stats()` (line 67) uses `vtk_to_numpy` + numpy for the same
computation at near-C speed. `get_statistics` should use the same numpy path,
or better, delegate to `get_rich_field_stats` for scalar fields.

Similarly, `get_histogram()` (lines 351-356) and `get_spatial_extent()` (lines
389-403) iterate over all tuples in Python. Both would benefit from numpy.

### 3.8 `get_statistics()` does not search cell data for available fields

When reporting "field not found" (lines 298-302), `get_statistics()` only lists
point data arrays. It should also list cell data arrays for a complete error
message, consistent with how the same function already searches both point and
cell data for the array itself (lines 294-296).

### 3.9 Inconsistent error return conventions

Query functions in `queries.py` return error strings like `"No data available"`
(10 occurrences). The caller (server.py tool handlers) cannot distinguish
errors from legitimate text results. A more robust pattern would be to raise
exceptions for errors and return strings only for success, or use a
`(result, error)` tuple pattern like `load_file()` already does.

### 3.10 `_create_volume()` is 226 lines (filters.py:685-911)

This function handles: auto-detecting color_by, auto-detecting scalar_range,
deciding whether to resample, choosing sampling dimensions proportionally,
building color transfer functions, building opacity transfer functions,
configuring volume properties (shade, gradient opacity, sample distance),
applying clipping planes, and creating the scalar bar. It should be decomposed
into smaller functions.

### 3.11 `build()` method in `dsl.py` is 197 lines with inline special cases

`PipelineBuilder.build()` (dsl.py:426-623) handles four special vtk_class
sentinels (`_extract_region`, `_extract_component`, `_seeds_near`, plus normal
filters) with nested try/except blocks. The `_seeds_near` handler (lines
501-545) does regex parsing of formatted strings to extract coordinates -- a
fragile pattern that would break if `get_spatial_extent`'s output format
changes. `get_spatial_extent` should return structured data that `_seeds_near`
can use directly.

### 3.12 Module-level side effects at import time

`server.py` runs `_parse_args()` and creates a `Renderer` at import time
(lines 40, 98). This means `import vislang.server` in test code triggers
argparse and VTK window creation. The test files work around this with mocks
(e.g., `test_auto_screenshot.py`), but it limits testability. Server state
should be initialized in `main()`.

### 3.13 `restore_version()` calls `set_pipeline()` with code string, not file

`restore_version()` (server.py:1321) calls `set_pipeline(code)` passing the
code as a string, but `set_pipeline()` expects a file path. This appears to be
a bug -- it would try to open a file named with the pipeline code content.

### 3.14 Duplicate reader-extension maps

`filters.py:441-447` defines `EXT_TO_READER` and `server.py:327-333` defines
a local `readers` dict in `quick_start()`. Both map file extensions to VTK
reader classes but `quick_start` doesn't include `.vtr`. They should share a
single source of truth.

### 3.15 No tests for server.py tool handlers

315 tests exercise `queries.py`, `filters.py`, and `dsl.py` directly, but
there are zero tests that invoke `@mcp.tool()` functions. The MCP protocol
layer, node lookup boilerplate, condition parsing in `query_stats`, and
auto-screenshot logic are all untested. The missing `load()` tool (concern
3.2) would have been caught by even a basic smoke test.

---

## 4. Proposed Backlog Items

### High Priority

1. **Implement the missing `load()` MCP tool** -- The instructions and backlog
   reference it, but it doesn't exist. Users will hit an error immediately.
   Probably 30 lines of code plus a few tests.

2. **Fix unreachable code in `queries.query_stats()`** -- Delete line 1266
   (`return "\n".join(lines)`) which is dead code after the early return on
   lines 1252-1264.

3. **Fix `restore_version()` bug** -- It passes code content to
   `set_pipeline()` which expects a file path. Either write the code to a temp
   file, or refactor `set_pipeline` to accept code directly.

### Medium Priority

4. **Extract node-lookup boilerplate into a decorator** -- Create a
   `@requires_data` decorator or `_get_data_or_error()` helper to eliminate 16
   copies of the 4-line lookup-and-error pattern in server.py.

5. **Consolidate duplicated constants** -- Move `_scalar_type_map` and
   `_component_name_map` to module-level constants in `filters.py` and import
   them where needed. Also unify the reader-extension maps (`EXT_TO_READER`
   in filters.py vs `readers` in server.py:quick_start).

6. **Replace Python loops with numpy in queries.py** -- `get_statistics()`,
   `get_histogram()`, and `get_spatial_extent()` iterate over all tuples in
   Python. Convert to `vtk_to_numpy` + numpy operations for 10-100x speedup
   on large datasets.

7. **Split `server.py` into modules** -- Already on the backlog as `[~]`.
   Suggested split: `server_state.py` (globals, _get_data, _auto_screenshot),
   `tools_query.py` (read-only query tools), `tools_mutate.py`
   (state-changing tools), `tools_meta.py` (list_capabilities, get_examples,
   etc.). Keep `server.py` as the thin entry point.

### Low Priority

8. **Decompose `_create_volume()` and `PipelineBuilder.build()`** -- Both are
   200+ line functions. Break into smaller functions with single
   responsibilities.

9. **Make `_seeds_near` use structured data** -- Instead of regex-parsing the
   formatted string from `get_spatial_extent`, have it call a structured
   version that returns numeric values directly.

10. **Add server-layer test coverage** -- Write tests that exercise `@mcp.tool`
    functions through the MCP handler interface, or at minimum test the
    condition-parsing logic in `query_stats` and the node-lookup boilerplate.

11. **Defer module-level side effects** -- Move `_parse_args()` and `Renderer`
    creation from module level into `main()`, using lazy initialization. This
    would make `import vislang.server` safe for testing.
