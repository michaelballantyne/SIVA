"""Sandboxed execution backends for spec (DSL) code.

SIVA executes agent-written "spec" code (a Python-syntax DSL) to build a
pipeline. The default backend is CPython ``exec()``, which is *not* sandboxed:
a prompt-injected agent could submit malicious spec code and reach the host.
This module adds two sandboxed alternatives so the spec-execution channel can
be made safe, selectable at runtime for comparison.

Backends
--------

All three backends share one interface::

    execute(code, namespace, backend) -> dict[str, value]

where *namespace* is the restricted namespace produced by
``siva.dsl._make_namespace`` (builder methods + whitelisted builtins + math +
print). Each backend runs *code* against the builder methods found in that
namespace and returns the final top-level variable bindings it can recover
(name -> value), with any pipeline handles returned as real
``siva.dsl.NodeRef`` objects so that ``_freeze_spec`` can scan them.

- ``"exec"`` -- CPython ``exec()``. Default. Full Python; **no sandboxing**.
- ``"starlark"`` -- starlark-pyo3. A deliberately small, hermetic dialect of
  Python. No imports, no attribute traversal, no ``open``/``exec``/``eval``,
  no ``while``/recursion (strong termination story). Requires the ``sandbox``
  extra (``pip install -e ".[sandbox]"``).
- ``"monty"`` -- pydantic-monty. A Python interpreter written in Rust with a
  restricted builtin/stdlib surface and enforceable resource limits. Requires
  the ``sandbox`` extra.

Backend is chosen by the *backend* argument, else the ``SIVA_SANDBOX``
environment variable (``exec`` | ``starlark`` | ``monty``), else ``exec``.

DSL-visible differences per backend
-----------------------------------

**exec** (reference): full Python expression syntax, real ``math`` module,
``print`` goes to stdout.

**starlark**:

- f-strings only substitute *bare identifiers*: ``f"iso {level}"`` works,
  ``f"{level + 1}"`` is a parse error. Enable/disable is handled here.
- ``math`` exposes only a curated set of members (see ``_MATH_CONSTANTS`` /
  ``_MATH_FUNCS``) as ``math.pi``, ``math.sqrt(...)`` etc. Members outside that
  set do not exist.
- ``tuple(...)`` yields a list (Starlark has no distinct tuple type).
- No ``while`` loops and no recursion; ``for`` iterates finite iterables only.
- ``print`` is routed to this module's logger (Starlark's native ``print``
  writes to process stdout, which would corrupt the MCP protocol, so it is
  intentionally *not* used).

**monty**:

- Full Python f-strings and expressions.
- ``math`` is the real (restricted) Monty ``math`` module: full member set,
  dotted access. It is imported implicitly when a spec references ``math``.
- ``print`` is routed to this module's logger via Monty's ``print_callback``.
- Resource limits (wall-clock, memory, allocations, recursion) are enforced;
  see ``_MONTY_LIMITS``.

Resource limits
---------------

- **monty**: hard limits via ``pydantic_monty.ResourceLimits`` -- see
  ``_MONTY_LIMITS`` (a few seconds wall-clock, a few hundred MB).
- **starlark**: the language guarantees termination structurally (no
  ``while``, no recursion, finite ``for``), but starlark-pyo3 exposes **no**
  wall-clock or memory limit API, so a spec like ``for i in range(10**9)`` can
  still burn CPU. There is no watchdog here.
- **exec**: none (it is the unsafe reference backend).
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
    """
    name = resolve_backend(backend)
    if name == "exec":
        return _run_exec(code, namespace)
    if name == "starlark":
        return _run_starlark(code, namespace)
    if name == "monty":
        return _run_monty(code, namespace)
    raise ValueError(f"Unknown sandbox backend {name!r}.")  # pragma: no cover


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
    """Collect top-level simple assignment target names from *code*.

    Parsing untrusted code with ``ast.parse`` is safe (no execution). We
    collect names bound by module-level ``Assign``/``AnnAssign`` statements,
    plus the target and body assignments of module-level ``for`` loops (the
    "loop that builds several nodes" pattern). Conditionally-bound names
    (inside ``if``/``while``) are intentionally skipped -- they may be unbound
    when we read them back, and the callers tolerate a partial name set.

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

    def targets(node):
        for t in getattr(node, "targets", []):
            _walk_target(t, add)

    def _assign_names(stmt):
        if isinstance(stmt, _ast.Assign):
            targets(stmt)
        elif isinstance(stmt, _ast.AnnAssign) and isinstance(stmt.target, _ast.Name):
            add(stmt.target.id)

    for stmt in tree.body:
        _assign_names(stmt)
        if isinstance(stmt, _ast.For):
            _walk_target(stmt.target, add)
            for inner in stmt.body:
                _assign_names(inner)

    return names


def _walk_target(node, add):
    """Add ``Name`` ids from an assignment target (handles tuple unpacking)."""
    if isinstance(node, _ast.Name):
        add(node.id)
    elif isinstance(node, (_ast.Tuple, _ast.List)):
        for elt in node.elts:
            _walk_target(elt, add)


def _references_name(code, target):
    """True if *code* references the bare name *target* anywhere."""
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return False
    return any(
        isinstance(n, _ast.Name) and n.id == target for n in _ast.walk(tree)
    )


# --------------------------------------------------------------------------
# exec backend (reference; NOT sandboxed)
# --------------------------------------------------------------------------

def _run_exec(code, namespace):
    """Execute *code* with CPython ``exec`` and return the namespace.

    This is the historical behavior; ``_freeze_spec`` scans the returned dict
    for ``NodeRef`` values. Errors (``SyntaxError``, ``NameError``, ...)
    propagate unchanged.
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
    # absent or hold non-representable values (e.g. a bare range()).
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

# A few seconds of wall-clock and a few hundred MB is plenty for constructing
# a spec (no VTK runs at construct time). These bound a runaway or malicious
# spec.
def _monty_limits():
    import pydantic_monty as pm

    return pm.ResourceLimits(
        max_duration_secs=5.0,
        max_memory=256 * 1024 * 1024,
        max_allocations=50_000_000,
        max_recursion_depth=200,
    )


_TOKEN_KEY = "__siva_node__"


def _import_monty():
    try:
        import pydantic_monty
    except ImportError as e:  # pragma: no cover - exercised only when missing
        raise ImportError(
            "The 'monty' backend requires pydantic-monty. Install the "
            "sandbox extra: pip install -e \".[sandbox]\"."
        ) from e
    return pydantic_monty


def _to_token(value, table):
    """Convert a ``NodeRef`` to a Monty-representable token, registering it.

    Recurses into lists/tuples/dicts (builder methods return ``NodeRef`` or
    ``None`` today, but this stays robust to richer returns).
    """
    if isinstance(value, NodeRef):
        table[value._node_id] = value
        return {_TOKEN_KEY: value._node_id}
    if isinstance(value, list):
        return [_to_token(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_token(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _to_token(v, table) for k, v in value.items()}
    return value


def _from_token(value, table):
    """Convert node tokens back to ``NodeRef``, recursing into containers."""
    if isinstance(value, dict):
        if len(value) == 1 and _TOKEN_KEY in value:
            node_id = value[_TOKEN_KEY]
            if node_id in table:
                return table[node_id]
        return {k: _from_token(v, table) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_token(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_from_token(v, table) for v in value)
    return value


def _make_monty_wrapper(method, table):
    """Wrap a builder method for Monty: unwrap token args, tokenize results.

    Monty cannot represent arbitrary Python objects, so ``NodeRef`` handles
    cross the boundary as ``{"__siva_node__": id}`` tokens, with a host-side
    id -> NodeRef *table* for reconstruction.
    """
    def wrapper(*args, **kwargs):
        args = tuple(_from_token(a, table) for a in args)
        kwargs = {k: _from_token(v, table) for k, v in kwargs.items()}
        return _to_token(method(*args, **kwargs), table)

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

    table = {}
    external = {
        name: _make_monty_wrapper(method, table)
        for name, method in _builder_callables(namespace).items()
    }

    names = _collect_assigned_names(code)
    # Monty's run() returns only the last expression, so append a marker dict
    # that captures the top-level bindings we want back.
    marker = "{" + ", ".join(f"{n!r}: {n}" for n in names) + "}"

    # Implicitly import the real (restricted) math module when referenced, so
    # `math.sqrt(...)` works without the spec importing it (parity with exec).
    prepend = "import math\n" if _references_name(code, "math") else ""
    offset = prepend.count("\n")
    full = prepend + code + "\n" + marker

    try:
        # Monty raises syntax errors at construction, runtime errors at run().
        monty = pm.Monty(full, script_name="spec.py")
        result = monty.run(
            external_functions=external,
            print_callback=_make_monty_print(),
            limits=_monty_limits(),
        )
    except pm.MontySyntaxError as e:
        raise SyntaxError(_monty_message(e, offset)) from e
    except pm.MontyError as e:
        raise SandboxError(_monty_message(e, offset)) from e

    bindings = {}
    if isinstance(result, dict):
        for name, value in result.items():
            bindings[name] = _from_token(value, table)
    return bindings


def _monty_message(exc, offset):
    """Build a readable message from a Monty error, adjusting for *offset*.

    Monty's ``str(exc)`` already names the underlying error kind (e.g.
    ``NameError: ...``) but omits the line; we append the user-code line from
    the structured traceback, corrected for any implicitly-prepended lines.
    """
    msg = str(exc).strip()
    try:
        frames = exc.traceback()
    except Exception:
        frames = None
    if frames:
        frame = frames[-1]
        line = getattr(frame, "line", None)
        if isinstance(line, int):
            adjusted = line - offset
            if adjusted >= 1:
                return f"{msg} (spec.py line {adjusted})"
    return msg
