"""Tests for the Monty spec-execution sandbox (siva.sandbox).

Covers:

1. **Frozen-spec shape**: representative specs construct to the expected frozen
   :class:`~siva.spec.Spec` (values pinned from the historical exec baseline).
2. **Marshalling**: ``NodeRef`` handles cross the boundary as opaque
   ``NodeHandle`` ids and resolve back to canonical host instances, including
   deep filter chains (the O(1)-per-crossing regression).
3. **Sandboxing**: escape attempts (imports, ``open``, dunder traversal,
   ``getattr``, ``exec``/``eval``) raise without host side effects.
4. **Resource limits**: a busy loop trips Monty's wall-clock limit.
5. **Preprocessing / error quality**: the stub-import header is stripped
   without perturbing line numbers, and runtime errors name the spec line.

These need no rendering, so the file runs under a plain
``.venv/bin/python -m pytest`` (no xvfb).
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pydantic_monty as pm

from siva.dsl import construct
from siva import sandbox


# A representative spec: a source, a filter chain with kwargs, a for loop that
# builds several nodes, a list-valued param, an f-string, and scene calls.
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


class TestFrozenSpecShape(unittest.TestCase):
    """A representative spec freezes to the expected Spec.

    Expected values are pinned from the historical exec baseline so behavior
    stays fixed under Monty.
    """

    def test_representative_spec_shape(self):
        spec = construct(REPRESENTATIVE_SPEC)
        # source + threshold + 3 contours = 5 nodes; 4 shows.
        self.assertEqual(len(spec.nodes), 5)
        self.assertEqual(len(spec.shows), 4)
        self.assertEqual(spec.scene.background, (0.1, 0.2, 0.3))
        self.assertIsNotNone(spec.scene.camera)
        self.assertEqual(spec.scene.camera.position, (1.0, 2.0, 3.0))
        self.assertEqual(spec.scene.camera.zoom, 1.5)
        # NodeRef-valued bindings only (src, t, and the final c).
        self.assertEqual(set(spec.bindings), {"src", "t", "c"})

    def test_show_names_and_fstring(self):
        spec = construct(REPRESENTATIVE_SPEC)
        names = [s.name for s in spec.shows]
        self.assertEqual(names, ["thresholded", "iso 0", "iso 1", "iso 2"])

    def test_threshold_params_pinned(self):
        spec = construct(REPRESENTATIVE_SPEC)
        # nodes[1] is the threshold; its kwargs survive as params.
        params = spec.nodes[1].params
        self.assertEqual(params["LowerThreshold"], 50.0)
        self.assertEqual(params["UpperThreshold"], 200.0)


class TestMath(unittest.TestCase):
    """math members are usable and produce the expected params."""

    SPEC = """
src = source('vtkSphereSource')
r = threshold(src, Radius=math.sqrt(16.0), Angle=math.pi)
show(r)
"""

    def test_math_values(self):
        spec = construct(self.SPEC)
        params = spec.nodes[1].params
        self.assertAlmostEqual(params["Radius"], 4.0)
        self.assertAlmostEqual(params["Angle"], 3.141592653589793)

    def test_expression_fstring_ok(self):
        # Monty supports full Python f-strings (expression substitution).
        spec = construct(
            "src = source('vtkSphereSource')\nshow(src, name=f'val {1 + 2}')"
        )
        self.assertEqual(spec.shows[0].name, "val 3")


class TestMarshalling(unittest.TestCase):
    """NodeRef crosses the boundary as an opaque NodeHandle, resolved back to
    canonical host instances by node_id."""

    def test_noderefs_in_args_kwargs_and_lists(self):
        # SeedSource kwarg + a filter chain exercise NodeRef passing across the
        # boundary. The whole spec constructs and the SeedSource edge survives.
        spec = construct(
            "src = source('vtkRTAnalyticSource')\n"
            "seeds = source('vtkLineSource')\n"
            "vel = make_vector(input=src)\n"
            "streams = stream_tracer(input=vel, SeedSource=seeds)\n"
            "show(streams, name='flow')\n"
        )
        self.assertEqual(set(spec.bindings), {"src", "seeds", "vel", "streams"})
        tracer = spec.nodes[-1]
        self.assertIn("SeedSource", tracer.params)

    def test_bindings_recovered(self):
        spec = construct(
            "src = source('vtkSphereSource')\n"
            "t = threshold(src, Radius=1.0)\n"
        )
        self.assertEqual(set(spec.bindings), {"src", "t"})

    def test_conditional_binding_recovered(self):
        # Name bound only inside an if-body must still be recovered.
        spec = construct(
            "src = source('vtkSphereSource')\n"
            "if True:\n"
            "    t = threshold(src, Radius=1.0)\n"
            "show(t)\n"
        )
        self.assertIn("t", spec.bindings)

    def test_unbound_conditional_binding_skipped(self):
        # A name whose branch is not taken is simply absent -- no error.
        spec = construct(
            "src = source('vtkSphereSource')\n"
            "if False:\n"
            "    never = threshold(src, Radius=1.0)\n"
            "show(src)\n"
        )
        self.assertIn("src", spec.bindings)
        self.assertNotIn("never", spec.bindings)

    def test_unknown_handle_id_rejected(self):
        # The host trusts only the node_id and only to look up the canonical
        # NodeRef. A handle bearing an id never produced by a builder call --
        # forged or corrupted -- has no table entry and must be rejected.
        # (In practice a NodeHandle is frozen and unconstructable in-sandbox;
        # this pins the host-side guard directly.)
        with self.assertRaises(sandbox.SandboxError) as ctx:
            sandbox._from_handles(sandbox.NodeHandle(9999), {})
        self.assertIn("9999", str(ctx.exception))

    def test_unknown_handle_id_rejected_in_container(self):
        # The recursive container walk resolves handles nested in
        # lists/tuples/dicts, so an unknown id anywhere inside is caught.
        with self.assertRaises(sandbox.SandboxError):
            sandbox._from_handles([{"k": sandbox.NodeHandle(9999)}], {})

    def test_field_mutation_is_harmless(self):
        # NodeHandle is frozen, so a spec that tries to doctor its id is
        # stopped inside the sandbox and never reaches the host.
        with self.assertRaises(sandbox.SandboxError):
            construct(
                "src = source('vtkSphereSource')\n"
                "src.node_id = 42\n"
                "t = threshold(src, Radius=1.0)\n"
                "show(t)\n"
            )
        # Host state is intact: a fresh construct builds canonical ids.
        clean = (
            "src = source('vtkSphereSource')\n"
            "t = threshold(src, Radius=1.0)\n"
            "show(t)\n"
        )
        spec = construct(clean)
        self.assertEqual(set(spec.bindings), {"src", "t"})
        self.assertEqual(len(spec.nodes), 2)


class TestDeepChain(unittest.TestCase):
    """A deep filter chain constructs in O(1)-per-crossing time.

    The old full-NodeRef marshalling was quadratic in chain length and hit
    Monty's input-depth cap around 100 nodes. Marshalling a slim NodeHandle
    removes both problems.
    """

    def _chain_spec(self, n):
        # A source followed by n contours, each fed the previous node.
        lines = ["x = source('vtkRTAnalyticSource')"]
        for _ in range(n):
            lines.append("x = contour(x, Isosurfaces=[1.0])")
        lines.append("show(x)")
        return "\n".join(lines) + "\n"

    def test_deep_chain_constructs(self):
        n = 300
        start = time.time()
        spec = construct(self._chain_spec(n))
        elapsed = time.time() - start
        # source + n contours.
        self.assertEqual(len(spec.nodes), n + 1)
        # Generous wall-clock bound: this must be well under a second even on
        # a slow CI box. The old code failed outright past ~100 nodes.
        self.assertLess(
            elapsed, 1.0,
            f"n={n} chain took {elapsed:.3f}s -- marshalling may be superlinear",
        )

    def test_chain_scaling_not_wildly_superlinear(self):
        # A soft sanity check: n=300 should not be dramatically slower than
        # n=50 (it was ~quadratic before). Not a tight timing assertion -- just
        # a generous ceiling that a linear/near-linear implementation clears.
        def timed(n):
            start = time.time()
            construct(self._chain_spec(n))
            return time.time() - start

        t50 = timed(50)
        t300 = timed(300)
        # If it were truly quadratic, t300 would be ~36x t50. Allow a very
        # generous factor to avoid flakiness while still catching a regression.
        self.assertLess(t300, max(t50 * 12, 0.5))


class TestEscapes(unittest.TestCase):
    """Escape attempts raise inside Monty without host side effects."""

    def _assert_blocked(self, code):
        with self.assertRaises(Exception) as ctx:
            construct(code)
        self.assertNotIsInstance(ctx.exception, AssertionError)

    def test_import_blocked(self):
        self._assert_blocked("import os\nos.getcwd()")

    def test_open_blocked_no_side_effect(self):
        sentinel = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_sandbox_pwned.tmp",
        )
        if os.path.exists(sentinel):
            os.remove(sentinel)
        self._assert_blocked(f"open({sentinel!r}, 'w')")
        self.assertFalse(
            os.path.exists(sentinel),
            "sandbox let spec create a file on the host",
        )

    def test_dunder_traversal_blocked(self):
        self._assert_blocked("x = ().__class__.__mro__")
        self._assert_blocked("y = [].__class__.__base__.__subclasses__()")

    def test_getattr_blocked(self):
        self._assert_blocked("z = getattr(source, '__globals__')")

    def test_exec_eval_blocked(self):
        self._assert_blocked("exec('1 + 1')")
        self._assert_blocked("eval('1 + 1')")


class TestNodeHandleDunderBlocked(unittest.TestCase):
    """The registered NodeHandle dataclass exposes no dunders and cannot be
    constructed inside the Monty sandbox."""

    def _blocked(self, expr):
        with self.assertRaises(Exception):
            construct(f"a = source('vtkSphereSource')\nx = {expr}\nshow(a)\n")

    def test_class_dunder_blocked(self):
        self._blocked("a.__class__")

    def test_init_dunder_blocked(self):
        self._blocked("a.__init__")

    def test_dict_dunder_blocked(self):
        self._blocked("a.__dict__")

    def test_mro_traversal_blocked(self):
        self._blocked("a.__class__.__mro__")

    def test_getattr_class_blocked(self):
        self._blocked("getattr(a, '__class__')")

    def test_cannot_construct_handle(self):
        # NodeHandle is registered (so its name resolves), but a registered
        # dataclass cannot be constructed inside the sandbox.
        with self.assertRaises(Exception):
            construct("n = NodeHandle(7)\n")


class TestResourceLimits(unittest.TestCase):
    """A busy loop must trip Monty's wall-clock limit."""

    def test_busy_loop_times_out(self):
        original = sandbox._monty_limits
        sandbox._monty_limits = lambda: pm.ResourceLimits(
            max_duration_secs=0.3,
            max_memory=256 * 1024 * 1024,
            max_allocations=50_000_000,
            max_recursion_depth=200,
        )
        try:
            with self.assertRaises(sandbox.SandboxError) as ctx:
                construct("x = 0\nwhile True:\n    x = x + 1\n")
            self.assertIn("time", str(ctx.exception).lower())
        finally:
            sandbox._monty_limits = original


class TestErrorQuality(unittest.TestCase):
    """Errors surface as readable diagnostics naming the spec line."""

    def test_syntax_error(self):
        with self.assertRaises(SyntaxError):
            construct("a = = 3")

    def test_runtime_error_has_line(self):
        with self.assertRaises(sandbox.SandboxError) as ctx:
            construct(
                "src = source('vtkSphereSource')\nbad = undefined_name\n"
            )
        msg = str(ctx.exception)
        self.assertIn("undefined_name", msg)
        self.assertIn("line 2", msg)


class TestSpecImportHeaderStripped(unittest.TestCase):
    """The canonical stub-import header is stripped and does not perturb error
    line numbers."""

    HEADER_VARIANTS = (
        "from siva.spec_api import *\n",
        "import siva.spec_api\n",
        "from siva.spec_api import source, threshold, show\n",
    )

    def test_header_runs_identically(self):
        body = (
            "src = source('vtkSphereSource')\n"
            "t = threshold(src, Radius=2.0)\n"
            "show(t, name='thresholded')\n"
        )
        reference = construct(body)
        for header in self.HEADER_VARIANTS:
            with self.subTest(header=header.strip()):
                self.assertEqual(construct(header + body), reference)

    def test_header_preserves_error_line_numbers(self):
        # undefined_name is on line 3 whether or not the header is present,
        # because the header line is blanked in place (not removed).
        header = "from siva.spec_api import *\n"
        body = (
            "src = source('vtkSphereSource')\n"
            "bad = undefined_name\n"
        )
        with self.assertRaises(sandbox.SandboxError) as ctx:
            construct(header + body)
        msg = str(ctx.exception)
        self.assertIn("undefined_name", msg)
        self.assertRegex(msg, r"line 3\b")

    def test_strip_helper_blanks_only_header_lines(self):
        code = (
            "import siva.spec_api\n"
            "x = 1\n"
            "from siva.spec_api import source\n"
            "y = 2\n"
        )
        stripped = sandbox._strip_spec_import_header(code)
        lines = stripped.split("\n")
        self.assertEqual(lines[0], "")
        self.assertEqual(lines[1], "x = 1")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "y = 2")

    def test_strip_helper_leaves_unrelated_imports(self):
        # A non-spec import is not our concern to strip (the sandbox blocks it
        # at execution). The stripper must leave it alone.
        code = "import os\nx = 1\n"
        self.assertEqual(sandbox._strip_spec_import_header(code), code)


if __name__ == "__main__":
    unittest.main()
