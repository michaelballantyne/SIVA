#!/usr/bin/env python3
import io
import traceback
from contextlib import redirect_stdout, redirect_stderr

from mcp.server.fastmcp import FastMCP
from my_inspect import inspect_file
from my_load import load
from my_download import download
from my_compress import compress
from datasetInfo import DatasetInfo

mcp = FastMCP("VisLang Data Management")


def render(dataset_info, **kwargs):
    """Stub: register a dataset for visualization. Replaced by SIVA's show() on merge."""
    vars_loaded = list(dataset_info.data.keys()) if dataset_info.loaded else []
    print(f"[render] {dataset_info.filepath} "
          f"({'loaded: ' + ', '.join(vars_loaded) if vars_loaded else 'not loaded'})")
    return dataset_info


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
      render(dataset_info, **kwargs)                      -> DatasetInfo (vis stub)

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
