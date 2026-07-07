"""Sandboxed execution of spec (DSL) code via pydantic-monty.

SIVA executes agent-written "spec" code (a Python-syntax DSL) to build a
pipeline. Spec code is *untrusted*: a prompt-injected agent can submit
malicious code through the same channel a cooperative one uses. This module
runs that code inside Monty and returns the final top-level variable bindings
(name -> value), with pipeline handles surfaced as :class:`siva.dsl.NodeRef`
so ``_freeze_spec`` can scan them. The single entry point is::

    execute(code, namespace) -> dict[str, value]

where *namespace* is the restricted namespace produced by
``siva.dsl._make_namespace`` (builder methods + whitelisted builtins + math +
print).

Security model
--------------

`pydantic-monty <https://pypi.org/project/pydantic-monty/>`_ is a Python
interpreter written in Rust with a restricted builtin/stdlib surface and
*enforceable resource limits*. Within the boundary this module enforces:

- **No host escape.** Imports are limited to Monty's own restricted modules;
  there is no ``os``, ``open``, ``exec``/``eval``, or ``__builtins__``
  traversal. Dunder access on host-registered dataclasses is blocked, and spec
  code cannot construct a registered dataclass.
- **Bounded cost.** Wall-clock, memory, allocations, and recursion depth are
  capped (see the ``MONTY_*`` constants and :func:`_monty_limits`), so a
  runaway or malicious spec is stopped rather than left to burn resources.
- **No trust in inbound data.** Handles that come *back* from the sandbox are
  reconstructed copies whose fields a hostile spec could doctor; the
  marshalling layer (below) never trusts them.

Deliberately *out of scope* here: file-path confinement of the datasets a spec
names (enforced elsewhere, when files are actually opened) and VTK-level
resource use at compute time (no VTK runs during construct).

Marshalling model
------------------

Builder methods return :class:`~siva.dsl.NodeRef` handles that spec code passes
around. A ``NodeRef`` carries the node's full parameter tree — including its
input ``NodeRef`` — so a deep filter chain is a deeply nested structure.
Serializing that whole structure across the boundary on every crossing is
quadratic and hits Monty's input-depth cap. So ``NodeRef`` never crosses the
boundary. Instead the sandbox only ever holds :class:`NodeHandle`, an opaque
frozen dataclass with a single ``node_id`` field, registered via
``dataclass_registry``:

- **Outbound** (builder result -> sandbox): every ``NodeRef`` is recorded in a
  canonical ``node_id -> NodeRef`` table and replaced by ``NodeHandle(node_id)``
  (:func:`_to_handles`). The crossing is O(1) regardless of chain depth.
- **Inbound** (sandbox -> builder call, or a recovered binding): every
  ``NodeHandle`` is re-resolved to its canonical ``NodeRef`` by ``node_id``
  against that table (:func:`_from_handles`). An id absent from the table
  (forged, mutated, or never produced by a builder call) raises
  :class:`SandboxError`. The reconstructed handle's fields are never trusted —
  only the id, and only to look up the trusted original.

``NodeRef`` therefore stays host-only; the only thing the sandbox holds is an
opaque id. Both walks recurse into lists/tuples/dicts so nested handles are
handled too.

Preprocessing contract
----------------------

Spec files may begin with a canonical import of the (possibly not-yet-existing)
stub module :data:`SPEC_IMPORT_MODULE` so an editor's language server resolves
the DSL names. That import is meaningless at runtime -- the names are already
in *namespace* -- so :func:`_strip_spec_import_header` blanks any top-level
``import siva.spec_api`` / ``from siva.spec_api import ...`` statement before
execution. Each such statement is replaced with blank line(s) spanning exactly
its source lines, so line numbers in later error messages are unchanged (no
offset bookkeeping anywhere).

DSL surface
-----------

Spec code is full Python as Monty implements it: f-strings and expressions
work normally. ``math`` is the real (restricted) Monty ``math`` module, made
available by feeding ``import math`` as a separate REPL snippet *before* the
user code, so the user code's line numbers stay pristine. ``print`` is routed
to this module's logger (process stdout is reserved for the MCP protocol).
"""

import ast as _ast
import logging
from dataclasses import dataclass

import pydantic_monty as pm

from .dsl import NodeRef

logger = logging.getLogger(__name__)


# Canonical stub module a spec may import so an editor/language server resolves
# the DSL names. Stripped at runtime (see _strip_spec_import_header); the real
# module need not exist.
SPEC_IMPORT_MODULE = "siva.spec_api"


@dataclass(frozen=True)
class NodeHandle:
    """Opaque handle marshalled across the sandbox boundary in place of a NodeRef.

    A ``NodeRef`` carries the node's full parameter tree (including its input
    ``NodeRef``), so passing it across the boundary re-serializes the whole
    chain on every crossing. ``NodeHandle`` carries only the ``node_id``, which
    the host resolves back to the canonical ``NodeRef`` via its table (see the
    module docstring's marshalling model). It is a frozen
    :func:`~dataclasses.dataclass` so Monty can register it
    (``dataclass_registry=[NodeHandle]``) and marshal it by field data; a copy
    coming back from the sandbox is never trusted beyond its id.
    """

    node_id: int


class SandboxError(Exception):
    """A spec failed at runtime inside the sandbox.

    Carries Monty's own (cleaned) message and line information. Syntax failures
    are raised as ``SyntaxError`` instead.
    """


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
    does not parse we return it unchanged and let Monty surface the syntax
    error with correct line numbers.
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
# Static analysis: builder methods to expose, and names the spec assigns
# --------------------------------------------------------------------------

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
# Marshalling: NodeRef <-> NodeHandle across the boundary
# --------------------------------------------------------------------------

def _to_handles(value, table):
    """Replace outbound ``NodeRef`` instances with ``NodeHandle`` copies.

    Called on builder-method return values (the authoritative originals). Each
    ``NodeRef`` is recorded in the canonical *table* by ``node_id`` and replaced
    with an opaque ``NodeHandle(node_id)`` -- the only thing the sandbox ever
    holds. Recurses into lists/tuples/dicts so nested handles are captured too.
    """
    if isinstance(value, NodeRef):
        table[value.node_id] = value
        return NodeHandle(value.node_id)
    if isinstance(value, list):
        return [_to_handles(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_handles(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _to_handles(v, table) for k, v in value.items()}
    return value


def _from_handles(value, table):
    """Resolve inbound ``NodeHandle`` copies to their canonical ``NodeRef``.

    Monty hands a registered dataclass back as a reconstructed *copy* built
    from field data, which a hostile spec could doctor. We trust only the
    ``node_id`` and look up the canonical instance in *table*; an id absent
    from the table (forged, mutated, or never produced by a builder call)
    raises :class:`SandboxError`. Recurses into lists/tuples/dicts.
    """
    if isinstance(value, NodeHandle):
        canonical = table.get(value.node_id)
        if canonical is None:
            raise SandboxError(
                f"spec referenced unknown node id {value.node_id}"
            )
        return canonical
    if isinstance(value, list):
        return [_from_handles(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_from_handles(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _from_handles(v, table) for k, v in value.items()}
    return value


def _make_wrapper(method, table):
    """Wrap a builder method: resolve inbound handles, wrap outbound NodeRefs.

    Inbound ``NodeHandle`` arguments are re-resolved to their canonical host
    ``NodeRef`` by ``node_id`` (:func:`_from_handles`) before the real method
    runs. The result's ``NodeRef`` handles are recorded in the canonical *table*
    and replaced with ``NodeHandle`` copies (:func:`_to_handles`).
    """
    def wrapper(*args, **kwargs):
        args = tuple(_from_handles(a, table) for a in args)
        kwargs = {k: _from_handles(v, table) for k, v in kwargs.items()}
        result = method(*args, **kwargs)
        return _to_handles(result, table)

    return wrapper


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

# Resource limits. A few seconds of wall-clock and a few hundred MB is ample
# for *constructing* a spec (no VTK runs at construct time); these bound a
# runaway or malicious spec. Exposed as module-level constants so a deployment
# can tune them without editing logic.
MONTY_MAX_DURATION_SECS = 5.0
MONTY_MAX_MEMORY = 256 * 1024 * 1024
MONTY_MAX_ALLOCATIONS = 50_000_000
MONTY_MAX_RECURSION_DEPTH = 200


def _monty_limits():
    """Build a ``pydantic_monty.ResourceLimits`` from the ``MONTY_*`` constants."""
    return pm.ResourceLimits(
        max_duration_secs=MONTY_MAX_DURATION_SECS,
        max_memory=MONTY_MAX_MEMORY,
        max_allocations=MONTY_MAX_ALLOCATIONS,
        max_recursion_depth=MONTY_MAX_RECURSION_DEPTH,
    )


def _make_print():
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


def execute(code, namespace):
    """Run *code* against *namespace* in Monty; return final bindings.

    *namespace* is the dict from ``siva.dsl._make_namespace``. Returns a dict
    of top-level variable name -> value with pipeline handles resolved to
    canonical ``NodeRef`` instances. The canonical spec-import header
    (:data:`SPEC_IMPORT_MODULE`) is stripped before execution.
    """
    code = _strip_spec_import_header(code)

    table = {}  # canonical node_id -> NodeRef, populated by the wrappers
    external = {
        name: _make_wrapper(method, table)
        for name, method in _builder_callables(namespace).items()
    }

    # A MontyRepl keeps namespace state across snippets, which lets us (a) make
    # `math` available via a separate setup snippet so the user code's line
    # numbers stay pristine (no offset correction), and (b) recover bindings by
    # reading each name back afterwards -- including conditionally-bound names.
    repl = pm.MontyRepl(
        script_name="spec.py",
        dataclass_registry=[NodeHandle],
        limits=_monty_limits(),
    )

    try:
        # `math.sqrt(...)` works without the spec importing it. Fed as its own
        # snippet, so it does not shift user-code lines.
        repl.feed_run("import math", external_functions=external)
    except pm.MontyError as e:  # pragma: no cover - static setup snippet
        raise SandboxError(f"internal: math setup failed: {_monty_message(e)}") from e

    try:
        # Monty raises syntax errors and runtime errors both from feed_run.
        repl.feed_run(
            code,
            external_functions=external,
            print_callback=_make_print(),
        )
    except pm.MontySyntaxError as e:
        raise SyntaxError(_monty_message(e)) from e
    except pm.MontyError as e:
        raise SandboxError(_monty_message(e)) from e

    # Recover bindings: read each assigned name back from the persistent REPL
    # namespace. Names that are unbound (a branch not taken) or hold
    # non-representable values raise inside feed_run and are skipped. Returned
    # handles are resolved back to canonical NodeRefs.
    bindings = {}
    for name in _collect_assigned_names(code):
        try:
            value = repl.feed_run(name, external_functions=external)
        except pm.MontyError:
            continue
        bindings[name] = _from_handles(value, table)
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
