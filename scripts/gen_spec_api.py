#!/usr/bin/env python3
"""Generate the editor-facing stubs for the spec (DSL) namespace.

Every spec file must begin with ``from siva.spec_api import *`` (enforced by
``siva.sandbox._install_dsl_namespace_header``); at runtime that header is
rewritten to a binding preamble and the modules named here never actually
load. Their only job is to give an editor's language server (Pylance/pyright)
something real to resolve when a human or agent edits a spec file. See the
module docstring of ``siva/sandbox.py`` for the full runtime contract.

This script is the single source of truth for two generated modules:

``siva/spec_api.py``
    One module-level function per DSL verb, carrying the real parameter
    signature and docstring verbatim. ``source`` / ``filter`` / ``background``
    are rendered as ``@overload`` sets so completions are class- (or
    argument-) specific; every other verb is a plain stub.

``siva/_spec_api_props.py``
    The typing foundation the verbs lean on: the opaque ``NodeRef`` handle, the
    closed-enum ``Literal`` aliases (colormaps, scalar types, background
    presets, representations), and one ``TypedDict`` per whitelisted VTK class
    describing that class's settable ``**props``. ``spec_api`` star-imports it.

Both files are pure typing surface -- nothing here executes, every function
body is ``...``, and neither module imports vtk, so they stay importable
without a display. The introspection that *builds* them (below) does touch vtk,
but only at generation time.

Run from anywhere:
    python scripts/gen_spec_api.py

The script is deterministic -- sorted iteration everywhere -- so running it
twice produces byte-identical output. ``tests/test_spec_api.py`` fails CI if
either checked-in file drifts from what this script would produce.
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

import vtk  # noqa: E402

from siva.colormaps import PRESETS  # noqa: E402
from siva.dsl import PipelineBuilder, _make_namespace  # noqa: E402
from siva.filters import (  # noqa: E402
    SCALAR_TYPE_MAP,
    WHITELISTED_CLASSES,
    _SPECIAL_CASE_KEYS,
)
from siva.sandbox import _builder_callables  # noqa: E402
from siva._vtk_introspect import vtk_setter_names  # noqa: E402

API_PATH = PROJECT_ROOT / "siva" / "spec_api.py"
PROPS_PATH = PROJECT_ROOT / "siva" / "_spec_api_props.py"
REGEN_COMMAND = "python scripts/gen_spec_api.py"

# Setters every VTK algorithm/object carries (AbortExecute, ReferenceCount,
# ...). They are technically valid runtime kwargs but never meaningful spec
# properties, so we subtract them to keep each TypedDict reading like a real
# API reference rather than a dump of framework plumbing. The generated keys
# stay a subset of the runtime validator's ``vtk_setter_names`` either way.
_BASE_SETTERS = vtk_setter_names(vtk.vtkObject) | vtk_setter_names(vtk.vtkAlgorithm)


# ---------------------------------------------------------------------------
# SIVA-level deviations from raw VTK
# ---------------------------------------------------------------------------

# The authoritative catalogue of every property the DSL layer accepts *on top
# of* (or with a different type than) a plain VTK ``Set<Name>(value)`` call.
# Read alongside ``siva.filters.create_vtk_filter`` / ``_apply_properties`` and
# the wrapper verbs in ``siva.dsl``: each entry is either a key with no VTK
# setter (a genuine SIVA add-on) or a type override for a setter whose SIVA
# meaning is richer than the raw signature. Values are rendered type
# expressions.
#
# Overrides that can be *derived by introspection* (mode families, scalar-type
# readers, implicit-function cut/clip) are NOT hand-listed here -- they are
# generated in ``_derived_extras`` so a newly whitelisted class picks them up
# automatically. This dict holds only the entries that cannot be derived: SIVA
# add-on keys with no matching VTK setter, and the two name->enum tables
# (``GlyphMode`` / ``IntegrationDirection`` / ``IntegratorType``) whose accepted
# values come from a hard-coded runtime dict rather than VTK method names.
SIVA_FILTER_EXTRAS: dict[str, dict[str, str]] = {
    "vtkContourFilter": {
        "ContourBy": "str",                        # SetInputArrayToProcess: scalar name
        "Isosurfaces": "float | Sequence[float]",  # SetValue(i, v) per entry
    },
    "vtkThreshold": {
        "ThresholdBy": "str",
        "ThresholdRange": "Sequence[float]",       # -> SetLowerThreshold/SetUpperThreshold
    },
    "vtkExtractGrid": {"Bounds": "Sequence[float]"},   # physical coords, converted to VOI
    "vtkExtractVOI": {"Bounds": "Sequence[float]"},
    "vtkGradientFilter": {"GradientField": "str"},
    "vtkArrayCalculator": {
        "AddScalarArrayName": "Sequence[str]",     # AddScalarArrayName(name) per entry
        "AddVectorArrayName": "Sequence[str]",
    },
    "vtkGlyph3D": {
        "GlyphSource": "NodeRef",                  # a source node supplying the glyph geometry
        "ScaleArray": "str",
        "OrientationArray": "str",
        # SetGlyphMode(int) via a name->enum table in _apply_properties:
        "GlyphMode": 'Literal["AllPoints", "EveryNthPoint", "UniformSpatialDistribution"]',
    },
    "vtkWarpScalar": {"Vectors": "str"},
    "vtkWarpVector": {"Vectors": "str"},
    "vtkStreamTracer": {
        "Vectors": "str",
        "SeedSource": "NodeRef",                   # a source node supplying seed points
        # name->enum tables in _apply_properties:
        "IntegrationDirection": 'Literal["Forward", "Backward", "Both"]',
        "IntegratorType": 'Literal["RungeKutta2", "RungeKutta4", "RungeKutta45"]',
    },
}

# Special-case keys the runtime dispatches via ``Set<key>To<value>()`` -- the
# accepted values are exactly the ``Set<key>To<Value>()`` zero-argument methods
# the class exposes, so we render them as a ``Literal`` read straight off the
# class (``_mode_family_literal``) rather than hand-listing per class. Keeping
# the *key* set here (rather than a per-class entry) means a newly whitelisted
# class exposing ``SetVectorMode`` / ``SetTensorMode`` is covered automatically.
_MODE_FAMILY_KEYS = frozenset({"VectorMode", "TensorMode"})

# Special-case keys whose *runtime* value type diverges from the plain VTK
# ``Set<key>(value)`` signature: the runtime accepts name-strings / scalar-type
# strings, while VTK types the setter as a bare int. For these the naive
# introspected type is wrong, so every whitelisted class exposing the setter
# must carry an enriched override (hand-listed in ``SIVA_FILTER_EXTRAS`` or
# produced by ``_derived_extras``). ``_audit_special_case_coverage`` asserts
# that below, so a newly whitelisted class can't silently regress.
#
# This is deliberately narrower than "every ``_SPECIAL_CASE_KEYS`` setter needs
# an override": most special-case keys (``FileName``, ``VOI``, ``SampleRate``,
# ``DataExtent``, ``LowPoint`` / ``HighPoint``, ``OnRatio``, ...) are dispatched
# with ``*value`` or a same-typed scalar, so their introspected type is already
# correct and an override would only restate it. Enforcing overrides on those
# would be noise, not safety.
_TYPE_DIVERGING_SPECIAL_KEYS = (
    _MODE_FAMILY_KEYS
    | {"GlyphMode", "IntegrationDirection", "IntegratorType"}  # name->enum tables
    | {"DataScalarType"}                                        # scalar-type name -> constant
)

# Verb parameters that are explicit in the signature but carry a closed set of
# string values. Keyed by ``(verb, param)`` -> rendered annotation.
ENUM_PARAM_ANNOTATIONS = {
    ("raw_source", "scalar_type"): "ScalarTypeName | int",
}

# ``show(**display_props)`` is intentionally open-ended, so it keeps its
# untyped ``**display_props`` catch-all; we only surface the two display props
# whose values are closed enums as typed keyword-only parameters in front of
# it. Anything else the author passes still routes to ``**display_props``.
SHOW_ENUM_KWARGS = {
    "lut": "ColormapName",
    "representation": "Representation",
}

# ``show(..., representation=)`` accepts these four values: three map through
# ``create_show``'s ``rep_map`` (Surface/Wireframe/Points) and "Volume" takes
# the separate volume-rendering path. Small enough, and split across two code
# sites, to state here rather than introspect.
REPRESENTATIONS = ("Points", "Surface", "Volume", "Wireframe")


# ---------------------------------------------------------------------------
# Introspection: NodeRef-typed params / returns (docstring + AST driven)
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


def _wrapped_class(method):
    """Return the whitelisted VTK class a wrapper verb forwards ``**props`` to.

    Named verbs like ``contour``/``threshold``/``slice`` wrap a fixed VTK class
    and pass ``**props`` straight through; we find that class by scanning the
    verb body for a ``self.filter("vtkXxx", ...)`` or ``self.source("vtkXxx",
    ...)`` call with a string-literal class name. Returns ``None`` for verbs
    with no such call (``source``/``filter`` themselves, ``show``, ...).
    """
    source = textwrap.dedent(inspect.getsource(method.__func__))
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("filter", "source")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in WHITELISTED_CLASSES
        ):
            return node.args[0].value
    return None


def _background_presets(method):
    """Return the sorted preset names from ``background``'s local ``presets`` dict.

    Read out of the method body by AST rather than duplicated here, so the
    two never drift.
    """
    source = textwrap.dedent(inspect.getsource(method.__func__))
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "presets" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return sorted(k.value for k in node.value.keys)
    raise RuntimeError("could not find a `presets` dict in background()")


# ---------------------------------------------------------------------------
# Introspection: VTK setter value types (parsed from setter __doc__ signatures)
# ---------------------------------------------------------------------------

def _split_top_level(text):
    """Split *text* on top-level commas, respecting (), [], {} nesting."""
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts]


def _setter_arg_annotations(instance, prop):
    """Yield, per complete ``Set<prop>`` overload, its list of arg annotations.

    VTK's Python bindings put the (stub-derived) signatures at the top of each
    method's ``__doc__``, e.g. ``SetCenter(self, _arg:(float, float, float)) ->
    None``. We read the annotation of every argument after ``self`` from each
    line that carries a complete ``(...) ->`` signature (wrapped/truncated
    lines are skipped -- vector setters always also list a complete tuple
    overload).
    """
    method = getattr(instance, "Set" + prop, None)
    doc = getattr(method, "__doc__", "") or ""
    pattern = re.compile(rf"^Set{re.escape(prop)}\((?P<params>.*?)\)\s*->")
    for line in doc.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        annotations = []
        for param in _split_top_level(match.group("params")):
            if param == "self" or not param:
                continue
            annotations.append(param.split(":", 1)[1].strip() if ":" in param else "Any")
        yield annotations


def _scalar_type(annotation):
    """Map a single VTK arg annotation to a scalar type name, or ``Any``."""
    token = annotation.strip().strip("'\"")
    return token if token in ("float", "int", "bool", "str") else "Any"


def _combine_scalars(scalars):
    """Reduce a set of scalar type names to one permissive annotation."""
    kinds = set(scalars)
    if not kinds or "Any" in kinds:
        return "Any"
    if kinds <= {"int", "float", "bool"}:
        # PEP 484: int/bool satisfy float, so widen a mixed numeric set.
        return "float" if "float" in kinds else ("int" if "int" in kinds else "bool")
    if kinds == {"str"}:
        return "str"
    return "Any"


def _sequence_element(annotation):
    """If *annotation* is a tuple/sequence type, return its element scalar; else None."""
    text = annotation.strip()
    if text.startswith("(") and text.endswith(")"):
        elements = [_scalar_type(e) for e in _split_top_level(text[1:-1])]
        return _combine_scalars(elements) if elements else "Any"
    match = re.match(r"^(?:Sequence|List|Tuple)\[(.+)\]$", text)
    if match:
        elements = [_scalar_type(e) for e in _split_top_level(match.group(1)) if e != "..."]
        return _combine_scalars(elements) if elements else "Any"
    return None


def _setter_type(instance, prop):
    """Infer the rendered value type for ``Set<prop>`` from its signatures.

    Multi-argument and tuple/sequence setters (vectors, extents) become
    ``Sequence[...]`` -- deliberately not fixed-length tuples, since the
    wrappers accept lists and tuples alike. Single scalar setters keep their
    scalar type. Anything unrecognised (VTK object arguments, unparseable
    signatures) falls back to ``Any``.
    """
    is_sequence = False
    elements = set()
    for annotations in _setter_arg_annotations(instance, prop):
        if not annotations:
            continue
        if len(annotations) >= 2:
            is_sequence = True
            elements.update(_scalar_type(a) for a in annotations)
        else:
            element = _sequence_element(annotations[0])
            if element is not None:
                is_sequence = True
                elements.add(element)
            else:
                elements.add(_scalar_type(annotations[0]))
    if not elements:
        return "Any"
    combined = _combine_scalars(elements)
    if combined == "Any":
        return "Any"
    return f"Sequence[{combined}]" if is_sequence else combined


def _is_toggle_setter(instance, prop):
    """True if every parsed ``Set<prop>`` overload takes no value argument.

    These are the ``Set<X>To<Y>()`` mode toggles (and the rare zero-arg
    ``Set*``): callable ``Set*`` names that set nothing, so they are noise in a
    props reference rather than real properties. Kept only when *no* signature
    parses (an unusual doc we would rather surface as ``Any`` than silently drop).
    """
    signatures = list(_setter_arg_annotations(instance, prop))
    return bool(signatures) and all(len(args) == 0 for args in signatures)


def _mode_family_literal(instance, base):
    """Render a ``Literal`` of the zero-arg ``Set<base>To<Value>()`` method values."""
    prefix = f"Set{base}To"
    values = []
    for name in dir(instance):
        if not name.startswith(prefix):
            continue
        doc = (getattr(getattr(instance, name), "__doc__", "") or "").strip()
        first = doc.splitlines()[0] if doc else ""
        if re.match(rf"^{re.escape(name)}\(self\)\s*->", first):  # takes no arguments
            values.append(name[len(prefix):])
    values = sorted(set(values))
    if not values:
        return "Any"
    return "Literal[" + ", ".join(repr(v) for v in values) + "]"


# ---------------------------------------------------------------------------
# Per-class props and source/filter classification
# ---------------------------------------------------------------------------

def _props_typename(vtk_class):
    """``"vtkConeSource"`` -> ``"VtkConeSourceProps"`` (deterministic)."""
    return vtk_class[0].upper() + vtk_class[1:] + "Props"


def _derived_extras(vtk_class, instance):
    """Return {property: rendered_type} for SIVA overrides derived by introspection.

    These mirror ``siva.filters._apply_properties`` special-cases whose set of
    accepted values (or its very existence) can be read off the VTK class, so
    they need no per-class hand-listing and a newly whitelisted class picks them
    up automatically:

    * ``VectorMode`` / ``TensorMode`` -- runtime dispatches ``Set<key>To<value>()``,
      so the accepted values are exactly that family's zero-arg methods.
    * ``DataScalarType`` -- runtime accepts a scalar-type *name* string (or a raw
      VTK int constant) for any reader exposing ``SetDataScalarType``.
    * ``CutFunction`` -- the ``{"type": "Plane"|"Sphere"|"Box", ...}`` dict is
      special-cased for the cutter *and* every clip class (it falls back to
      ``SetClipFunction``), and the clip*/slice wrapper verbs all build one
      internally -- so the direct ``filter()`` form must advertise it on any
      class exposing ``SetCutFunction`` or ``SetClipFunction``. (The introspected
      ``ClipFunction: Any`` setter is a different, spec-unusable key.)
    """
    setters = vtk_setter_names(instance)
    extras = {}
    for key in sorted(_MODE_FAMILY_KEYS & setters):
        extras[key] = _mode_family_literal(instance, key)
    if "DataScalarType" in setters:
        extras["DataScalarType"] = "ScalarTypeName | int"
    if hasattr(instance, "SetCutFunction") or hasattr(instance, "SetClipFunction"):
        extras["CutFunction"] = "dict[str, Any]"
    return extras


def _siva_extra_keys(vtk_class, instance):
    """All SIVA-level extra property names for *vtk_class* (hand-listed + derived).

    The runtime typo-validator accepts each of these on top of the plain VTK
    setters, so ``tests/test_spec_api.py`` uses this to bound the TypedDict keys.
    """
    return set(SIVA_FILTER_EXTRAS.get(vtk_class, {})) | set(_derived_extras(vtk_class, instance))


def _class_props(vtk_class, instance):
    """Return {property: rendered_type} for *vtk_class*: introspected + SIVA extras.

    Some setters take a VTK object (``SetInputData``, ``SetLocator``,
    ``SetClipFunction``, ...) or have a signature we can't narrow, so they render
    as ``key: Any``. We keep them rather than prune them: the TypedDict's contract
    is to be a faithful subset of the names the runtime typo-validator
    (``siva.filters._validate_vtk_kwargs``) accepts, so it never flags a kwarg the
    runtime would take. An ``Any`` key can't be constructed from spec code (there
    are no VTK objects in the sandbox), so it never *helps*, but it also never
    produces a false positive -- and telling those setters apart from merely
    imprecise scalar ones would be a fragile heuristic whose only payoff is
    slightly shorter autocomplete lists. Faithful-and-permissive wins.

    SIVA extras (hand-listed then derived) are layered last, so a richer type
    overrides the naive VTK-introspected one for the same key.
    """
    props = {}
    for setter in sorted(vtk_setter_names(instance) - _BASE_SETTERS):
        if setter.startswith("_") or _is_toggle_setter(instance, setter):
            continue
        props[setter] = _setter_type(instance, setter)
    props.update(SIVA_FILTER_EXTRAS.get(vtk_class, {}))
    props.update(_derived_extras(vtk_class, instance))
    return props


def _audit_special_case_coverage(instances):
    """Raise if a type-diverging special-case setter is left with its naive type.

    Guards the drift described in ``_TYPE_DIVERGING_SPECIAL_KEYS`` and the
    ``CutFunction`` note in ``_derived_extras``: for every whitelisted class,
    any exposed setter whose runtime value type diverges from the plain VTK
    signature must be enriched by an override rather than fall through to
    ``_setter_type``. Runs at generation time (see ``render_props_module``); the
    byte-identical freshness check in ``tests/test_spec_api.py`` then makes CI
    fail if a regeneration would trip this. A generation-time assertion is
    cleaner than a standalone test here because it keeps the invariant next to
    the extras that satisfy it and blocks a bad file from ever being written.
    """
    problems = []
    for name in sorted(WHITELISTED_CLASSES):
        instance = instances[name]
        setters = vtk_setter_names(instance)
        props = _class_props(name, instance)
        for key in sorted(_TYPE_DIVERGING_SPECIAL_KEYS & setters):
            if props.get(key) == _setter_type(instance, key):
                problems.append(f"{name}.{key}")
        if (hasattr(instance, "SetCutFunction") or hasattr(instance, "SetClipFunction")) \
                and "CutFunction" not in props:
            problems.append(f"{name}.CutFunction")
    if problems:
        raise RuntimeError(
            "special-case setters left with their naive VTK type (add an override "
            "to SIVA_FILTER_EXTRAS or _derived_extras): " + ", ".join(sorted(problems))
        )


def _is_source(instance):
    """True for readers/sources (no input port); False for filters (>= 1)."""
    return instance.GetNumberOfInputPorts() == 0


# ---------------------------------------------------------------------------
# Rendering: shared pieces
# ---------------------------------------------------------------------------

def _render_docstring(doc, indent="    "):
    r"""Render *doc* as an indented triple-quoted docstring block.

    Matches the style used throughout ``siva/dsl.py``: the summary line sits
    directly after the opening ``\"\"\"``, the closing ``\"\"\"`` is on its own
    line, and blank lines stay bare (no trailing whitespace).
    """
    first, *rest = doc.split("\n")
    lines = [f'{indent}"""{first}']
    lines += [f"{indent}{line}" if line else "" for line in rest]
    lines.append(f'{indent}"""')
    return "\n".join(lines)


def _render_param(param, noderef_names, enum_annotation=None):
    """Render one ordinary ``inspect.Parameter`` as stub source text."""
    if enum_annotation is not None:
        annotation = f": {enum_annotation}"
        equals = " = "
    elif param.name in noderef_names:
        # NodeRef params that default to None (a filter that can build its own
        # geometry) widen to NodeRef | None so the default is not a type error.
        annotation = ": NodeRef | None" if param.default is None else ": NodeRef"
        equals = " = "
    else:
        annotation = ""
        equals = "="  # PEP 8: no spaces around `=` for an unannotated default
    if param.default is inspect.Parameter.empty:
        return f"{param.name}{annotation}"
    return f"{param.name}{annotation}{equals}{param.default!r}"


def _build_params(verb, method, unpack_class):
    """Render a verb's full parameter list, applying enum + Unpack annotations."""
    noderef_names = _noderef_params(method)
    parts = []
    for param in inspect.signature(method).parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            parts.append(f"*{param.name}")
        elif param.kind is inspect.Parameter.VAR_KEYWORD:
            if unpack_class:
                parts.append(f"**{param.name}: Unpack[{_props_typename(unpack_class)}]")
            else:
                parts.append(f"**{param.name}")
        else:
            enum = ENUM_PARAM_ANNOTATIONS.get((verb, param.name))
            parts.append(_render_param(param, noderef_names, enum))
    return ", ".join(parts)


def _stub_body(doc):
    """Return the docstring (if any) plus the ``...`` body, as indented lines."""
    lines = []
    if doc:
        lines.append(_render_docstring(doc))
    lines.append("    ...")
    return lines


# ---------------------------------------------------------------------------
# Rendering: verb stubs
# ---------------------------------------------------------------------------

def _render_plain(verb, method):
    """Render an ordinary verb: real signature, docstring, ``...`` body."""
    unpack_class = _wrapped_class(method) if _has_var_keyword(method) else None
    params = _build_params(verb, method, unpack_class)
    returns = " -> NodeRef" if _returns_noderef(method) else " -> None"
    return "\n".join([f"def {verb}({params}){returns}:", *_stub_body(inspect.getdoc(method))])


def _render_show(method):
    """Render ``show`` with typed enum keyword params in front of ``**display_props``."""
    noderef_names = _noderef_params(method)
    head, kw_name = [], None
    for param in inspect.signature(method).parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            kw_name = param.name
        else:
            head.append(_render_param(param, noderef_names))
    injected = [f"{name}: {annotation} = ..." for name, annotation in SHOW_ENUM_KWARGS.items()]
    params = ", ".join([*head, "*", *injected, f"**{kw_name}"])
    return "\n".join([f"def show({params}) -> None:", *_stub_body(inspect.getdoc(method))])


def _render_overloaded(verb, method, overload_params, impl_params, returns):
    """Render a verb as ``@overload`` variants plus a docstring-bearing impl."""
    lines = []
    for params in overload_params:
        lines.append("@overload")
        lines.append(f"def {verb}({params}){returns}: ...")
    lines.append(f"def {verb}({impl_params}){returns}:")
    lines += _stub_body(inspect.getdoc(method))
    return "\n".join(lines)


def _render_source(method, source_classes):
    """Render ``source``: one overload per source/reader class, no escape hatch.

    There is deliberately *no* trailing ``(vtk_class: str, **props: Any)``
    catch-all overload. The runtime rejects any class not in the whitelist
    outright (``siva.filters.create_vtk_filter``), so a catch-all would only
    model calls that can never succeed while defeating the per-class TypedDict
    checking -- with it, pyright accepts every prop typo and wrong-typed value.
    Dropping it makes each ``source(...)`` call resolve against its class's
    overload, so a misspelled prop, a wrong-typed value, or a class name that
    isn't whitelisted is flagged.

    Tradeoff: the class name must be a string *literal* for pyright to pick the
    overload. A name held in a ``str`` variable matches no ``Literal[...]``
    overload and is flagged -- acceptable, since specs always pass a literal
    (and a non-literal couldn't be prop-checked anyway).
    """
    overloads = [
        f'vtk_class: Literal["{cls}"], **props: Unpack[{_props_typename(cls)}]'
        for cls in source_classes
    ]
    return _render_overloaded("source", method, overloads,
                              "vtk_class: str, **props: Any", " -> NodeRef")


def _render_filter(method, filter_classes):
    """Render ``filter``: one overload per filter class, no escape hatch.

    As with ``source`` (see ``_render_source``), there is no trailing
    ``str``-typed catch-all overload: it would swallow every prop typo and
    wrong-typed value on ``filter(...)`` and can only describe calls the runtime
    would reject. Same string-literal tradeoff applies.
    """
    overloads = [
        f'vtk_class: Literal["{cls}"], input: NodeRef | None = ..., '
        f"**props: Unpack[{_props_typename(cls)}]"
        for cls in filter_classes
    ]
    return _render_overloaded("filter", method, overloads,
                              "vtk_class: str, input: NodeRef | None = None, **props: Any",
                              " -> NodeRef")


def _render_background(method):
    """Render ``background``: a preset overload and an (r, g, b) overload."""
    presets = _background_presets(method)
    literal = "Literal[" + ", ".join(repr(p) for p in presets) + "]"
    overloads = [f"preset: {literal}, /", "r: float, g: float, b: float, /"]
    return _render_overloaded("background", method, overloads, "*args: Any", " -> None")


def _render_verb(verb, method, source_classes, filter_classes):
    """Dispatch a verb to its renderer (overloaded / show / plain)."""
    if verb == "source":
        return _render_source(method, source_classes)
    if verb == "filter":
        return _render_filter(method, filter_classes)
    if verb == "background":
        return _render_background(method)
    if verb == "show":
        return _render_show(method)
    return _render_plain(verb, method)


def _has_var_keyword(method):
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in inspect.signature(method).parameters.values()
    )


# ---------------------------------------------------------------------------
# Rendering: whole modules
# ---------------------------------------------------------------------------

def _header(source_module):
    return (
        "# GENERATED FILE -- DO NOT EDIT.\n"
        "#\n"
        f"# Generated by scripts/gen_spec_api.py from {source_module}.\n"
        "# Regenerate with:\n"
        "#\n"
        f"#     {REGEN_COMMAND}\n"
    )


PROPS_DOCSTRING = '''"""Typing foundation for the editor-facing SIVA spec stub.

Companion to ``siva/spec_api.py`` (which star-imports this module): it holds the
opaque :class:`NodeRef` handle, the closed-enum ``Literal`` aliases, and one
``TypedDict`` per whitelisted VTK class enumerating that class's settable
``**props``. Each TypedDict is discovered exactly the way the runtime validator
discovers valid kwargs (``siva._vtk_introspect.vtk_setter_names``), plus the
SIVA-level extras layered on by ``siva.filters`` / the DSL wrapper verbs.

Nothing here executes and nothing imports vtk -- this is pure type surface.
"""'''

API_DOCSTRING = '''"""Editor-facing stub for the SIVA spec (DSL) namespace.

Every SIVA spec (a ``view-*.py`` pipeline file executed by
``siva.sandbox.execute``) must begin with::

    from siva.spec_api import *

That import is never actually resolved at runtime -- the sandbox rewrites the
line in place to a binding preamble before the spec ever reaches Monty (see
the module docstring of ``siva/sandbox.py``). This module exists purely so an
editor's language server (Pylance/pyright) has something real to resolve: it
gives every DSL verb its real parameter signature and docstring, and (via the
generated ``siva._spec_api_props`` it star-imports) class-specific ``**props``
completions and closed-enum checking, so specs get autocomplete, hover docs,
and undefined-name / bad-argument checking while being edited.

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


def render_props_module():
    """Return the full generated source of ``siva/_spec_api_props.py``."""
    builder = PipelineBuilder()
    instances = {name: cls() for name, cls in sorted(WHITELISTED_CLASSES.items())}
    _audit_special_case_coverage(instances)

    colormaps = sorted(PRESETS)
    scalar_types = sorted(SCALAR_TYPE_MAP)
    bg_presets = _background_presets(_make_namespace(builder)["background"])

    def literal(values):
        return "Literal[" + ", ".join(repr(v) for v in values) + "]"

    aliases = {
        "BackgroundPreset": literal(bg_presets),
        "ColormapName": literal(colormaps),
        "Representation": literal(REPRESENTATIONS),
        "ScalarTypeName": literal(scalar_types),
    }

    typedicts = {}  # class name -> rendered block
    for name in sorted(WHITELISTED_CLASSES):
        props = _class_props(name, instances[name])
        typename = _props_typename(name)
        body = [f"class {typename}(TypedDict, total=False):"]
        if props:
            body += [f"    {key}: {props[key]}" for key in sorted(props)]
        else:  # pragma: no cover - every whitelisted class exposes properties
            body.append("    pass")
        typedicts[name] = "\n".join(body)

    all_names = ["NodeRef", *sorted(aliases), *(_props_typename(n) for n in sorted(WHITELISTED_CLASSES))]

    parts = [
        _header("the SIVA DSL surface (VTK class introspection + siva.colormaps)"),
        PROPS_DOCSTRING,
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Sequence",
        "from typing import Any, Literal, TypedDict",
        "",
        "__all__ = [",
        *(f"    {name!r}," for name in all_names),
        "]",
        "",
        "",
        NODEREF_CLASS,
        "",
        "",
        "# Closed-enum aliases (colormaps, scalar types, background presets, representations).",
    ]
    for alias in sorted(aliases):
        parts.append(f"{alias} = {aliases[alias]}")
    for name in sorted(WHITELISTED_CLASSES):
        parts.append("")
        parts.append("")
        parts.append(typedicts[name])
    return "\n".join(parts) + "\n"


def render_api_module():
    """Return the full generated source of ``siva/spec_api.py``."""
    builder = PipelineBuilder()
    namespace = _make_namespace(builder)
    methods = _builder_callables(namespace)

    instances = {name: cls() for name, cls in WHITELISTED_CLASSES.items()}
    source_classes = sorted(n for n, inst in instances.items() if _is_source(inst))
    filter_classes = sorted(n for n, inst in instances.items() if not _is_source(inst))

    verb_names = sorted(methods)
    all_names = ["NodeRef", "math"] + verb_names

    parts = [
        _header("the PipelineBuilder DSL surface (siva.dsl / siva.sandbox)"),
        API_DOCSTRING,
        "",
        # Defers annotation evaluation to strings (PEP 563), so unions like
        # `NodeRef | None` type-check under pyright without requiring a newer
        # runtime -- this module is never executed (see the module docstring),
        # but it must stay *importable* Python for the sync test.
        "from __future__ import annotations",
        "",
        "import math",
        "",
        "from typing import Any, Unpack, overload",
        "",
        "from siva._spec_api_props import *  # noqa: F401,F403  (NodeRef, enums, *Props)",
        "",
        "__all__ = [",
        *(f"    {name!r}," for name in all_names),
        "]",
    ]
    for verb in verb_names:
        parts.append("")
        parts.append("")
        parts.append(_render_verb(verb, methods[verb], source_classes, filter_classes))
    return "\n".join(parts) + "\n"


def outputs():
    """Return {path: rendered source} for every generated module."""
    return {
        PROPS_PATH: render_props_module(),
        API_PATH: render_api_module(),
    }


def main():
    for path, content in outputs().items():
        path.write_text(content, encoding="utf-8")
        print(f"  wrote {path.relative_to(PROJECT_ROOT)}  ({len(content):,} chars)")


if __name__ == "__main__":
    main()
