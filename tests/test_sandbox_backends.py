"""Tests for the sandboxed spec-execution backends (siva.sandbox).

Covers three concerns:

1. **Parity**: a representative spec constructs to an *identical* frozen
   :class:`~siva.spec.Spec` under all three backends (exec / starlark / monty).
2. **Sandboxing**: escape attempts (imports, ``open``, dunder traversal,
   ``getattr``, ``exec``/``eval``) raise in starlark and monty without host
   side effects.
3. **Resource limits**: a busy loop trips Monty's wall-clock limit.

These need no rendering, so the file runs under a plain
``.venv/bin/python -m pytest`` (no xvfb).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.dsl import construct
from siva import sandbox

try:
    import starlark  # noqa: F401
    _HAS_STARLARK = True
except ImportError:
    _HAS_STARLARK = False

try:
    import pydantic_monty  # noqa: F401
    _HAS_MONTY = True
except ImportError:
    _HAS_MONTY = False


# A representative spec: a source, a filter chain with kwargs, a for loop that
# builds several nodes, a list-valued param, identifier-only f-strings (valid
# in every backend), and scene calls. Written so it parses and runs identically
# under exec, starlark, and monty.
REPRESENTATIVE_SPEC = """
src = source('vtkRTAnalyticSource')
t = threshold(src, LowerThreshold=50.0, UpperThreshold=200.0)
show(t, name="thresholded")
levels = [10.0, 20.0, 30.0]
for i in range(3):
    level = levels[i]
    c = contour(src, Isosurfaces=[level])
    show(c, name=f"iso {i}")
background(0.1, 0.2, 0.3)
camera(position=[1.0, 2.0, 3.0], focal_point=[0.0, 0.0, 0.0], zoom=1.5)
"""


class TestBackendParity(unittest.TestCase):
    """The same spec must freeze to the same Spec across all backends."""

    def test_exec_baseline_shape(self):
        spec = construct(REPRESENTATIVE_SPEC, backend="exec")
        # source + threshold + 3 contours = 5 nodes; 4 shows.
        self.assertEqual(len(spec.nodes), 5)
        self.assertEqual(len(spec.shows), 4)
        self.assertEqual(spec.scene.background, (0.1, 0.2, 0.3))
        self.assertIsNotNone(spec.scene.camera)
        # NodeRef-valued bindings only (src, t, and the final c).
        self.assertEqual(set(spec.bindings), {"src", "t", "c"})

    def test_all_backends_identical(self):
        reference = construct(REPRESENTATIVE_SPEC, backend="exec")
        for backend in ("starlark", "monty"):
            if backend == "starlark" and not _HAS_STARLARK:
                continue
            if backend == "monty" and not _HAS_MONTY:
                continue
            with self.subTest(backend=backend):
                got = construct(REPRESENTATIVE_SPEC, backend=backend)
                self.assertEqual(got.nodes, reference.nodes)
                self.assertEqual(got.shows, reference.shows)
                self.assertEqual(got.scene, reference.scene)
                self.assertEqual(got.bindings, reference.bindings)
                self.assertEqual(got, reference)

    def test_backend_default_is_exec(self):
        # No backend arg and no env var -> exec.
        old = os.environ.pop(sandbox._ENV_VAR, None)
        try:
            self.assertEqual(sandbox.resolve_backend(), "exec")
        finally:
            if old is not None:
                os.environ[sandbox._ENV_VAR] = old

    def test_env_var_selects_backend(self):
        old = os.environ.get(sandbox._ENV_VAR)
        try:
            os.environ[sandbox._ENV_VAR] = "monty"
            self.assertEqual(sandbox.resolve_backend(), "monty")
            # Explicit arg overrides env.
            self.assertEqual(sandbox.resolve_backend("starlark"), "starlark")
        finally:
            if old is None:
                os.environ.pop(sandbox._ENV_VAR, None)
            else:
                os.environ[sandbox._ENV_VAR] = old

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            sandbox.resolve_backend("nope")


class TestMathParity(unittest.TestCase):
    """math members are usable and produce identical params across backends."""

    SPEC = """
src = source('vtkSphereSource')
r = threshold(src, Radius=math.sqrt(16.0), Angle=math.pi)
show(r)
"""

    def test_math_matches_exec(self):
        reference = construct(self.SPEC, backend="exec")
        ref_params = reference.nodes[1].params
        self.assertAlmostEqual(ref_params["Radius"], 4.0)
        for backend in ("starlark", "monty"):
            if backend == "starlark" and not _HAS_STARLARK:
                continue
            if backend == "monty" and not _HAS_MONTY:
                continue
            with self.subTest(backend=backend):
                got = construct(self.SPEC, backend=backend)
                self.assertEqual(got.nodes, reference.nodes)


class _EscapeMixin:
    """Shared escape-attempt assertions. Subclasses set ``backend``."""

    backend = None

    def _assert_blocked(self, code):
        with self.assertRaises(Exception) as ctx:
            construct(code, backend=self.backend)
        # Must not be a silent success masquerading as an error-free run.
        self.assertNotIsInstance(ctx.exception, AssertionError)

    def test_import_blocked(self):
        self._assert_blocked("import os\nos.getcwd()")

    def test_open_blocked_no_side_effect(self):
        sentinel = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"_sandbox_pwned_{self.backend}.tmp",
        )
        if os.path.exists(sentinel):
            os.remove(sentinel)
        code = f"open({sentinel!r}, 'w')"
        self._assert_blocked(code)
        self.assertFalse(
            os.path.exists(sentinel),
            "sandbox let spec create a file on the host",
        )

    def test_dunder_traversal_blocked(self):
        self._assert_blocked("x = ().__class__.__mro__")
        self._assert_blocked("y = [].__class__.__base__.__subclasses__()")

    def test_getattr_blocked(self):
        # getattr as an escape hatch to reach attributes must not be available.
        self._assert_blocked("z = getattr(source, '__globals__')")

    def test_exec_eval_blocked(self):
        self._assert_blocked("exec('1 + 1')")
        self._assert_blocked("eval('1 + 1')")


@unittest.skipUnless(_HAS_STARLARK, "starlark-pyo3 not installed")
class TestStarlarkEscapes(_EscapeMixin, unittest.TestCase):
    backend = "starlark"

    def test_expression_fstring_rejected(self):
        # Starlark f-strings only substitute bare identifiers.
        with self.assertRaises(SyntaxError):
            construct(
                "src = source('vtkSphereSource')\n"
                "show(src, name=f'{1 + 1}')",
                backend="starlark",
            )

    def test_identifier_fstring_ok(self):
        spec = construct(
            "n = 5\nsrc = source('vtkSphereSource')\nshow(src, name=f'iso {n}')",
            backend="starlark",
        )
        self.assertEqual(spec.shows[0].name, "iso 5")


@unittest.skipUnless(_HAS_MONTY, "pydantic-monty not installed")
class TestMontyEscapes(_EscapeMixin, unittest.TestCase):
    backend = "monty"

    def test_expression_fstring_ok(self):
        # Monty supports full Python f-strings.
        spec = construct(
            "src = source('vtkSphereSource')\nshow(src, name=f'val {1 + 2}')",
            backend="monty",
        )
        self.assertEqual(spec.shows[0].name, "val 3")


@unittest.skipUnless(_HAS_MONTY, "pydantic-monty not installed")
class TestMontyResourceLimits(unittest.TestCase):
    """A busy loop must trip Monty's wall-clock limit."""

    def test_busy_loop_times_out(self):
        import pydantic_monty as pm

        original = sandbox._monty_limits
        sandbox._monty_limits = lambda: pm.ResourceLimits(
            max_duration_secs=0.3,
            max_memory=256 * 1024 * 1024,
            max_allocations=50_000_000,
            max_recursion_depth=200,
        )
        try:
            with self.assertRaises(sandbox.SandboxError) as ctx:
                construct(
                    "x = 0\nwhile True:\n    x = x + 1\n",
                    backend="monty",
                )
            self.assertIn("time", str(ctx.exception).lower())
        finally:
            sandbox._monty_limits = original


class TestErrorQuality(unittest.TestCase):
    """Backend errors surface as readable diagnostics naming the line."""

    def test_exec_syntax_error(self):
        with self.assertRaises(SyntaxError):
            construct("a = = 3", backend="exec")

    @unittest.skipUnless(_HAS_STARLARK, "starlark-pyo3 not installed")
    def test_starlark_runtime_error_has_line(self):
        with self.assertRaises(sandbox.SandboxError) as ctx:
            construct(
                "src = source('vtkSphereSource')\nbad = undefined_name\n",
                backend="starlark",
            )
        msg = str(ctx.exception)
        self.assertIn("undefined_name", msg)
        self.assertIn("spec.py:2", msg)

    @unittest.skipUnless(_HAS_MONTY, "pydantic-monty not installed")
    def test_monty_runtime_error_has_line(self):
        with self.assertRaises(sandbox.SandboxError) as ctx:
            construct(
                "src = source('vtkSphereSource')\nbad = undefined_name\n",
                backend="monty",
            )
        msg = str(ctx.exception)
        self.assertIn("undefined_name", msg)
        self.assertIn("line 2", msg)

    @unittest.skipUnless(_HAS_MONTY, "pydantic-monty not installed")
    def test_monty_syntax_error(self):
        with self.assertRaises(SyntaxError):
            construct("a = = 3", backend="monty")


if __name__ == "__main__":
    unittest.main()
