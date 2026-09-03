"""Construct-test every ```python fence in domains/*.md so domain guides cannot
silently rot.

Domain guides show DSL snippets as prose illustration, not runnable files
(unlike ``demos/**/view-*.py``, which are whole specs -- see
``tests/test_demos.py``). Nothing else exercises them, so a snippet can drift
to a removed/renamed DSL form -- exactly the failure mode that broke three
wildfire forms silently before this test existed. This test discovers every
fenced ```python block in every top-level ``domains/*.md`` file and asserts it
``construct()``s against the current DSL: parses and freezes a pipeline graph
without building VTK or reading data files (see ``siva.dsl.construct`` and
``tests/test_demos.py``'s docstring for what "construct only" means here), so
it's fast, data-free, and needs no display/Xvfb.

Two conventions this test relies on, since domain snippets are fragments, not
full specs:

- **Implicit header.** A fence need not repeat the mandatory
  ``from siva.spec_api import *`` header (Monty requires it as the first
  top-level statement -- see ``siva/sandbox.py``); this test prepends it if
  the fence doesn't already start with it.
- **Implicit ``data``.** A fence that references ``data`` without binding it
  is assumed to continue from a preceding "load the dataset" example (as
  ``domains/wildfire.md`` documents explicitly). This test prepends a
  placeholder ``data = source(...)`` binding when a fence uses ``data`` as a
  free name (no top-level ``data = ...`` assignment of its own).
- **Opt-out marker.** A fence that is intentionally fragmentary and can't
  construct on its own (e.g. it shows a bare expression, not a statement, or
  depends on a binding other than ``data``) can opt out by adding
  ``skip-test`` to the fence's info string, i.e. ```` ```python skip-test ````
  instead of just ```` ```python ````. Document any such fence inline so
  readers know why it's excluded.
"""

import re
from pathlib import Path

import pytest

from siva.dsl import construct

REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAINS_DIR = REPO_ROOT / "domains"

# Matches ```python[<optional info string>]\n<code>```
FENCE_RE = re.compile(r"```python([^\n]*)\n(.*?)```", re.DOTALL)

HEADER = "from siva.spec_api import *"
PLACEHOLDER_DATA = (
    'data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")'
)


def _iter_fences(md_path):
    """Yield (index, info_string, code) for every ```python fence in *md_path*."""
    text = md_path.read_text()
    for i, match in enumerate(FENCE_RE.finditer(text)):
        info, code = match.groups()
        yield i, info.strip(), code


def _discover_domain_fences():
    cases = []
    for md_path in sorted(DOMAINS_DIR.glob("*.md")):
        for i, info, code in _iter_fences(md_path):
            if "skip-test" in info:
                continue
            case_id = f"{md_path.relative_to(REPO_ROOT)}::fence{i}"
            cases.append(pytest.param(code, id=case_id))
    return cases


DOMAIN_FENCES = _discover_domain_fences()


def _prepare(code):
    """Prepend the mandatory header and a placeholder ``data`` binding as
    needed so a prose fragment can construct on its own (see module
    docstring for the conventions this implements)."""
    prelude = []
    if not code.lstrip().startswith(HEADER):
        prelude.append(HEADER)
    uses_data = re.search(r"\bdata\b", code) is not None
    binds_data = re.search(r"(?m)^\s*data\s*=", code) is not None
    if uses_data and not binds_data:
        prelude.append(PLACEHOLDER_DATA)
    if not prelude:
        return code
    return "\n".join(prelude) + "\n" + code


def test_domain_fences_discovered():
    """Guard against the glob/regex silently matching nothing."""
    assert DOMAIN_FENCES, f"No python fences found under {DOMAINS_DIR}"


@pytest.mark.parametrize("code", DOMAIN_FENCES)
def test_domain_fence_constructs(code):
    """Each domain-guide fence must construct without error against current
    DSL forms (see module docstring for the header/``data`` conventions)."""
    construct(_prepare(code))
