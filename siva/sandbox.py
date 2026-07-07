"""Sandboxed execution backends for spec (DSL) code.

SIVA executes agent-written "spec" code (a Python-syntax DSL) to build a
pipeline. This module runs that code against a builder and returns the final
top-level variable bindings (name -> value), with pipeline handles surfaced as
:class:`siva.dsl.NodeRef` so ``_freeze_spec`` can scan them. Three backends
share one entry point::

    execute(code, namespace, backend) -> dict[str, value]

where *namespace* is the restricted namespace produced by
``siva.dsl._make_namespace`` (builder methods + whitelisted builtins + math +
print). Backend is chosen by the *backend* argument, else the ``SIVA_SANDBOX``
environment variable (``exec`` | ``starlark`` | ``monty``), else ``exec``.

Security model
--------------

The whole point of the sandboxed backends is that spec code is *untrusted*: a
prompt-injected agent could submit malicious code through the same channel a
cooperative one uses. What each backend guarantees:

- ``"exec"`` (default, reference) -- CPython ``exec()``. **NOT a sandbox.**
  Full Python; a hostile spec can import ``os``, open files, spawn processes,
  reach ``__builtins__`` via dunder traversal, etc. Use only when the spec
  source is trusted. It exists as the semantic reference the other two are
  compared against.
- ``"starlark"`` -- starlark-pyo3, a deliberately small hermetic Python
  dialect. No imports, no attribute traversal onto host objects, no
  ``open``/``exec``/``eval``, no ``while`` and no recursion (so it terminates
  structurally). **It has no CPU or wall-clock or memory limit** -- the
  language guarantees *termination*, not *bounded cost*, so ``for i in
  range(10**9): ...`` still burns CPU. There is no watchdog here.
- ``"monty"`` -- pydantic-monty, a Python interpreter written in Rust with a
  restricted builtin/stdlib surface and *enforceable resource limits*
  (wall-clock, memory, allocations, recursion depth -- see the ``MONTY_*``
  module constants). Dunder access on host-registered dataclasses is blocked,
  spec code cannot construct a registered dataclass, and imports are limited
  to Monty's own restricted modules. This is the intended production backend.

Both ``starlark`` and ``monty`` require the ``sandbox`` extra
(``pip install -e ".[sandbox]"``).

Marshalling model
-----------------

Builder methods return :class:`~siva.dsl.NodeRef` handles that spec code passes
around. How a handle crosses the sandbox boundary differs per backend:

- **exec**: no boundary; real ``NodeRef`` objects flow directly.
- **starlark**: a ``NodeRef`` returned to Starlark is wrapped in
  ``OpaquePythonObject`` (Starlark cannot represent an arbitrary Python
  object). It round-trips back to the *same* ``NodeRef`` when passed into
  another host callable or read via ``module[name]``.
- **monty**: ``NodeRef`` is a registered dataclass
  (``dataclass_registry=[NodeRef]``), so Monty marshals it across the boundary
  by *field data*. Crucially, an instance coming *back* from the sandbox (as a
  call argument or a recovered binding) is a **reconstructed copy**, not the
  original -- and a hostile spec could doctor its fields. So we never trust
  inbound field data: the wrappers maintain a canonical ``node_id -> NodeRef``
  table populated from the *real* objects the builder methods return, and every
  inbound ``NodeRef`` is re-resolved to its canonical instance by ``node_id``
  (:func:`_resolve_nodes`). An unknown id raises :class:`SandboxError`. This
  makes the reconstructed ``vtk_class``/``properties`` fields irrelevant --
  only the ``node_id`` is used, and only to look up the trusted original.

Preprocessing contract
----------------------

Spec files may begin with a canonical import of the (possibly not-yet-existing)
stub module :data:`SPEC_IMPORT_MODULE` so an editor's language server resolves
the DSL names. That import is meaningless at runtime -- the names are already
in *namespace* -- so :func:`_strip_spec_import_header` blanks any top-level
``import siva.spec_api`` / ``from siva.spec_api import ...`` statement **before
handing code to any backend, exec included**. Each such statement is replaced
with blank line(s) spanning exactly its source lines, so line numbers in later
error messages are unchanged (no offset bookkeeping anywhere).

DSL-visible differences per backend
-----------------------------------

**exec** (reference): full Python expression syntax, the real ``math`` module,
``print`` to stdout.

**starlark**:

- f-strings only substitute *bare identifiers*: ``f"iso {level}"`` works,
  ``f"{level + 1}"`` is a parse error.
- ``math`` exposes only a curated member set (``_MATH_CONSTANTS`` /
  ``_MATH_FUNCS``) as ``math.pi``, ``math.sqrt(...)``; other members do not
  exist.
- ``tuple(...)`` yields a list (Starlark has no distinct tuple type).
- No ``while`` loops and no recursion; ``for`` iterates finite iterables only.
- ``print`` is routed to this module's logger (Starlark's native ``print``
  writes to process stdout, which would corrupt the MCP protocol).

**monty**:

- Full Python f-strings and expressions.
- ``math`` is the real (restricted) Monty ``math`` module: full member set,
  dotted access. It is made available by feeding ``import math`` as a separate
  REPL snippet *before* the user code, so the user code's line numbers stay
  pristine and no offset correction is needed.
- ``print`` is routed to this module's logger via Monty's ``print_callback``.
- Resource limits (wall-clock, memory, allocations, recursion) are enforced;
  see the ``MONTY_*`` constants and :func:`_monty_limits`.
"""

import ast as _ast
import logging
import math as _math
import os

from .dsl import NodeRef

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------

BACKENDS = ("exec", "starlark", "monty")
DEFAULT_BACKEND = "exec"
_ENV_VAR = "SIVA_SANDBOX"

# Canonical stub module a spec may import so an editor/language server resolves
# the DSL names. Stripped at runtime (see _strip_spec_import_header); the real
# module need not exist.
SPEC_IMPORT_MODULE = "siva.spec_api"


class SandboxError(Exception):
    """A spec failed at runtime inside a sandboxed backend.

    Carries the backend's own (cleaned) message and line information. Syntax
    failures are raised as ``SyntaxError`` instead, to mirror the ``exec``
    backend's surface.
    """


def resolve_backend(backend=None):
    """Return the backend name to use, validating it.

    Precedence: explicit *backend* argument, then ``$SIVA_SANDBOX``, then
    :data:`DEFAULT_BACKEND`.
    """
    name = backend or os.environ.get(_ENV_VAR) or DEFAULT_BACKEND
    if name not in BACKENDS:
        raise ValueError(
            f"Unknown sandbox backend {name!r}. "
            f"Valid options: {', '.join(BACKENDS)}."
        )
    return name


def execute(code, namespace, backend=None):
    """Run *code* against *namespace* using *backend*; return final bindings.

    *namespace* is the dict from ``siva.dsl._make_namespace``. Returns a dict
    of top-level variable name -> value with pipeline handles as ``NodeRef``.
    The canonical spec-import header (:data:`SPEC_IMPORT_MODULE`) is stripped
    here, uniformly for every backend, before execution.
    """
    name = resolve_backend(backend)
    code = _strip_spec_import_header(code)
    if name == "exec":
        return _run_exec(code, namespace)
    if name == "starlark":
        return _run_starlark(code, namespace)
    if name == "monty":
        return _run_monty(code, namespace)
    raise ValueError(f"Unknown sandbox backend {name!r}.")  # pragma: no cover


# --------------------------------------------------------------------------
# Preprocessing: strip the canonical stub-import header
# --------------------------------------------------------------------------

def _is_spec_module(name):
    """True if *name* is the spec stub module or a submodule of it."""
    return name == SPEC_IMPORT_MODULE or name.startswith(SPEC_IMPORT_MODULE + ".")


def _strip_spec_import_header(code):
    """Blank any top-level import of :data:`SPEC_IMPORT_MODULE` in *code*.

    Editors resolve the DSL names via a typed stub module that spec files
    import (``from siva.spec_api import *``); at runtime the names already live
    in the execution namespace, so the import is inert and the module need not
    exist. We replace each such *top-level* statement with blank line(s)
    spanning exactly its source lines, so downstream error line numbers are
    unaffected -- no offset bookkeeping. A statement that imports the stub
    module alongside others on one physical line is blanked wholesale; the
    canonical header keeps the import on its own line, so this is a non-issue
    in practice.

    Parsing untrusted code with ``ast.parse`` is side-effect-free. If the code
    does not parse we return it unchanged and let the backend surface the
    syntax error with correct line numbers.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return code

    to_blank = set()
    for node in tree.body:
        if isinstance(node, _ast.Import):
            hit = any(_is_spec_module(alias.name) for alias in node.names)
        elif isinstance(node, _ast.ImportFrom):
            hit = node.level == 0 and node.module and _is_spec_module(node.module)
        else:
            hit = False
        if hit:
            end = getattr(node, "end_lineno", None) or node.lineno
            to_blank.update(range(node.lineno, end + 1))

    if not to_blank:
        return code

    lines = code.split("\n")
    for lineno in to_blank:
        if 1 <= lineno <= len(lines):
            lines[lineno - 1] = ""
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

# math members exposed to the sandboxed backends. Constants are surfaced as
# literal values; functions are wired as host callables.
_MATH_CONSTANTS = ("pi", "e", "tau")
_MATH_FUNCS = (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "log", "log10", "log2", "exp", "pow", "floor", "ceil", "fabs",
    "radians", "degrees", "hypot", "copysign", "fmod",
)


def _builder_callables(namespace):
    """Return the bound PipelineBuilder methods from *namespace*.

    ``_make_namespace`` is the single source of the exposed API surface; the
    builder methods are exactly its bound-method entries.
    """
    import inspect

    return {
        name: value
        for name, value in namespace.items()
        if inspect.ismethod(value)
    }


def _collect_assigned_names(code):
    """Collect the names bound by assignments anywhere in *code*.

    Parsing untrusted code with ``ast.parse`` is safe (no execution). We
    collect names bound by ``Assign``/``AnnAssign`` statements and ``for`` loop
    targets, descending into the bodies of control-flow statements
    (``if``/``for``/``while``/``with``/``try``) so *conditionally*-bound names
    (e.g. ``if cond: t = threshold(...)``) are included. Nested function and
    class scopes are not descended into -- names bound there are not module
    globals. Callers tolerate names that turn out to be unbound at recovery
    time (a conditional branch not taken).

    Returns names in a stable, de-duplicated order.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return []

    names = []
    seen = set()

    def add(name):
        if name not in seen:
            seen.add(name)
            names.append(name)

    def visit(stmt):
        if isinstance(stmt, _ast.Assign):
            for target in stmt.targets:
                _walk_target(target, add)
        elif isinstance(stmt, _ast.AnnAssign) and isinstance(stmt.target, _ast.Name):
            add(stmt.target.id)
        elif isinstance(stmt, _ast.For):
            _walk_target(stmt.target, add)
            for inner in stmt.body:
                visit(inner)
            for inner in stmt.orelse:
                visit(inner)
        elif isinstance(stmt, (_ast.If, _ast.While)):
            for inner in stmt.body:
                visit(inner)
            for inner in stmt.orelse:
                visit(inner)
        elif isinstance(stmt, _ast.With):
            for inner in stmt.body:
                visit(inner)
        elif isinstance(stmt, _ast.Try):
            for block in (stmt.body, stmt.orelse, stmt.finalbody):
                for inner in block:
                    visit(inner)
            for handler in stmt.handlers:
                for inner in handler.body:
                    visit(inner)
        # FunctionDef/ClassDef/etc.: distinct scope, not module globals.

    for stmt in tree.body:
        visit(stmt)

    return names


def _walk_target(node, add):
    """Add ``Name`` ids from an assignment target (handles tuple unpacking)."""
    if isinstance(node, _ast.Name):
        add(node.id)
    elif isinstance(node, (_ast.Tuple, _ast.List)):
        for elt in node.elts:
            _walk_target(elt, add)


# --------------------------------------------------------------------------
# exec backend (reference; NOT sandboxed)
# --------------------------------------------------------------------------

def _run_exec(code, namespace):
    """Execute *code* with CPython ``exec`` and return the namespace.

    This is the historical behavior; ``_freeze_spec`` scans the returned dict
    for ``NodeRef`` values. Errors (``SyntaxError``, ``NameError``, ...)
    propagate unchanged. This backend is **not** a sandbox.
    """
    exec(code, namespace)
    return namespace


# --------------------------------------------------------------------------
# starlark backend
# --------------------------------------------------------------------------

def _import_starlark():
    try:
        import starlark
    except ImportError as e:  # pragma: no cover - exercised only when missing
        raise ImportError(
            "The 'starlark' backend requires starlark-pyo3. Install the "
            "sandbox extra: pip install -e \".[sandbox]\"."
        ) from e
    return starlark


def _starlark_print(*args, **kwargs):
    logger.info("[spec print] %s", " ".join(str(a) for a in args))


def _make_starlark_wrapper(starlark, method):
    """Wrap a builder method for Starlark: NodeRef results become opaque.

    Starlark converts host-callable return values to Starlark values; a
    ``NodeRef`` is not JSON-serializable, so it is wrapped in
    ``OpaquePythonObject``, which round-trips back to the same ``NodeRef``
    when passed into another host callable or read via ``module[name]``.
    Arguments arrive already converted (opaque values, including those nested
    in lists, are unwrapped to their ``NodeRef`` before the call).
    """
    def wrapper(*args, **kwargs):
        result = method(*args, **kwargs)
        if isinstance(result, NodeRef):
            return starlark.OpaquePythonObject(result)
        return result

    return wrapper


def _starlark_math_preamble():
    """Starlark source defining a ``math`` struct from host callables."""
    parts = [f"{c}={getattr(_math, c)!r}" for c in _MATH_CONSTANTS]
    parts += [f"{fn}=_m_{fn}" for fn in _MATH_FUNCS]
    return "math = struct(" + ", ".join(parts) + ")\n"


def _run_starlark(code, namespace):
    starlark = _import_starlark()

    module = starlark.Module()
    for name, method in _builder_callables(namespace).items():
        module.add_callable(name, _make_starlark_wrapper(starlark, method))
    # Builtins missing from Starlark's standard globals.
    module.add_callable("print", _starlark_print)
    module.add_callable("sum", sum)
    module.add_callable("round", round)
    for fn in _MATH_FUNCS:
        module.add_callable("_m_" + fn, getattr(_math, fn))

    globals_ = starlark.Globals.standard().extended_by(
        [starlark.LibraryExtension.StructType]
    )
    dialect = starlark.Dialect.standard()
    dialect.enable_f_strings = True
    # Allow bare statements (assignments, for loops) at module top level;
    # otherwise the standard dialect only permits them inside `def`.
    dialect.enable_top_level_stmt = True

    try:
        # The math preamble is evaluated as a separate AST into the shared
        # module so that user-code line numbers stay accurate in errors.
        pre_ast = starlark.parse("<siva-math>", _starlark_math_preamble(), dialect)
        starlark.eval(module, pre_ast, globals_)
    except Exception as e:  # pragma: no cover - preamble is static & tested
        raise SandboxError(f"internal: math preamble failed: {e}") from e

    try:
        user_ast = starlark.parse("spec.py", code, dialect)
    except Exception as e:
        raise SyntaxError(_clean_starlark_message(e)) from e

    try:
        starlark.eval(module, user_ast, globals_)
    except Exception as e:
        raise SandboxError(_clean_starlark_message(e)) from e

    # Recover bindings by reading each collected name; skip names that are
    # absent (conditional branch not taken) or hold non-representable values.
    bindings = {}
    for name in _collect_assigned_names(code):
        try:
            bindings[name] = module[name]
        except Exception:
            continue
    return bindings


def _clean_starlark_message(exc):
    """Return a readable one-or-few-line message from a StarlarkError."""
    msg = str(exc).strip()
    return msg or f"{type(exc).__name__}"


# --------------------------------------------------------------------------
# monty backend
# --------------------------------------------------------------------------

# Resource limits for the monty backend. A few seconds of wall-clock and a few
# hundred MB is ample for *constructing* a spec (no VTK runs at construct
# time); these bound a runaway or malicious spec. Exposed as module-level
# constants so a deployment can tune them without editing logic.
MONTY_MAX_DURATION_SECS = 5.0
MONTY_MAX_MEMORY = 256 * 1024 * 1024
MONTY_MAX_ALLOCATIONS = 50_000_000
MONTY_MAX_RECURSION_DEPTH = 200


def _monty_limits():
    """Build a ``pydantic_monty.ResourceLimits`` from the ``MONTY_*`` constants."""
    import pydantic_monty as pm

    return pm.ResourceLimits(
        max_duration_secs=MONTY_MAX_DURATION_SECS,
        max_memory=MONTY_MAX_MEMORY,
        max_allocations=MONTY_MAX_ALLOCATIONS,
        max_recursion_depth=MONTY_MAX_RECURSION_DEPTH,
    )


def _import_monty():
    try:
        import pydantic_monty
    except ImportError as e:  # pragma: no cover - exercised only when missing
        raise ImportError(
            "The 'monty' backend requires pydantic-monty. Install the "
            "sandbox extra: pip install -e \".[sandbox]\"."
        ) from e
    return pydantic_monty


def _register_nodes(value, table):
    """Record every ``NodeRef`` in *value* in the canonical *table* by id.

    Called on builder-method return values (the authoritative originals),
    recursing into lists/tuples/dicts so nested handles are captured too.
    """
    if isinstance(value, NodeRef):
        table[value.node_id] = value
    elif isinstance(value, (list, tuple)):
        for item in value:
            _register_nodes(item, table)
    elif isinstance(value, dict):
        for item in value.values():
            _register_nodes(item, table)


def _resolve_nodes(value, table):
    """Resolve inbound ``NodeRef`` copies to their canonical originals.

    Monty hands a registered dataclass back as a reconstructed *copy* built
    from field data, which a hostile spec could doctor. We trust only the
    ``node_id`` and look up the canonical instance in *table*; an id absent
    from the table (forged, mutated, or never produced by a builder call)
    raises :class:`SandboxError`. Recurses into lists/tuples/dicts.
    """
    if isinstance(value, NodeRef):
        canonical = table.get(value.node_id)
        if canonical is None:
            raise SandboxError(
                f"spec referenced unknown node id {value.node_id}"
            )
        return canonical
    if isinstance(value, list):
        return [_resolve_nodes(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_resolve_nodes(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _resolve_nodes(v, table) for k, v in value.items()}
    return value


def _make_monty_wrapper(method, table):
    """Wrap a builder method for Monty: resolve inbound handles, record results.

    Inbound ``NodeRef`` arguments are reconstructed copies; each is re-resolved
    to its canonical host instance by ``node_id`` (:func:`_resolve_nodes`)
    before the real method runs. The result's handles are recorded in the
    canonical *table* (:func:`_register_nodes`) so later crossings resolve.
    """
    def wrapper(*args, **kwargs):
        args = tuple(_resolve_nodes(a, table) for a in args)
        kwargs = {k: _resolve_nodes(v, table) for k, v in kwargs.items()}
        result = method(*args, **kwargs)
        _register_nodes(result, table)
        return result

    return wrapper


def _make_monty_print():
    """Return a print_callback that buffers fragments and logs whole lines.

    Monty invokes the callback once per print fragment (arguments, separators,
    and the trailing newline). We accumulate until a newline and then log.
    """
    buf = []

    def callback(stream, text):
        if text == "\n":
            logger.info("[spec print] %s", "".join(buf))
            buf.clear()
        else:
            buf.append(text)

    return callback


def _run_monty(code, namespace):
    pm = _import_monty()

    table = {}  # canonical node_id -> NodeRef, populated by the wrappers
    external = {
        name: _make_monty_wrapper(method, table)
        for name, method in _builder_callables(namespace).items()
    }

    # A MontyRepl keeps namespace state across snippets, which lets us (a) make
    # `math` available via a separate setup snippet so the user code's line
    # numbers stay pristine (no offset correction), and (b) recover bindings by
    # reading each name back afterwards -- including conditionally-bound names.
    repl = pm.MontyRepl(
        script_name="spec.py",
        dataclass_registry=[NodeRef],
        limits=_monty_limits(),
    )

    try:
        # Parity with exec: `math.sqrt(...)` works without the spec importing
        # it. Fed as its own snippet, so it does not shift user-code lines.
        repl.feed_run("import math", external_functions=external)
    except pm.MontyError as e:  # pragma: no cover - static setup snippet
        raise SandboxError(f"internal: math setup failed: {_monty_message(e)}") from e

    try:
        # Monty raises syntax errors and runtime errors both from feed_run.
        repl.feed_run(
            code,
            external_functions=external,
            print_callback=_make_monty_print(),
        )
    except pm.MontySyntaxError as e:
        raise SyntaxError(_monty_message(e)) from e
    except pm.MontyError as e:
        raise SandboxError(_monty_message(e)) from e

    # Recover bindings: read each assigned name back from the persistent REPL
    # namespace. Names that are unbound (a branch not taken) or hold
    # non-representable values raise inside feed_run and are skipped.
    bindings = {}
    for name in _collect_assigned_names(code):
        try:
            value = repl.feed_run(name, external_functions=external)
        except pm.MontyError:
            continue
        bindings[name] = _resolve_nodes(value, table)
    return bindings


def _monty_message(exc):
    """Build a readable message from a Monty error, naming the spec line.

    Monty's ``str(exc)`` already names the underlying error kind (e.g.
    ``NameError: ...``) but omits the line; we append the user-code line from
    the structured traceback. No offset correction is needed: the user code is
    fed as its own REPL snippet, so its reported line numbers are already
    relative to the spec.
    """
    msg = str(exc).strip()
    try:
        frames = exc.traceback()
    except Exception:
        frames = None
    if frames:
        line = getattr(frames[-1], "line", None)
        if isinstance(line, int) and line >= 1:
            return f"{msg} (spec.py line {line})"
    return msg
