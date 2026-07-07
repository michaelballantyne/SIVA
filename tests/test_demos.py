"""Construct-test every demo spec so ``demos/`` cannot silently rot.

The demos exist to reproduce the paper's examples with CURRENT SIVA syntax.
Nothing else runs them routinely, so they historically drifted to DSL forms
that no longer exist and failed at ``construct()``. This test discovers every
``demos/**/view-*.py`` and asserts it constructs into a frozen ``Spec``.

``construct()`` only parses and freezes a spec -- it does NOT build VTK or read
data files -- so this is fast, data-free, and needs no display/Xvfb even though
the specs reference multi-gigabyte dataset files that are absent.
"""

from pathlib import Path

import pytest

from siva.dsl import construct

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = REPO_ROOT / "demos"

DEMO_SPECS = sorted(DEMOS_DIR.glob("**/view-*.py"))


def test_demo_specs_discovered():
    """Guard against the glob silently matching nothing (e.g. a moved dir)."""
    assert DEMO_SPECS, f"No demo specs found under {DEMOS_DIR}"


@pytest.mark.parametrize(
    "spec_path", DEMO_SPECS, ids=[str(p.relative_to(REPO_ROOT)) for p in DEMO_SPECS]
)
def test_demo_spec_constructs(spec_path):
    """Each demo spec must construct without error against current DSL forms."""
    code = spec_path.read_text()
    construct(code)
