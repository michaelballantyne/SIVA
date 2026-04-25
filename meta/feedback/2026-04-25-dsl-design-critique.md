# DSL design critique — what the week-of-vibecode left behind

Conversation-driven reflection comparing VisLang's DSL to PyVista, then
zooming in on what's probably wrong with the current DSL itself. Most items
are small inconsistencies; a couple are big enough to count as a re-architecture.

## Framing

VisLang's distinctive value over PyVista is **not** the DSL surface. PyVista
matches or beats us on most filter-wrapping work, and is meaningfully better
for human ergonomics (especially vector calculus via NumPy array access).
Our actual differentiators are:

- Deferred graph + serialized `view-*.py` + versioned history
- Aggregated per-node feedback after each build (shape + errors + warnings)
- Domain-specific empty-output diagnostics (currently 3 filter classes deep)
- MCP/session integration (live window, preserved camera, query tools)
- Small, flat, uniformly-shaped surface area for an LLM caller

The DSL itself is a sibling of PyVista, not a generation beyond it. Some of
its specific choices are probably just vibecode artifacts.

## Clear inconsistencies / probable bugs

1. **Two naming conventions in one call.** `contour(input=data, ContourBy="...",
   Isosurfaces=[...])` mixes snake_case wrapper args (`input`, `inside_out`)
   with CamelCase VTK passthrough. Forces the reader (and LLM) to know which
   words are ours vs VTK's. Fix: pick one — either an explicit
   `vtk={"ContourBy": ...}` passthrough surface, or commit to snake_case
   everywhere with an internal mapping. The current state isn't a deliberate
   choice.

2. **`curl`'s signature is the odd one out.** Positional `vector_field`
   instead of `input=`, plus `vector=True/False` toggling output rank. Every
   other filter is `input=`-keyworded. Fix: `input=`, replace bool with
   `mode="vector"|"magnitude"` or split into two functions.

3. **Inconsistent `inside_out` defaults.** `clip` keeps the normal-pointing
   side by default; `clip_sphere` and `clip_box` keep *inside* by default.
   Same flag name, opposite polarity. Pick one.

4. **`extract_grid` and `extract_region` overlap.** Same operation, different
   argument shapes (extent indices vs physical coords). `extract_region` is
   strictly more useful. Drop `extract_grid` or label it explicitly as the
   low-level escape hatch.

5. **Three entry points for "get data in":** `source`, `load`, `raw_source`.
   Could be one with internal dispatch.

6. **Eager validation breaks "all errors at once."** `extract_region` raises
   before build phase; everything else collects errors per node. Defer the
   eager `ValueError`s to build time so failure reporting is uniform.

7. **Empty-output diagnostics are a fragile if/elif chain** (`filters.py:632–702`).
   Should be a registry: `filter_class → diagnostic_fn`. Adding "why is your
   `vtkProbeFilter` empty" should be one new function, not a new branch.

## Probably-wrong opinions

8. **Scene directives mixed with data filters on the same builder.**
   `camera`, `title`, `axes`, `background`, `scene_preset` scatter scene
   state across the script. The data/display split that `show()` takes
   seriously isn't reflected here. Consider a `scene(...)` block or single
   config-dict directive.

9. **`show()` is becoming an argument-bag.** `color_by`, `scalar_range`,
   `lut`, `opacity`, `representation`, `opacity_function`, `component`,
   `scalar_bar`, and growing. Same path PyVista's `add_mesh` is on — flat
   kwargs, no schema, semantics depend on `representation`. Splitting into
   sub-objects (`color=...`, `volume=...`) would scale better and make
   per-representation kwarg applicability obvious.

10. **Property passthrough is silently forgiving.** Typo'd VTK kwargs hit
    `Set<Typo>(...)` and no-op or error opaquely. Cheap fix: in
    `create_vtk_filter`, check `hasattr(vtk_class, "Set" + key)` and raise a
    structured "unknown property `Foo` on `vtkContourFilter`; valid: [...]".
    Probably the highest-leverage afternoon's work on this list.

11. **`make_vector` / `compute_magnitude` defaults are wildfire-specific.**
    `components=("u","v","w")` leaks dataset assumptions into the DSL.
    Defaultless `components=` is more honest.

12. **`show(node, name, ...)` repeat-name semantics are unspecified.** If you
    `show(a, "x", ...)` then `show(b, "x", ...)`, what happens? Replace?
    Stack? Error? Worth nailing down explicitly.

## Bigger architectural reconsiderations

13. **Build the deferred graph on top of PyVista, not raw VTK.** PyVista
    already solves display wiring, NumPy array access, reader dispatch,
    active-array management, implicit-function clipping — most of the heavy
    wrapping VisLang currently does, with more polish than we'll match. Our
    distinctive value (deferred graph, per-node feedback, MCP/session,
    diagnostics) is *additive* to PyVista, not in conflict. The current
    implementation goes to raw VTK because deferred semantics are easier
    to enforce against an inert VTK pipeline than an eager wrapper — but
    PyVista exposes raw VTK underneath, so this is solvable. The win: free
    breadth and ergonomics, smaller maintenance surface, several items
    above (#1, #5, #11) might collapse.

14. **An `exec`-style inspection tool for failed builds.** Sandbox with
    `nodes` (dict of PyVista-wrapped intermediate outputs by name),
    `statuses`, and `np`/`pv`/`vtk`. Replaces the treadmill of adding new
    hardcoded empty-output diagnostics with a general capability. Security
    ceiling is already "agent runs arbitrary Python in this process" via
    `interpret()`, so this is strictly less risky. Pairs naturally with #13.

15. **A "macro" / reusable-subgraph unit.** Today's composition unit is a
    single filter. Common recipes ("extract fire front," "build streamlines
    through a vertical seed line") get re-typed each session. A subgraph
    primitive — Python function taking/returning NodeRefs, surfaced in
    `get_dsl_overview` — would let domain knowledge live as code rather
    than as docstring lore.

## Suggested order if we tackle any of this

1. **#10** — property typo checking. Cheap, immediate diagnostics win,
   doesn't conflict with anything else.
2. **#7** — refactor empty-output diagnostics into a registry. Unblocks
   broader coverage and matches #14 if we go there.
3. **#13** — sketch VisLang-on-PyVista for one filter + `show()`. A small
   prototype tells us whether #1, #5, #11 collapse and whether the bigger
   redesign is worth committing to before we touch the smaller items.

## Caveats

- "PyVista does this better" claims here are based on conversational
  recollection, not a current side-by-side audit. Worth verifying before
  building on #13.
- Some of these are stylistic. The vibecode-era inconsistencies (#1–#6) are
  unambiguously worth fixing; the bigger items (#13–#15) are real choices
  that deserve a design reflection of their own before commitment.
