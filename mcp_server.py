#!/usr/bin/env python3
import io
import os
import traceback
from contextlib import redirect_stdout, redirect_stderr

from mcp.server.fastmcp import FastMCP
from my_inspect import inspect_file
from my_load import load
from my_download import download
from my_compress import compress
from datasetInfo import DatasetInfo
from my_render import render

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


def _dsl_context():
    return {
        "inspect": inspect_file,
        "load": load,
        "download": download,
        "compress": compress,
        "render": render,
    }


def _summarize_datasets(context):
    """Find DatasetInfo objects left in the exec namespace and summarize them."""
    lines = []
    for name, val in context.items():
        if isinstance(val, DatasetInfo):
            lines.append(f"\n--- {name} ---\n{val}")
    return "\n".join(lines)


@mcp.tool()
def run_pipeline(spec_path: str) -> str:
    """Execute the data pipeline spec at spec_path and return an execution report.

    DSL forms available in the spec (no imports needed):
      inspect(filepath)                                   -> DatasetInfo (metadata only)
      load(dataset_info, variables=None, dimensions=None) -> DatasetInfo (with arrays)
      download(remote_source, local_path)                 -> local_path (str)
      compress(dataset_info, variables, error_bound)      -> DatasetInfo (compressed)
      render(dataset_info, positions=None, subsample_factor=30, grid_size=128)
        pushes geometry to the live render server; returns its URL in the output.
        positions=('x','y','z') required only if particle coords can't be auto-detected.

    dimensions examples:
      {'particles': 0.1}  # random 10% of particles
      {'grid': 64}        # stride to ~64 cells per axis

    Returns captured output, a summary of every DatasetInfo in scope, and any error.
    """
    try:
        with open(spec_path) as f:
            spec_code = f.read()
    except FileNotFoundError:
        return f"ERROR: spec file not found: {spec_path}"
    except Exception as e:
        return f"ERROR reading spec: {type(e).__name__}: {e}"

    buf = io.StringIO()
    context = _dsl_context()
    status = "OK"
    error_text = ""

    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            exec(compile(spec_code, spec_path, "exec"), context)
    except Exception:
        status = "FAILED"
        error_text = traceback.format_exc()

    output = buf.getvalue().rstrip()
    dataset_summary = _summarize_datasets(context)

    parts = [f"Status: {status}", f"Spec: {spec_path}"]
    if output:
        parts.append(f"\n--- Output ---\n{output}")
    if dataset_summary:
        parts.append(f"\n--- Datasets ---{dataset_summary}")
    if error_text:
        parts.append(f"\n--- Error ---\n{error_text.rstrip()}")

    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run()
