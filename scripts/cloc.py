#!/usr/bin/env python3
"""Count Python code, test, and docstring lines in the project.

Excludes meta/, docs/, experiments/ by default. Splits remaining .py
files into "tests" (path matches tests?/, test_*.py, *_test.py) and
"code". For each group reports:

  - files
  - raw lines (wc -l equivalent)
  - SLOC (excluding blanks, comments, and docstrings)
  - docstring lines (module/class/function docstrings)
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

EXCLUDE_DIRS = ("meta/", "docs/", "experiments/")
TEST_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$")


def tracked_py_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    files = []
    for p in out:
        if not p.endswith(".py"):
            continue
        if any(p.startswith(d) for d in EXCLUDE_DIRS):
            continue
        files.append(Path(p))
    return files


def sloc_count(src: str) -> int:
    """Lines containing at least one non-string, non-comment token."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenizeError:
        return 0
    skip = {
        tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
        tokenize.ENCODING, tokenize.ENDMARKER,
        tokenize.INDENT, tokenize.DEDENT, tokenize.STRING,
    }
    lines = set()
    for t in toks:
        if t.type in skip:
            continue
        lines.add(t.start[0])
    return len(lines)


def docstring_lines(src: str) -> int:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0
    total = 0
    node_types = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, node_types):
            ds = ast.get_docstring(node, clean=False)
            if ds:
                total += ds.count("\n") + 1
    return total


def measure(files: list[Path]) -> dict:
    raw = sloc = docs = 0
    for f in files:
        try:
            src = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        raw += src.count("\n") + (0 if src.endswith("\n") or not src else 1)
        sloc += sloc_count(src)
        docs += docstring_lines(src)
    return {"files": len(files), "raw": raw, "sloc": sloc, "docs": docs}


def fmt_row(label: str, m: dict) -> str:
    return f"  {label:<12} {m['files']:>4}  {m['raw']:>7}  {m['sloc']:>7}  {m['docs']:>7}"


def main() -> int:
    files = tracked_py_files()
    tests = [f for f in files if TEST_RE.search(str(f))]
    code = [f for f in files if f not in tests]

    code_m = measure(code)
    tests_m = measure(tests)
    total_m = {k: code_m[k] + tests_m[k] for k in code_m}

    print(f"Python files outside {', '.join(EXCLUDE_DIRS)}")
    print()
    print(f"  {'group':<12} {'files':>4}  {'raw':>7}  {'sloc':>7}  {'docs':>7}")
    print(f"  {'-'*12} {'-'*4}  {'-'*7}  {'-'*7}  {'-'*7}")
    print(fmt_row("code", code_m))
    print(fmt_row("tests", tests_m))
    print(fmt_row("total", total_m))
    print()
    print("  raw  = wc -l")
    print("  sloc = lines with non-string, non-comment tokens (no blanks/docstrings)")
    print("  docs = lines inside module/class/function docstrings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
