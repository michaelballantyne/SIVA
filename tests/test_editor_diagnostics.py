"""Editor-behavior tests for the spec_api stub, driven by real pyright.

These prove the *point* of `siva/spec_api.py`: that an editor's language
server can actually resolve DSL names in a spec file that begins with
`from siva.spec_api import *`, without either (a) flagging every DSL call as
undefined, or (b) silently swallowing genuine typos.

We shell out to `npx -y pyright@<pinned version> --outputjson` and parse its
diagnostics. No rendering here, so this runs under a plain
`.venv/bin/python -m pytest` (no xvfb) -- but it does need `node`/`npx` on
PATH and network access on first run (npx downloads pyright once, then
caches it). The whole module is skipped cleanly if `npx` isn't available.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = REPO_ROOT / "demos"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "editor_diagnostics"

# Pin the pyright version so results are reproducible across machines/CI.
PYRIGHT_VERSION = "1.1.411"

OK_FIXTURE = FIXTURES_DIR / "ok_spec.py"
BAD_FIXTURE = FIXTURES_DIR / "bad_spec.py"
# Closed-enum fixtures (Phase 2): lut / representation / background / scalar_type.
ENUMS_OK_FIXTURE = FIXTURES_DIR / "enums_ok.py"
ENUMS_BAD_FIXTURE = FIXTURES_DIR / "enums_bad.py"

pytestmark = pytest.mark.skipif(
    shutil.which("npx") is None or shutil.which("node") is None,
    reason="node/npx not available",
)


def _discover_demo_spec_files():
    """Every demos/**/*.py file whose first statement is the spec header.

    Distinguishes real spec files (`view-*.py`, `vorticity-slice.py`, ...) --
    which run through the sandbox and so must begin with the canonical
    header -- from ordinary driver scripts under demos/ that embed spec code
    as string literals rather than being spec files themselves.
    """
    spec_files = []
    for path in sorted(DEMOS_DIR.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "from siva.spec_api import *":
                spec_files.append(path)
            break
    return spec_files


DEMO_SPEC_FILES = _discover_demo_spec_files()


def _run_pyright(paths):
    """Run pyright once over *paths*; return {file: [error messages]}."""
    cmd = [
        "npx", "-y", f"pyright@{PYRIGHT_VERSION}", "--outputjson",
        *(str(p) for p in paths),
    ]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"pyright did not produce valid JSON.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    errors = {}
    for diag in data["generalDiagnostics"]:
        if diag["severity"] == "error":
            errors.setdefault(diag["file"], []).append(diag["message"])
    return errors


@pytest.fixture(scope="module")
def pyright_errors():
    """Run pyright exactly once over every fixture/demo file these tests need."""
    assert DEMO_SPEC_FILES, "no demo spec files discovered under demos/"
    all_paths = DEMO_SPEC_FILES + [
        OK_FIXTURE, BAD_FIXTURE, ENUMS_OK_FIXTURE, ENUMS_BAD_FIXTURE,
    ]
    return _run_pyright(all_paths)


def test_demo_spec_files_are_clean(pyright_errors):
    """Every real spec file under demos/ type-checks with zero errors."""
    for path in DEMO_SPEC_FILES:
        key = str(path)
        assert key not in pyright_errors, (
            f"{path.relative_to(REPO_ROOT)} has pyright errors: {pyright_errors.get(key)}"
        )


def test_multi_verb_fixture_is_clean(pyright_errors):
    """A spec using several verbs, including filter/slice, has zero errors.

    `filter` and `slice` are real DSL verbs that intentionally shadow Python
    builtins (see siva/sandbox.py's header-substitution scheme) -- this
    guards against the stub accidentally leaving the builtin resolved
    instead of the DSL form.
    """
    key = str(OK_FIXTURE)
    assert key not in pyright_errors, f"ok_spec.py has pyright errors: {pyright_errors.get(key)}"


def test_misspelled_verb_is_caught(pyright_errors):
    """A misspelled DSL verb is still flagged -- proves we're not just suppressing everything."""
    key = str(BAD_FIXTURE)
    assert key in pyright_errors, "bad_spec.py should have produced a pyright error but didn't"
    messages = " ".join(pyright_errors[key])
    assert "contuor" in messages


def test_valid_enum_arguments_are_clean(pyright_errors):
    """Valid lut / representation / background / scalar_type values type-check."""
    key = str(ENUMS_OK_FIXTURE)
    assert key not in pyright_errors, (
        f"enums_ok.py should be clean but has: {pyright_errors.get(key)}"
    )


def test_invalid_enum_arguments_are_caught(pyright_errors):
    """Bad closed-enum values (lut/representation/background/scalar_type) are flagged."""
    key = str(ENUMS_BAD_FIXTURE)
    assert key in pyright_errors, "enums_bad.py should have produced pyright errors but didn't"
    messages = " ".join(pyright_errors[key])
    for token in ("chartreuse_swirl", "Hologram", "float64_nope"):
        assert token in messages, f"expected a diagnostic mentioning {token!r}; got: {messages}"
