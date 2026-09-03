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

Deliberately *out of scope* here: VTK-level resource use at compute time (no
VTK runs during construct). File-path confinement of the datasets a spec names
is a separate concern enforced later, at the point a reader's path property is
about to be used -- ``siva.filters.confine_to_workdir``, called from
``create_vtk_filter`` before the file is opened (and thus also covering
``siva.run`` and hot reload, which funnel through the same construct-then-
compute chokepoint). The rule: absolute paths are rejected, and the named path
must not lexically escape the working directory (``../x`` is rejected even
though the process never resolves symlinks for the check) -- a symlink placed
*inside* the working directory that points elsewhere on disk is intentionally
still followed, since it was placed by the trusted human, not the sandboxed
spec.

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

Preprocessing contract: the mandatory DSL-namespace header
----------------------------------------------------------

Every spec **must** begin with the canonical header ``from siva.spec_api
import *`` as its first top-level statement (:func:`_install_dsl_namespace_header`
enforces this; a missing or misplaced header is a ``SyntaxError`` the agent
sees). An optional module docstring -- a leading ``ast.Expr`` string constant
-- may precede the header, since an agent-written spec plausibly opens with
one; nothing else may. There is exactly one supported "empty pipeline" form:
a header-only file -- just ``from siva.spec_api import *`` and nothing else
(the optional docstring aside) -- which builds cleanly to an empty view. A
whitespace-only or otherwise header-less file is *not* equivalent and stays a
``SyntaxError`` naming the required header line, so clearing a view is always
one deliberate, unambiguous gesture rather than an accidental side effect of
deleting everything. The stub module :data:`SPEC_IMPORT_MODULE` need not
exist at runtime -- the header exists so an editor's language server resolves
the DSL names, and so we have a guaranteed, uniquely identifiable line to
rewrite.

At runtime that header line is *substituted in place* with a generated one-line
binding preamble, e.g. ``source = __siva_source; filter = __siva_filter; ...``.
This is the crux of the design. Monty resolves its own builtins (``filter``,
``slice``, ``map``, ``min``, ``sorted``, ...) at *compile* time, and that
resolution wins over identically-named ``external_functions`` -- so a builder
method named ``filter`` passed as an external function would be unreachable,
shadowed by Monty's builtin. A module-level *global binding* of the same name,
however, is honored (an already-bound name skips builtin substitution). So we
register every builder wrapper under a non-colliding alias key
``__siva_<name>`` in ``external_functions`` and let the preamble bind each real
DSL name to its alias at module scope, shadowing any builtin. Routing *all*
verbs (not just the colliding ones) through this scheme means present and
future Monty builtins can never shadow a DSL form.

The swap is one physical line for one physical line, so every subsequent line
keeps its original number -- an error on the author's line 3 is reported at
line 3, with no offset bookkeeping anywhere. Both the alias dict and the
preamble are derived from the *same* introspected builder methods
(:func:`_builder_callables`), so a newly added builder method works with zero
maintenance. Because the rewrite blanks a whole physical line, a spec-import
statement may not share a physical line with any other top-level statement
(e.g. via a ``;``-joined ``from siva.spec_api import *; x = 1``) -- that would
silently discard the other statement, so :func:`_install_dsl_namespace_header`
rejects it with a ``SyntaxError`` instead of guessing what to keep.

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


# Canonical stub module every spec imports so an editor/language server
# resolves the DSL names. Rewritten to the binding preamble at runtime (see
# _install_dsl_namespace_header); the real module need not exist.
SPEC_IMPORT_MODULE = "siva.spec_api"

# The canonical header line every spec must begin with. Named in the
# missing-header error so the agent knows exactly what to add.
SPEC_IMPORT_HEADER = f"from {SPEC_IMPORT_MODULE} import *"


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

    Carries Monty's own (cleaned) message and line information in the message
    text itself (see :func:`_monty_message`). When Monty's traceback exposes a
    precise position, the exception also carries ``lineno``/``offset``/
    ``end_lineno``/``end_offset`` attributes (mirroring the standard
    ``SyntaxError`` interface) so a caller can render a source excerpt with a
    caret, the way ``siva.hot_reload`` does for the agent-facing build report.
    These default to ``None`` when Monty's traceback doesn't have a usable
    frame. Syntax failures are raised as ``SyntaxError`` instead (see
    :func:`_syntax_error_from_monty`).
    """

    lineno = None
    offset = None
    end_lineno = None
    end_offset = None


# --------------------------------------------------------------------------
# Preprocessing: enforce the header and substitute the binding preamble
# --------------------------------------------------------------------------

def _is_spec_module(name):
    """True if *name* is the spec stub module or a submodule of it."""
    return name == SPEC_IMPORT_MODULE or name.startswith(SPEC_IMPORT_MODULE + ".")


def _is_spec_import(node):
    """True if *node* is a top-level import of the spec stub module."""
    if isinstance(node, _ast.Import):
        return any(_is_spec_module(alias.name) for alias in node.names)
    if isinstance(node, _ast.ImportFrom):
        return node.level == 0 and bool(node.module) and _is_spec_module(node.module)
    return False


def _is_module_docstring(node):
    """True if *node* is a leading module-docstring ``Expr`` (a string constant)."""
    return (
        isinstance(node, _ast.Expr)
        and isinstance(node.value, _ast.Constant)
        and isinstance(node.value.value, str)
    )


def _line_span(node):
    """The inclusive 1-based physical line range a statement occupies."""
    start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    return start, end


def _install_dsl_namespace_header(code, preamble):
    """Require the spec's canonical header and swap it for *preamble* in place.

    Every spec must begin with :data:`SPEC_IMPORT_HEADER`
    (``from siva.spec_api import *``) as its first top-level statement -- an
    optional module docstring may precede it, but nothing else. That header
    line is rewritten to *preamble* -- a single physical line that binds each
    DSL form to its ``__siva_<name>`` alias, making every verb a module global
    that shadows any same-named Monty builtin (see the module docstring). The
    rewrite is one physical line for one physical line, so all later lines keep
    their original numbers and error messages need no offset correction.

    If the header is absent or is not the first statement (after an optional
    docstring), a :class:`SyntaxError` naming the exact required line is raised
    -- the agent that submitted the spec sees it through the same channel as
    any other spec syntax error.

    Because the rewrite blanks a whole physical line, a spec-import statement
    must not share a physical line with any other top-level statement (e.g.
    ``from siva.spec_api import *; x = 1``, or a stray later spec import
    joined to real code by a ``;``) -- blanking that line would silently
    discard the other statement. Such sharing is rejected with a
    :class:`SyntaxError` naming the offending line, rather than guessing which
    statement to keep.

    Parsing untrusted code with ``ast.parse`` is side-effect-free. If the code
    does not parse at all we return it unchanged and let Monty surface the real
    syntax error with correct line numbers (the spec cannot run anyway, so the
    missing preamble is moot).
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return code

    body = tree.body
    header_index = 1 if body and _is_module_docstring(body[0]) else 0
    header_is_first = len(body) > header_index and _is_spec_import(body[header_index])
    if not header_is_first:
        raise SyntaxError(
            f"SIVA spec must begin with '{SPEC_IMPORT_HEADER}' as its first "
            f"statement (it binds the DSL forms) -- an optional module "
            f"docstring may precede it, but nothing else. Add that line at "
            f"the top of the spec. Note: an empty view file must still carry "
            f"the header line '{SPEC_IMPORT_HEADER}'; a header-only file (just "
            f"that line, nothing else) clears the view -- whitespace-only "
            f"files are not a supported way to clear a view."
        )
    header_node = body[header_index]

    spec_imports = [node for node in body if _is_spec_import(node)]

    # A spec-import statement sharing a physical line with another top-level
    # statement (typically ';'-joined) would have that other statement
    # silently blanked below -- refuse instead of discarding user code.
    for imp in spec_imports:
        imp_start, imp_end = _line_span(imp)
        imp_lines = set(range(imp_start, imp_end + 1))
        for other in body:
            if other is imp:
                continue
            other_start, other_end = _line_span(other)
            if imp_lines & set(range(other_start, other_end + 1)):
                raise SyntaxError(
                    f"line {imp.lineno}: the '{SPEC_IMPORT_HEADER}'-style "
                    f"import must be alone on its own physical line -- it "
                    f"shares a line with another statement (e.g. joined by "
                    f"';'). The sandbox rewrites that whole line in place and "
                    f"would otherwise silently discard the other statement. "
                    f"Put the import on its own line."
                )

    # Blank every spec-import statement in place (a stray later one would fail
    # at runtime), and put the binding preamble on the first line of the header.
    lines = code.split("\n")
    for node in spec_imports:
        start, end = _line_span(node)
        for lineno in range(start, end + 1):
            if 1 <= lineno <= len(lines):
                lines[lineno - 1] = ""
        if node is header_node:
            lines[start - 1] = preamble
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


def _walk_target(node, add):
    """Add ``Name`` ids from an assignment/binding target.

    Shared by every binding form that can name a target: ``Assign``,
    ``AugAssign``, ``for``, ``with ... as``, and the walrus operator. Handles
    tuple/list unpacking, including a starred remainder (e.g. ``a, *rest =
    ...``) and a tuple/list target under ``with`` (``with ctx() as (a, b):``).
    """
    if isinstance(node, _ast.Name):
        add(node.id)
    elif isinstance(node, (_ast.Tuple, _ast.List)):
        for elt in node.elts:
            _walk_target(elt, add)
    elif isinstance(node, _ast.Starred):
        _walk_target(node.value, add)


class _AssignedNameCollector(_ast.NodeVisitor):
    """Collect names bound anywhere in a module's top-level scope.

    Walks the full tree (statements *and* expressions, since a walrus can bind
    from inside an expression), but stops at the boundary of any nested
    function/lambda/class scope -- names bound there are not module globals.
    Binding forms covered: ``Assign`` and ``AnnAssign`` (including tuple/list/
    starred unpacking), ``AugAssign``, ``for``/``async for`` targets, ``with``/
    ``async with ... as`` targets, and the walrus operator (``NamedExpr``,
    which binds in the *enclosing* scope per PEP 572 -- including from inside
    a comprehension, which is why comprehensions are not treated as a scope
    boundary here even though their loop variables are not collected).
    """

    def __init__(self):
        self.names = []
        self._seen = set()

    def _add(self, name):
        if name not in self._seen:
            self._seen.add(name)
            self.names.append(name)

    # Nested scopes: bind their own locals, not module globals. Don't descend.
    def visit_FunctionDef(self, node):
        pass

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_Lambda(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    def visit_Assign(self, node):
        for target in node.targets:
            _walk_target(target, self._add)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, _ast.Name):
            self._add(node.target.id)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        _walk_target(node.target, self._add)
        self.generic_visit(node)

    def visit_For(self, node):
        _walk_target(node.target, self._add)
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        _walk_target(node.target, self._add)
        self.generic_visit(node)

    def visit_withitem(self, node):
        if node.optional_vars is not None:
            _walk_target(node.optional_vars, self._add)
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        _walk_target(node.target, self._add)
        self.generic_visit(node)


def _collect_assigned_names(code):
    """Collect the names bound by assignments anywhere in *code*.

    Parsing untrusted code with ``ast.parse`` is safe (no execution). See
    :class:`_AssignedNameCollector` for exactly which binding forms are
    covered and which scopes are excluded. Conditionally-bound names (e.g.
    ``if cond: t = threshold(...)``) are included since control-flow bodies
    are ordinary child nodes, not scope boundaries; callers tolerate names
    that turn out to be unbound at recovery time (a branch not taken).

    Returns names in a stable, de-duplicated order.
    """
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return []

    collector = _AssignedNameCollector()
    collector.visit(tree)
    return collector.names


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
    canonical ``NodeRef`` instances.

    The spec must begin with the canonical header ``from siva.spec_api import
    *`` (see :func:`_install_dsl_namespace_header`); the header line is
    rewritten in place to a binding preamble that aliases every DSL form so it
    shadows any same-named Monty builtin. A missing header raises a
    ``SyntaxError`` naming the required line.
    """
    original_code = code  # for name recovery: no preamble, pristine user names

    table = {}  # canonical node_id -> NodeRef, populated by the wrappers
    methods = _builder_callables(namespace)
    # Each builder wrapper is registered under a non-colliding alias key so it
    # can never be shadowed by a Monty builtin; the preamble (one physical
    # line) binds the real DSL name to that alias at module scope, which does
    # shadow the builtin. Both derive from the same introspected methods.
    external = {
        f"__siva_{name}": _make_wrapper(method, table)
        for name, method in methods.items()
    }
    preamble = "; ".join(f"{name} = __siva_{name}" for name in methods)
    code = _install_dsl_namespace_header(code, preamble)

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
        raise _syntax_error_from_monty(e) from e
    except pm.MontyError as e:
        raise _sandbox_error_from_monty(e) from e

    # Recover bindings: read each assigned name back from the persistent REPL
    # namespace. Names that are unbound (a branch not taken) or hold
    # non-representable values raise inside feed_run and are skipped. Returned
    # handles are resolved back to canonical NodeRefs.
    #
    # We collect names from the *original* user code (before the preamble was
    # spliced in), so the preamble's infra bindings (the DSL verb names and
    # their ``__siva_*`` aliases) are never seen as user variables. As a belt
    # -and-suspenders guard we also drop any DSL verb / alias name explicitly.
    bindings = {}
    for name in _collect_assigned_names(original_code):
        if name in methods or name.startswith("__siva_"):
            continue
        try:
            value = repl.feed_run(name, external_functions=external)
        except pm.MontyError:
            continue
        bindings[name] = _from_handles(value, table)
    return bindings


def _monty_frame_position(exc):
    """Return ``(line, column, end_line, end_column)`` from a Monty error's
    innermost traceback frame, or all ``None`` if no usable frame is exposed.

    Monty's ``Frame`` carries 1-based ``line``/``column`` (and matching
    ``end_line``/``end_column``) alongside the ``source_line`` text; we only
    use the position here; callers get the source text from the user's own
    spec code rather than Monty's copy, so it reflects exactly what the agent
    wrote (see the module docstring's header-substitution scheme -- Monty's
    own copy has the header line rewritten to the binding preamble).
    """
    try:
        frames = exc.traceback()
    except Exception:
        frames = None
    if not frames:
        return None, None, None, None
    frame = frames[-1]
    line = getattr(frame, "line", None)
    if not isinstance(line, int) or line < 1:
        return None, None, None, None
    column = getattr(frame, "column", None)
    end_line = getattr(frame, "end_line", None)
    end_column = getattr(frame, "end_column", None)
    return line, column, end_line, end_column


def _monty_message(exc):
    """Build a readable message from a Monty error, naming the spec line
    and (when Monty exposes one) the column.

    Monty's ``str(exc)`` already names the underlying error kind (e.g.
    ``NameError: ...``) but omits the position; we append the user-code line
    (and column, if available) from the structured traceback. No offset
    correction is needed: the user code is fed as its own REPL snippet, so its
    reported line numbers are already relative to the spec.
    """
    msg = str(exc).strip()
    line, column, _end_line, _end_column = _monty_frame_position(exc)
    if line is None:
        return msg
    if column is not None:
        return f"{msg} (spec.py line {line}, column {column})"
    return f"{msg} (spec.py line {line})"


def _sandbox_error_from_monty(exc):
    """Build a :class:`SandboxError` from a non-syntax Monty error.

    Carries the same "spec.py line N[, column C]" message :func:`_monty_message`
    always produced, plus (when available) the raw position as
    ``lineno``/``offset``/``end_lineno``/``end_offset`` attributes for a caller
    that wants to render a source excerpt (``siva.hot_reload`` does, in its
    build report).
    """
    err = SandboxError(_monty_message(exc))
    line, column, end_line, end_column = _monty_frame_position(exc)
    err.lineno = line
    err.offset = column
    err.end_lineno = end_line if isinstance(end_line, int) else line
    err.end_offset = end_column
    return err


def _syntax_error_from_monty(exc):
    """Build a :class:`SyntaxError` from a ``MontySyntaxError``.

    The message passed to the constructor is Monty's raw (cleaned) message,
    with no location suffix -- setting ``.lineno`` below makes the standard
    library's own ``SyntaxError.__str__`` append a ``(..., line N)`` suffix
    automatically (without a column), which keeps existing callers that just
    do ``str(exc)`` working. A caller that wants the column too (again,
    ``siva.hot_reload``) reads ``.lineno``/``.offset``/``.end_lineno``/
    ``.end_offset`` directly -- the same attributes CPython itself sets on a
    real ``SyntaxError`` -- and pairs them with the user's own source line
    (Monty's ``source_line`` reflects its rewritten copy of the header line,
    not the user's original, so we don't use it here).
    """
    msg = str(exc).strip()
    err = SyntaxError(msg)
    line, column, end_line, end_column = _monty_frame_position(exc)
    if line is not None:
        err.lineno = line
        err.offset = column
        err.end_lineno = end_line if isinstance(end_line, int) else line
        err.end_offset = end_column
    return err
