# Grammar of 3D Visualization: LLM Interaction Design

*Supplement — April 10, 2026*

How would a GoG-inspired grammar change the LLM interaction model? This
matters because VisLang's primary user interface is conversational AI,
not direct hand-coding.

---

## The current interaction pattern

Today's VisLang workflow:

```
Human: "Show me where it's hottest"
  LLM: calls get_statistics("data", "theta")       → learns range is [289, 2019]
  LLM: calls suggest_isosurface("data", "theta")   → gets [500, 800, 1200]
  LLM: calls suggest_opacity("data", "theta")      → gets opacity curve
  LLM: writes a pipeline file with threshold + show (15+ keyword args)
  LLM: calls set_pipeline("view-main.py")
  LLM: calls screenshot()
Human: "Make the fire more transparent"
  LLM: reads the pipeline, finds the opacity_function, modifies it
  LLM: rewrites the file, calls set_pipeline again
```

**Problems with this pattern:**
1. The suggest→paste→build cycle requires 3-4 tool calls before the first
   render. Each tool call has latency.
2. The LLM must manage 15+ keyword arguments correctly on `show()`.
   Mistakes (wrong field name, range outside data bounds) are common and
   require another full cycle to fix.
3. Modifying a single visual property (opacity) requires rewriting the
   entire pipeline file and re-executing.
4. The LLM has no way to express "like the last version but with this
   one change" — it must produce the complete new spec.

## How the grammar changes this

### Fewer tool calls via "auto" defaults

The grammar can internalize the suggest→paste cycle:

```python
bonsai = data("bonsai.vti")

show(bonsai | rep_volume("density"),
     encode(color=scale_color("density", "auto", "terrain"),
            opacity=scale_opacity("density", "auto"),
            legend="Density"))
```

`"auto"` means: query the data (equivalent of suggest_scalar_range,
suggest_opacity) and use the result. The grammar system calls the same
query functions internally during compilation. The LLM writes one
pipeline file and calls set_pipeline once.

This collapses the 3-4 tool call setup into 1. If the auto defaults are
good, the first render is useful; the LLM refines from there.

### Structured modification via encoding objects

With the current DSL, changing the colormap means finding the `lut="hot"`
argument buried in a show() call with 12 other arguments, changing it,
and rewriting the file. With grammar encoding objects:

```python
# Version 1
fire_color = scale_color("theta", [500, 2000], "hot")
fire_opacity = scale_opacity("theta", [(500,0),(800,0.05),(1200,0.3),(2000,0.6)])

scene = layer(
    show(fire | where("theta", between=[500, 2000]) | rep_volume("theta"),
         encode(color=fire_color, opacity=fire_opacity, shade=True)),
    ...
)
```

To change only the colormap, the LLM changes one line:
```python
fire_color = scale_color("theta", [500, 2000], "inferno")  # was "hot"
```

The rest of the pipeline is untouched. This is a smaller, safer edit that
is less likely to accidentally break something. The diff is meaningful:
"changed colormap from hot to inferno."

### Better error recovery

When a grammar pipeline fails, the error can reference grammar concepts:

```
Error in layer[0], rep_volume("theta"):
  Scale range [500, 2000] does not overlap with field "theta" range [289, 2019].
  → The scale range is valid but the threshold 'where' removed all data.
  → Try: remove 'where("theta", between=[500, 2000])' and let the
    opacity transfer function handle the selection.
```

This tells the LLM exactly what went wrong and suggests a fix. The
current error: "vtkSmartVolumeMapper: no input" gives no actionable
information.

### Incremental refinement via encoding edits

The grammar's encoding separation enables a new MCP tool pattern:
instead of rewriting the entire pipeline file, the LLM could call a tool
that modifies just the encoding:

```
# Hypothetical tool: modify encoding without touching data/transform/rep
set_encoding("fire_volume", color=scale_color("theta", [500, 2000], "inferno"))
```

This would be much faster than full pipeline re-execution because only
the mapper/actor properties change — no VTK filter re-execution needed.
The grammar's structural separation is what makes this possible: because
encoding is a separate concern, it can be modified independently.

This connects to the "reconciler" item in the backlog — the grammar
makes reconciliation tractable because the spec is structured enough to
diff meaningfully.

## MCP tool design for the grammar

### Tools that stay as tools (query layer)

These are about understanding the data, not about building the
visualization:
- `describe_data()` — what fields exist, their types and ranges
- `get_statistics(node, field)` — detailed stats for a specific field
- `get_histogram(node, field)` — distribution shape
- `sample_points(...)` — point probing
- `screenshot()` — visual feedback
- `profile(...)` — line probe

These stay as MCP tools. They inform the LLM's grammar writing but are
not part of the grammar itself.

### Tools that become grammar elements

These are currently MCP tools but would be better expressed in the grammar:
- `set_colormap()` → edit `scale_color()` in the pipeline
- `set_opacity()` → edit `encode(opacity=...)` in the pipeline
- `set_background()` → edit `scene.view(background=...)` in the pipeline
- `toggle_visibility()` → comment out a `show()` layer
- `annotate()` → add an `annotate()` form to the grammar

The VISION.md already proposes removing mutation tools in favor of
pipeline edits. The grammar makes this natural because the pipeline
structure clearly separates the concerns that mutation tools currently
target.

### Tools that bridge grammar and data

New tools the grammar enables:
- `set_pipeline(file)` — stays, but now compiles a grammar spec
- `suggest_encoding(field)` → returns a grammar-syntax encoding string
  that the LLM can paste directly
- `validate_pipeline(file)` → pre-execution validation: check field
  names, range overlap, representation compatibility. Returns structured
  errors without executing.
- `diff_pipeline(v1, v2)` → structured diff between two versions,
  expressed in grammar terms ("changed: scale_color range from [0,200]
  to [50,150]; added: rep_outline layer")

## The "auto" pattern in depth

The `"auto"` pattern is the grammar's most LLM-friendly feature. Let me
explore how far it can go:

```python
# Fully auto — for first-pass exploration
show(data | rep_volume("density"),
     encode(color="auto", opacity="auto"))
# Grammar queries data, picks colormap based on field type,
# builds opacity from histogram shape

# Partially auto — LLM knows what it wants for color but not opacity
show(data | rep_volume("density"),
     encode(color=scale_color("density", [20, 200], "terrain"),
            opacity="auto"))

# Fully specified — for refined visualization
show(data | rep_volume("density"),
     encode(color=scale_color("density", [20, 200], "terrain"),
            opacity=scale_opacity("density", [(0,0),(50,0.2),(200,0.8)])))
```

This creates a spectrum from exploration to precision:
1. `"auto"` for first look
2. Auto with overrides for partial specification
3. Full specification for publication-quality

The auto resolution happens at compile time (when set_pipeline is called),
so the LLM can inspect the resolved values by reading the status output.
This means auto is transparent — the LLM learns what values were chosen
and can override them in the next iteration.

### Auto for representations

```python
# "Show me the fire front"
fire | where("theta", above=500) | rep_isosurface("theta", at="auto")
# Grammar calls suggest_isosurface internally

# "Show me the flow pattern"
fire | derive("velocity", from_components=["u","v","w"]) \
     | rep_streamlines("velocity", seeds="auto")
# Grammar uses field statistics to place seeds in interesting regions
```

### Auto for camera

```python
scene.view(camera="auto")
# Grammar calls the equivalent of suggest_camera("overview")

scene.view(camera="front")
# Named preset cameras
```

## Implications for context window management

A key practical concern: LLM context windows are finite. Current VisLang
sessions accumulate screenshots (base64 images) and verbose tool results.
The grammar helps in several ways:

1. **Fewer tool calls per iteration.** Auto defaults + pipeline-as-spec
   means less back-and-forth, less accumulated context.
2. **Smaller pipeline edits.** Changing one `scale_color()` line instead
   of rewriting the whole file means less context spent on the diff.
3. **Structured status output.** Instead of dumping all array names and
   node stats after every set_pipeline, report only what changed:
   "Pipeline v7: 4 layers, all ok. Changed: scale_color range."
4. **Encoding-only updates skip re-execution.** If only visual properties
   changed, no need for VTK filter re-execution or full status dump.

## Summary

The grammar's most important interaction-design contribution is
**collapsing multi-step tool-call sequences into single pipeline edits**.
The suggest→paste→build cycle becomes write→auto-resolve→render. The
encoding separation enables targeted modifications instead of full
rewrites. Error messages reference grammar concepts instead of VTK
internals. And the "auto" pattern lets the LLM explore quickly and
refine progressively.

This is not just a cleaner syntax — it is a fundamentally more efficient
interaction model for conversational visualization.
