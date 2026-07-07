#!/usr/bin/env python3
"""Generate ``siva/spec_api.py``, the editor-facing stub for the spec (DSL) namespace.

Every spec file must begin with ``from siva.spec_api import *`` (enforced by
``siva.sandbox._install_dsl_namespace_header``); at runtime that header is
rewritten to a binding preamble and the module named here never actually
loads. Its only job is to give an editor's language server (Pylance/pyright)
something real to resolve when a human or agent edits a spec file. See the
module docstring of ``siva/sandbox.py`` for the full runtime contract.

This script is the single source of truth for that stub: it introspects the
*exact* DSL surface the sandbox exposes -- the bound public methods of
``PipelineBuilder`` as collected by ``siva.dsl._make_namespace`` and
``siva.sandbox._builder_callables`` -- and renders one module-level function
per DSL verb, carrying over the real parameter signature and docstring
verbatim. Nothing here executes; every function body is ``...``.

Run from anywhere:
    python scripts/gen_spec_api.py

The script is idempotent -- running it twice produces the same output.
``tests/test_spec_api.py`` fails CI if the checked-in file drifts from what
this script would produce.
"""

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from siva.dsl import PipelineBuilder, _make_namespace  # noqa: E402
from siva.sandbox import _builder_callables  # noqa: E402

OUTPUT_PATH = PROJECT_ROOT / "siva" / "spec_api.py"
REGEN_COMMAND = "python scripts/gen_spec_api.py"


# ---------------------------------------------------------------------------
# Introspection: which parameters/returns are NodeRef-typed
# ---------------------------------------------------------------------------

def _arg_docs(doc):
    """Return {param_name: description} parsed from a docstring's Args: block.

    Google-style docstrings (as used throughout ``siva/dsl.py``) list each
    parameter on its own line, indented 4 spaces under ``Args:``, with
    continuation lines indented further. We only need enough of that
    structure to know, per parameter, whether its description mentions
    ``NodeRef`` -- that is the signal a parameter carries a pipeline handle.
    """
    if not doc:
        return {}
    lines = doc.split("\n")
    try:
        start = lines.index("Args:") + 1
    except ValueError:
        return {}
    descriptions = {}
    name = None
    buf = []
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break  # next top-level section (Returns:, Notes:, ...)
        match = re.match(r"^ {4}(\*{0,2}\w+)\b(.*)", line)
        if match:
            if name is not None:
                descriptions[name] = "\n".join(buf)
            name = match.group(1).lstrip("*")
            buf = [match.group(2)]
        elif name is not None:
            buf.append(line)
    if name is not None:
        descriptions[name] = "\n".join(buf)
    return descriptions


def _noderef_params(method):
    """Return the set of parameter names whose docstring marks them as NodeRef."""
    arg_docs = _arg_docs(inspect.getdoc(method))
    return {name for name, desc in arg_docs.items() if "NodeRef" in desc}


def _returns_noderef(method):
    """True if *method*'s body contains a ``return <value>`` (not bare ``return``).

    Every DSL verb either builds and returns a new ``NodeRef`` (the common
    case) or records state on the builder and returns nothing (``show``,
    ``camera``, ``background``, ``title``, ``annotate``, ``axes``). A bare
    function body scan is enough to tell those two shapes apart -- no verb
    returns anything other than a ``NodeRef`` or ``None``.
    """
    source = textwrap.dedent(inspect.getsource(method.__func__))
    tree = ast.parse(source)
    func_def = tree.body[0]
    return any(
        isinstance(node, ast.Return) and node.value is not None
        for node in ast.walk(func_def)
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_param(param, noderef_names):
    """Render one ``inspect.Parameter`` as stub source text."""
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        return f"*{param.name}"
    if param.kind is inspect.Parameter.VAR_KEYWORD:
        return f"**{param.name}"

    is_noderef = param.name in noderef_names
    if not is_noderef:
        annotation = ""
    elif param.default is None:
        # Many node-typed params default to None (e.g. a filter that can
        # create its own geometry) -- NodeRef alone would make that default
        # a type error, so widen to NodeRef | None.
        annotation = ": NodeRef | None"
    else:
        annotation = ": NodeRef"
    if param.default is inspect.Parameter.empty:
        return f"{param.name}{annotation}"
    # PEP 8: spaces around `=` for an annotated default, none otherwise.
    equals = " = " if is_noderef else "="
    return f"{param.name}{annotation}{equals}{param.default!r}"


def _render_docstring(doc, indent="    "):
    """Render *doc* as an indented triple-quoted docstring block.

    Matches the style used throughout ``siva/dsl.py``: the summary line sits
    directly after the opening ``\"\"\"``, the closing ``\"\"\"`` is on its own
    line, and blank lines stay bare (no trailing whitespace).
    """
    first, *rest = doc.split("\n")
    lines = [f'{indent}"""{first}']
    lines += [f"{indent}{line}" if line else "" for line in rest]
    lines.append(f'{indent}"""')
    return "\n".join(lines)


def _render_function(name, method):
    """Render one DSL verb as a module-level stub function definition."""
    # inspect.signature on the bound method already drops `self`.
    signature = inspect.signature(method)
    noderef_names = _noderef_params(method)
    params = ", ".join(
        _render_param(param, noderef_names) for param in signature.parameters.values()
    )
    returns = " -> NodeRef" if _returns_noderef(method) else " -> None"

    doc = inspect.getdoc(method) or ""

    lines = [f"def {name}({params}){returns}:"]
    if doc:
        lines.append(_render_docstring(doc))
    lines.append("    ...")
    return "\n".join(lines)


HEADER = f'''# GENERATED FILE -- DO NOT EDIT.
#
# Generated by scripts/gen_spec_api.py from the PipelineBuilder DSL surface
# (siva.dsl._make_namespace / siva.sandbox._builder_callables). Regenerate
# with:
#
#     {REGEN_COMMAND}
'''

MODULE_DOCSTRING = '''"""Editor-facing stub for the SIVA spec (DSL) namespace.

Every SIVA spec (a ``view-*.py`` pipeline file executed by
``siva.sandbox.execute``) must begin with::

    from siva.spec_api import *

That import is never actually resolved at runtime -- the sandbox rewrites the
line in place to a binding preamble before the spec ever reaches Monty (see
the module docstring of ``siva/sandbox.py``). This module exists purely so an
editor's language server (Pylance/pyright) has something real to resolve: it
gives every DSL verb its real parameter signature and docstring, so specs get
autocomplete, hover docs, and undefined-name checking while being edited.

Nothing here executes. Every function body is ``...`` -- calling one of these
directly (outside the sandbox) does nothing and returns ``None``.
"""'''

NODEREF_CLASS = '''class NodeRef:
    """Opaque handle to a pipeline node.

    Returned by DSL forms that build pipeline nodes (``source()``,
    ``filter()``, ``contour()``, ...) and passed as ``input=`` (or another
    node-typed argument) to downstream forms. It has no usable attributes or
    methods here -- this stub only stands in for type-checking; the real
    value the sandbox holds per node is an opaque id (see
    ``siva.sandbox.NodeHandle``), and the real construction-time value is
    ``siva.dsl.NodeRef``, which never leaves the host process.
    """

    ...'''


def generate():
    """Return the full generated module source as a string."""
    builder = PipelineBuilder()
    namespace = _make_namespace(builder)
    methods = _builder_callables(namespace)

    verb_names = sorted(methods)
    all_names = ["NodeRef", "math"] + verb_names

    parts = [
        HEADER,
        MODULE_DOCSTRING,
        "",
        # Defers annotation evaluation to strings (PEP 563), so `NodeRef |
        # None` below type-checks under pyright without requiring Python
        # 3.10 at runtime -- this module is never actually executed (see the
        # module docstring), but it must still be *importable* Python on
        # whatever interpreter runs the sync test.
        "from __future__ import annotations",
        "",
        "import math",
        "",
        "__all__ = [",
    ]
    parts += [f"    {name!r}," for name in all_names]
    parts += [
        "]",
        "",
        "",
        NODEREF_CLASS,
    ]
    for name in verb_names:
        parts.append("")
        parts.append("")
        parts.append(_render_function(name, methods[name]))

    return "\n".join(parts) + "\n"


def main():
    content = generate()
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"  wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)}  ({len(content):,} chars)")


if __name__ == "__main__":
    main()
