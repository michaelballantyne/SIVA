# Tracked Execution Beyond Visualization

The core of tracked execution — content-addressed caching of a proxied
computation DAG — is domain-independent. This document sketches how it
could generalize.

## The abstraction

```python
# The universal pattern:
proxy = tracked_load("input_data")    # cache by path + mtime
result = proxy.transform(params)       # cache by op + input_hash + params
derived = result.compute(more_params)  # cache by op + input_hash + params
output(derived)                        # record desired output
```

Every `.method()` call:
1. Hashed by `(type, method, input_hash, args)`
2. Checked against cache
3. Executed on miss, cached on hit
4. Result wrapped in a new proxy

The DAG + GC ensures only the current pipeline's results stay in memory.

## Domain applications

### Data processing (pandas/polars)

```python
# Agent writes a data pipeline file:
df = read_csv("sales.csv")
filtered = df.query("revenue > 1000")
grouped = filtered.groupby("region").agg({"revenue": "sum"})
show_table(grouped)
```

The whitelist covers pandas DataFrame methods. `read_csv` is the
tracked entry point. Changing the query re-executes from that point;
the CSV read is cached.

**What's different from viz:** Output is a table, not a 3D render.
The "reconciler" diffs table outputs instead of VTK actors. The
"screenshot" equivalent is a table rendering or chart.

### Image processing (PIL/OpenCV)

```python
img = read_image("photo.jpg")
resized = img.resize((1024, 768))
enhanced = resized.filter(ImageFilter.SHARPEN)
cropped = enhanced.crop((100, 100, 900, 668))
show(cropped)
```

The whitelist covers PIL Image methods. Changing the crop box only
re-executes the crop; resize and sharpen are cached.

### ML feature engineering

```python
df = read_parquet("features.parquet")
normalized = df.apply(lambda col: (col - col.mean()) / col.std())
encoded = pd.get_dummies(normalized, columns=["category"])
selected = encoded.drop(columns=["id", "timestamp"])
output(selected)
```

Same pattern. The agent iterates on feature engineering steps; the
expensive parquet read and normalization stay cached when only the
feature selection changes.

## What the core library needs to generalize

### Already domain-independent
- `TrackedProxy` — wraps any object
- `DAG` — cache store + GC
- `dispatch` — whitelist check + hash + cache
- `stable_hash` — works on any serializable arguments
- `_dag_call` — shared cache-check/store pattern

### Domain-specific (would be swapped per domain)
- `whitelist.py` — different allowed methods per domain
- `executor.py` — different namespace (no `show`, `read` points at
  different readers)
- `reconciler.py` — different output diffing (tables vs actors vs images)
- `mcp_server/server.py` — different MCP tools

### The generalization structure

```
tracked_core/          # Domain-independent
  proxy.py
  dispatch.py          # DAG, _dag_call, stable_hash
  
tracked_viz/           # PyVista visualization
  whitelist.py
  executor.py
  reconciler.py
  
tracked_data/          # Pandas data processing (hypothetical)
  whitelist.py
  executor.py
  reconciler.py

tracked_image/         # Image processing (hypothetical)
  whitelist.py
  executor.py
  reconciler.py
```

Each domain is a thin layer (~500 lines) on top of the core (~400 lines).

## Open questions

1. **Is the caching model right for all domains?** Visualization pipelines
   are pure-ish (same input → same output). Pandas has more mutation
   (inplace=True, setitem). Would need stricter immutability enforcement.

2. **Is the proxy overhead acceptable?** For numpy arrays (large, few
   operations), the proxy overhead is negligible. For pandas DataFrames
   (many small method calls), it might be noticeable. Benchmark needed.

3. **Does the whitelist approach scale?** Pandas has hundreds of methods.
   Auto-generating the whitelist and categorizing safe vs dangerous is
   harder than for PyVista/numpy.

4. **What about stateful transformations?** Some operations (random
   sampling, shuffle) are non-deterministic. The cache would serve stale
   results. Same issue as VTK — need to document and enforce.

## Relationship to existing tools

- **Marimo notebooks** — reactive cell re-execution based on dataflow.
  Similar motivation (don't re-run everything). Different mechanism
  (cell-level, not operation-level).
- **DVC (Data Version Control)** — caches pipeline stage outputs by
  input hash. Same hash-consing idea but at file level, not operation
  level.
- **Fugue** — abstracts pandas/Spark behind a common interface. Different
  goal (portability, not caching).
- **Hamilton** — DAG-based feature engineering. Similar DAG structure
  but explicit (functions as nodes) rather than implicit (proxy interception).

The tracked execution approach is closest to DVC's model but at a finer
granularity: individual method calls instead of pipeline stages, with
transparent proxy interception instead of explicit DAG declaration.
