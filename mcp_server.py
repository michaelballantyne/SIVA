#!/usr/bin/env python3
import io
import os
import traceback
from contextlib import redirect_stdout, redirect_stderr

from mcp.server.fastmcp import FastMCP
from my_inspect import inspect_file
from my_estimate import estimate_render_cost as _estimate_render_cost, format_estimate

from dsl_forms import form_namespace, reset_sinks, collected_sinks, leaf_nodes
from planner import plan_pipeline, format_result

# --- Pipeline philosophy / guidance surfaced to the LLM ---------------------
# instructions/Instructions.md is sent verbatim as the server's startup
# instructions (always in the model's context). The rest of the instructions/
# folder is exposed as resources the model reads on demand. Edit the markdown
# to evolve the guidance; reconnect the server (/mcp) to reload Instructions.md.
_INSTRUCTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "instructions")


def _read_instruction(name):
    with open(os.path.join(_INSTRUCTIONS_DIR, f"{name}.md"), encoding="utf-8") as f:
        return f.read()


try:
    _STARTUP_INSTRUCTIONS = _read_instruction("Instructions")
except OSError:
    _STARTUP_INSTRUCTIONS = None  # folder missing — server still runs

mcp = FastMCP("VisLang Data Management", instructions=_STARTUP_INSTRUCTIONS)


@mcp.resource("vislang://instructions", name="instructions-index",
              description="List of available VisLang guidance documents.",
              mime_type="text/markdown")
def _instructions_index():
    try:
        docs = sorted(f[:-3] for f in os.listdir(_INSTRUCTIONS_DIR)
                      if f.endswith(".md"))
    except OSError:
        return "No instructions/ folder found."
    lines = ["# VisLang instruction documents", "",
             "Read any with `vislang://instructions/<name>`:", ""]
    lines += [f"- `vislang://instructions/{d}`" for d in docs]
    return "\n".join(lines)


@mcp.resource("vislang://instructions/{doc}", name="instruction-doc",
              description="A VisLang guidance document (philosophy, DSL, "
                          "adapters, rendering, authoring, roadmap).",
              mime_type="text/markdown")
def _instruction_doc(doc):
    try:
        return _read_instruction(doc)
    except OSError:
        raise ValueError(f"No instruction document named {doc!r}. "
                         f"See vislang://instructions for the list.")


@mcp.tool()
def inspect(filepath: str, positions: str = None) -> str:
    """Read a file's schema — variables, dimensions, attributes — metadata only, no bulk data.

    Use this to write a spec: you need to know what fields and axes exist before
    you can `region`, `subsample`, `fields`, or `filter` them. `inspect` is the
    engine behind the `source()` form; calling it here is the same read, just so
    you can see the schema while authoring.

    positions: optional "x,y,z" override naming the spatial-coordinate variables
    when auto-detection can't tell (e.g. particle data with unusual names).
    """
    try:
        pos = tuple(p.strip() for p in positions.split(",")) if positions else None
        return str(inspect_file(filepath, positions=pos))
    except Exception as e:
        return f"ERROR inspecting {filepath}: {type(e).__name__}: {e}"


@mcp.tool()
def estimate_render_cost(filepath: str) -> str:
    """Predict the cost of rendering a dataset and recommend a subsample — BEFORE loading.

    Reads only metadata (no bulk data). Use this on an unfamiliar or large file
    before rendering: render is headless k3d and ships the array to the browser,
    so a full "show everything" view can be huge. The report gives the estimated
    browser payload, the disk-read cost (and whether narrowing reduces it), and a
    ready-to-use recommendation to keep the first overview responsive.
    """
    try:
        return format_estimate(_estimate_render_cost(filepath))
    except Exception as e:
        return f"ERROR estimating {filepath}: {type(e).__name__}: {e}"


def _run_one(node, dry_run):
    """Plan+execute one pipeline; return (ok, formatted_text)."""
    try:
        return True, format_result(plan_pipeline(node, dry_run=dry_run))
    except Exception as e:
        return False, f"[{getattr(node, 'kind', '?')}] FAILED: {type(e).__name__}: {e}"


@mcp.tool()
def run_pipeline(spec_path: str) -> str:
    """Execute a declarative DSL spec at spec_path and return an execution report.

    Convention: keep the spec in a single file named `spec.py`, edited in place —
    pass spec_path="spec.py". Do not create a new/uniquely-named file per request.

    A spec is built from FORMS (no imports needed). Each form is a declarative
    GOAL that builds a node; nothing reads data until a sink runs:

      source(uri, positions=None)        the dataset (inspect under the hood)
      fields(node, keep)                 keep only these variables
      region(node, x=(a,b), ...)         crop to an index range per axis
      subsample(node, f) | (node, x=..)  reduce resolution (stride / fraction)
      timestep(node, index)              pick a timestep
      filter(node, "var > value")        keep where the predicate holds
      compress(node, variables, error_bound[, mode])
      save(node, path)        [sink]     write the result to disk
      render(node, cmap=None, opacity=None)   [sink] serve the browser viewer

    The forms build an AST; an interpreter inspects the source, static-checks the
    request against the schema (before any bulk read), fuses the narrowing, and
    lowers it to physical reads. `render`/`save` are the sinks that trigger
    execution — a spec with no sink is dry-run (its inferred plan is reported,
    nothing is materialized). Chain forms left-to-right:

        render(subsample(source("data.h5"), 2), cmap="green")

    Returns the inferred plan per pipeline, any printed URLs/paths, and errors.
    (Phase 1: source/fields/subsample/compress/save/render are wired;
    region/timestep/filter parse and static-check but are not yet materialized.)
    """
    try:
        with open(spec_path) as f:
            spec_code = f.read()
    except FileNotFoundError:
        return f"ERROR: spec file not found: {spec_path}"
    except Exception as e:
        return f"ERROR reading spec: {type(e).__name__}: {e}"

    # Build the AST. Forms only construct nodes — no I/O here.
    reset_sinks()
    ctx = form_namespace()
    try:
        exec(compile(spec_code, spec_path, "exec"), ctx)
    except Exception:
        return (f"Status: BUILD FAILED\nSpec: {spec_path}\n\n"
                f"--- Error ---\n{traceback.format_exc().rstrip()}")

    sinks = collected_sinks()
    dry = not sinks
    targets = leaf_nodes(ctx) if dry else sinks

    # Execute (or dry-run). This is where reads happen, so capture stdout here.
    buf = io.StringIO()
    results, any_failed = [], False
    with redirect_stdout(buf), redirect_stderr(buf):
        for t in targets:
            ok, text = _run_one(t, dry_run=dry)
            any_failed = any_failed or not ok
            results.append(text)
    output = buf.getvalue().rstrip()

    parts = [f"Status: {'FAILED' if any_failed else 'OK'}", f"Spec: {spec_path}"]
    if dry:
        parts.append("\n(no render()/save() sink — dry run: inferred plan only, "
                     "nothing materialized)")
    if results:
        parts.append("\n--- Pipelines ---\n" + "\n\n".join(results))
    if output:
        parts.append(f"\n--- Output ---\n{output}")
    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run()
