# Security Hardening -- lessons from viznoir

Date: 2026-04-16

This entry comes out of a security review pass on VisLang prompted by the
user asking "is it safe to run this on my machine?" The review was done in
conversation, comparing VisLang against its sibling project viznoir
(`~/code/viznoir`) which covers a similar domain (VTK-based scientific
visualization exposed via MCP) but was built with tighter validation
discipline from the start.

The goal of this entry is not to turn VisLang into viznoir. VisLang has
different goals -- a native VTK interactor window, a richer Python-flavored
pipeline DSL that the LLM writes directly, session-oriented workflows --
and several of viznoir's patterns would undercut those goals if copied
wholesale. The goal is to isolate the specific disciplines viznoir has
that VisLang doesn't, and pick the ones that buy safety without costing
expressivity.

---

## Findings in the current codebase

Severity is rated for the realistic threat model: a single user running
VisLang locally on macOS, where the untrusted party is the LLM (possibly
prompt-injected via a dataset filename, a README it reads, or a poisoned
VISION.md).

### HIGH: `export_standalone(path)` is a write-anywhere primitive

`vislang/server.py` around line 1649. The tool accepts `path` as a raw
string and does `open(path, "w").write(script)` with no validation. A
prompt-injected LLM can write to `~/.ssh/authorized_keys`,
`~/.zshrc`, a launch agent plist, or any other location the user can
write. This is the finding most worth fixing first, because the blast
radius is "silent persistence on the host" rather than "arbitrary read
with limited exfil."

### HIGH: No path validation on `load()` or `set_pipeline()`

`vislang/server.py` around line 429. `os.path.exists(filename)` is not a
scope check -- `/etc/passwd` exists. The MCP tool surface accepts
arbitrary paths with no `Path.resolve().is_relative_to(...)` test. An
LLM can `load("../../../etc/hostname")` or any file readable by the
user's UID.

### HIGH: VTK `FileName=` kwargs bypass all validation

`vislang/dsl.py`. `PipelineBuilder.source()` and `filter()` accept
arbitrary keyword arguments and pass them through to VTK. There is no
inspection of `FileName`-style kwargs. An LLM that reads the DSL
reference (which it does -- `get_dsl_reference` is an MCP tool) can
write:

```python
source("vtkGenericDataObjectReader", FileName="/Users/me/.aws/credentials")
```

VTK will fail to parse the file, but the read attempt succeeds. This is
the gap viznoir doesn't have, because viznoir's DSL is a Pydantic tree
and paths flow through a validator before reaching a VTK reader.

### MEDIUM: `exec(code, namespace)` with a leaky-by-omission sandbox

`vislang/dsl.py` around lines 1979 and 2005. `__builtins__` is set to
`{}` and `open`/`__import__` aren't in the namespace, but the namespace
does include `math`, `print`, and every `PipelineBuilder` method. The
sandbox's guarantee is "nothing we forgot to remove" -- that is not a
guarantee. Determined jailbreak payloads that walk the MRO of any
reachable object (e.g. `(1).__class__.__mro__[1].__subclasses__()`)
can usually reach `subprocess` or `os` in a standard CPython. No tests
in the suite try to break out.

### MEDIUM: MCP tool args are bare strings

Most tools accept `str` parameters with no Pydantic model. Even if
`load()` and `set_pipeline()` got path validation, the pattern of
"parameters are just strings" makes it easy for the next tool to
inherit the same gap. viznoir uses Pydantic models at the tool
boundary and gets validation "for free" on every tool.

### LOW / acknowledged

- Subprocess use is confined to tests (`tests/conftest.py` for Xvfb,
  `test_headless_interactive.py`), all `args` arrays with no
  `shell=True`. Fine.
- No network calls in the runtime path. No hardcoded credentials. No
  `curl | bash` in `scripts/`.
- Dependencies (`pyproject.toml`) are clean: vtk, mcp, numpy,
  matplotlib, pytest. No suspicious pins.
- Dataset loading is VTK-only (no pickle, torch.load, joblib).
- `.vislang/` state directory holds logs and PNGs, no deserialized
  executables.
- VTK reader CVEs exist historically but are a shared risk with
  ParaView and viznoir itself -- not specifically a VisLang problem.

---

## What viznoir does that VisLang should steal

### 1. `VIZNOIR_DATA_DIR`-style path scoping

viznoir has a single env var (`VIZNOIR_DATA_DIR`) that every path in
the codebase is resolved against. When unset, it documents the
behavior as "unrestricted" and leaves the choice to the operator.
When set, every read path is `Path(p).resolve()` and checked with
`is_relative_to(root)` before use.

For VisLang this needs one extension viznoir doesn't bother with:
separate read-scope from write-scope. viznoir's `export_standalone`
equivalent (`preview_3d`, `batch_render`) only writes to an output
directory, so a single scope suffices. VisLang's `export_standalone`
takes an arbitrary destination path, which is the primitive that
needs the tightest leash.

Proposed shape:

```python
# vislang/paths.py
SESSION_ROOT = Path(os.environ["VISLANG_SESSION_DIR"]).resolve()
DATA_ROOTS   = [Path(p).resolve() for p in
                os.environ.get("VISLANG_DATA_DIRS", "").split(":") if p]
OUTPUT_ROOT  = SESSION_ROOT / "outputs"

def safe_read_path(p: str | Path) -> Path:
    r = Path(p).resolve()
    if not any(r.is_relative_to(root) for root in [SESSION_ROOT, *DATA_ROOTS]):
        raise VisLangPathError(f"{p} outside allowed roots")
    return r

def safe_write_path(p: str | Path) -> Path:
    r = Path(p).resolve()
    if not r.is_relative_to(OUTPUT_ROOT):
        raise VisLangPathError(f"write outside {OUTPUT_ROOT}")
    return r
```

Then route every path through the appropriate helper:
- `load(filename)` -> `safe_read_path`
- `set_pipeline(file)` -> `safe_read_path`
- `export_standalone(path)` -> `safe_write_path`
- `FileName=` kwargs inside `PipelineBuilder.source()/filter()` ->
  `safe_read_path` (see next section)

This alone eliminates the three HIGH findings.

### 2. Intercept `FileName=` kwargs at the DSL boundary

viznoir doesn't have this problem because its DSL is a typed tree --
`SourceDef.path: ValidatedPath` is validated by Pydantic before the
compiler ever sees it. VisLang's DSL is free-form Python, so the
check has to happen at runtime, inside `PipelineBuilder.source()` /
`filter()` / `raw_source()`.

The logic: inspect kwargs for keys matching a small set
(`FileName`, `FileNames`, `FilePrefix`, `PatternString`,
lowercase variants). For any match, route the value through
`safe_read_path` before instantiating the VTK object. Reject
kwargs with obviously path-like values that don't match the known
key names (defense in depth).

This is cheap -- ~15 lines -- and closes a gap that
would otherwise persist even after the MCP tool args are locked down.

### 3. Replace `exec(code, namespace)` with an AST-validated evaluator

This is the bigger change and the one that matters most for the
"richer DSL" goal. The current design fights against itself: we want
the LLM to write expressive Python, but we need to prevent it from
reaching the full Python object model. `exec()` with a stripped
`__builtins__` is the worst of both worlds -- it looks safe, but
isn't.

The better shape is explicit: parse the LLM's code with `ast.parse`,
walk the tree, and accept only a deliberately chosen grammar.
Something like:

- **Allow:** top-level assignments to simple names; calls to a known
  set (`source`, `filter`, `threshold`, `clip`, `slice`, `show`,
  builder methods we vet); literal args (numbers, strings, lists,
  tuples, dicts of literals); simple binary ops; `for` over
  `range(<literal>)`; name references to a whitelist (`math`, the
  builder identifier, variables defined earlier in the same script).
- **Reject:** `Import`, `ImportFrom`, `Lambda`, `FunctionDef`,
  `ClassDef`, `Attribute` access on anything outside the whitelist,
  `Subscript` with dunder keys, f-strings containing calls, `yield`,
  `global`, `nonlocal`, `exec`/`eval`/`compile`/`getattr`/`setattr`/
  `globals`/`locals`/`vars`/`type`/`__import__` as Name.
- Then `compile(tree)` and `exec` the validated tree in a minimal
  namespace.

~200-300 lines of `ast.NodeVisitor`. Harder than a stripped-builtins
sandbox; *much* stronger guarantee. It also forces us to be explicit
about what DSL features we support, which is a good design
discipline in its own right -- the grammar becomes the
spec of the DSL.

Pair it with a dedicated "escape attempts" test file: 20-30 payloads
that the validator must reject. Items to include:
`(1).__class__.__mro__[1].__subclasses__()`,
`{}.__class__.__base__.__subclasses__()`,
`getattr(builder, "__globals__")`,
f-strings with embedded calls (`f"{open('/etc/passwd').read()}"`),
list/dict comprehensions calling non-whitelisted functions, lambdas,
nested function defs, `import` in all forms, walrus operator with
side-effecting RHS, `yield from`, `async def`.

### 4. Viznoir's `VIZNOIR_ALLOW_PROGRAMMABLE` escape hatch

viznoir has a `ProgrammableFilter` that accepts arbitrary Python.
It's disabled by default and gated on `VIZNOIR_ALLOW_PROGRAMMABLE=1`.
We should have the same for raw `exec()`: `VISLANG_ALLOW_RAW_PYTHON=1`
bypasses the AST validator. This keeps the escape hatch available
for the "I'm debugging the DSL" case without making it the default.

### 5. Typed MCP tool surface

Even with a free-form DSL inside `set_pipeline`'s body, the MCP tool
boundary is small (~45 tools) and should be rigid. Convert the
bare-string args to Pydantic models with custom types that validate
on deserialization:

```python
class SessionPath(str):
    @classmethod
    def __get_validators__(cls): yield cls.validate
    @classmethod
    def validate(cls, v): return str(safe_read_path(v))
```

`load(filename: SessionPath)` now rejects out-of-scope paths at the
fastmcp layer before the tool handler runs. viznoir leans on this
pattern heavily and it pays off in smaller, more obviously correct
tool implementations.

### 6. Stdout protection (not a security issue but we'll want it)

viznoir's `_protect_stdout()` in its renderer module dups fd 1,
redirects fd 1 to `/dev/null`, and writes JSON-RPC on the preserved
fd. The reason: VTK C code writes warning text and sometimes binary
junk (~20MB in edge cases) directly to fd 1, which corrupts the MCP
protocol stream. VisLang runs offscreen VTK in a process that serves
MCP over stdio, so we'll hit this eventually. ~15 lines, worth
having.

### 7. Singleton render window

Small detail but it matters: viznoir keeps a single
`vtkRenderWindow` instance and reuses it, recreating every 100
renders to bound GPU memory growth. VisLang's interactor goal
probably rules out "recreate every N renders" (would disrupt the UX),
but the *singleton* part -- reuse across calls rather than creating
fresh windows -- is worth adopting. VTK leaks reliably if you keep
instantiating windows.

---

## What viznoir does that VisLang should NOT copy

Calling this out explicitly because it'd be easy to over-learn.

### Don't Dockerize

viznoir ships a Docker image and uses it as the recommended
isolation boundary. That works for viznoir because it's headless: the
render window lives in the container, the PNG is the product. VisLang
wants a native VTK interactor window on the user's desktop. Docker on
macOS with GPU passthrough and a visible VTK window is painful and
fragile. The in-process hardening above (paths, AST validator, typed
MCP args) is the right answer for VisLang, not OS-level sandboxing.

If someone ever wants defense-in-depth, a macOS `sandbox-exec`
profile denying `file-write*` outside `OUTPUT_ROOT` is much lighter
than Docker. But not a priority.

### Don't adopt viznoir's dual-registry pattern

viznoir has a known-gotcha where filters live in two registries
(PascalCase in `core/registry.py`, snake_case in
`engine/filters.py`) and new filters need both. CLAUDE.md calls it
out as a maintenance hazard. VisLang currently has one source of
truth in `PipelineBuilder`; keep it that way. The AST validator from
#3 above will want a single registry of allowed calls, which is also
a good forcing function against drift.

### Don't adopt viznoir's Pydantic-tree DSL

viznoir's DSL is JSON-like Pydantic models that the LLM fills in.
It's safer but much less expressive than VisLang's approach. That's a
real product tradeoff, not just a safety tradeoff -- the "LLM writes
Python-shaped pipeline code" experience is a VisLang differentiator.
Keep it; harden it with AST validation instead of replacing it.

---

## Suggested ordering

Bang-for-buck, highest first:

1. `vislang/paths.py` + route existing path-taking MCP tools
   (`load`, `set_pipeline`, `export_standalone`) through it. Half a
   day. Eliminates the two HIGH findings on tool args.
2. `FileName=` kwarg interception in `PipelineBuilder.source()/
   filter()/raw_source()`. An hour. Eliminates the third HIGH
   finding.
3. AST validator + escape-attempts test suite. One full day. Moves
   the sandbox from "hope" to "spec."
4. Flip `exec()` -> `ast_validate_then_exec()`; add
   `VISLANG_ALLOW_RAW_PYTHON` escape hatch. Half a day.
5. Pydantic-model MCP tool args with custom validated path types. A
   few hours. Prevents regression.
6. `_protect_stdout()` -- when we start seeing MCP protocol
   corruption from VTK warnings. Probably soon.

After step 2 the LLM can no longer read outside the session via the
DSL. After step 4 the LLM can no longer escape the sandbox regardless
of jailbreak creativity. Steps 5-6 are consolidation and robustness.

---

## Notes on the review itself

- The review was done by reading the source, not by attempting live
  exploits. Someone should try the obvious payloads against a running
  server before the hardening lands, to confirm the baseline
  vulnerability profile matches the code reading. The AST-validator
  test suite (#3) is the natural place for this to live long-term.
- The current `exec()` sandbox is *better than nothing* -- the
  missing `__import__` and `open` bindings do raise the bar -- but
  "better than nothing" is the wrong target. The escape-attempts
  suite should be treated as the spec of what the sandbox must
  prevent, independent of implementation.
- VisLang's current threat model is implicit. Making it explicit --
  "the untrusted party is the LLM, possibly prompt-injected via
  filenames/docs it reads" -- would help future design decisions. A
  short section in VISION.md or a dedicated
  `meta/threat-model.md` would do it.
