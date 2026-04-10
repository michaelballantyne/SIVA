"""Tests for agent-friendly error messages in the tracked-execution library.

When an AI agent writes pipeline code that runs in a restricted exec namespace,
error messages are the ONLY feedback it gets. These tests verify that every
error case produces a clear, specific, and actionable message.

Each test:
1. Triggers a specific error condition.
2. Asserts that the error message contains the expected key phrases.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure the package is importable from its source directory
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.core import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.executor import execute_pipeline, inspect_exec
from tracked_execution.proxy import TrackedProxy
from tracked_execution.vtk_escape import vtk_escape, vtk_escape_multi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mesh(seed: int = 42) -> pv.ImageData:
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(5, 5, 5))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000.0
    mesh["Pressure"] = rng.random(mesh.n_points) * 100.0
    return mesh


def make_proxy(mesh=None) -> tuple[TrackedProxy, DAG]:
    dag = DAG()
    if mesh is None:
        mesh = make_mesh()
    h = stable_hash(("root", "test_mesh"))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag), dag


# ---------------------------------------------------------------------------
# 1. Blacklist violations — dispatcher raises for explicitly forbidden methods
# ---------------------------------------------------------------------------

class TestBlacklistErrorMessages:
    """Error messages for methods in the BLACKLIST."""

    def test_save_blocked_message(self):
        """Agent gets a clear error when trying to save a mesh."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.save("output.vtk")
        msg = str(exc_info.value)
        assert "save()" in msg, f"Expected 'save()' in: {msg}"
        assert "blocked" in msg, f"Expected 'blocked' in: {msg}"
        assert "filesystem write" in msg, f"Expected 'filesystem write' in: {msg}"

    def test_export_blocked_message(self):
        """Agent gets a clear error when trying to export a mesh."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.export("output.obj")
        msg = str(exc_info.value)
        assert "export()" in msg
        assert "blocked" in msg
        assert "filesystem write" in msg

    def test_write_blocked_message(self):
        """Agent gets a clear error when trying to write a mesh."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.write("output.vtu")
        msg = str(exc_info.value)
        assert "write()" in msg
        assert "blocked" in msg
        assert "filesystem write" in msg

    def test_mesh_setitem_blocked_with_actionable_message(self):
        """Agent gets a clear error when trying to assign a new field to a mesh."""
        proxy, _ = make_proxy()
        arr = np.zeros(proxy._real.n_points)
        with pytest.raises(AttributeError) as exc_info:
            proxy["Temperature"] = arr
        msg = str(exc_info.value)
        assert "blocked" in msg, f"Expected 'blocked' in: {msg}"
        assert "mutation" in msg or "immutable" in msg, f"Expected mutation language in: {msg}"
        assert "vtk_escape" in msg, f"Expected 'vtk_escape' suggestion in: {msg}"

    def test_numpy_array_setitem_blocked(self):
        """Agent gets a clear error when trying to mutate a numpy array element."""
        proxy, _ = make_proxy()
        arr_proxy = proxy["Temperature"]
        with pytest.raises(AttributeError) as exc_info:
            arr_proxy[0] = 5.0
        msg = str(exc_info.value)
        assert "blocked" in msg
        assert "mutation" in msg or "immutable" in msg
        assert "vtk_escape" in msg

    def test_numpy_iadd_blocked(self):
        """Agent gets a clear error when trying in-place addition on a numpy array."""
        proxy, _ = make_proxy()
        arr_proxy = proxy["Temperature"]
        with pytest.raises(AttributeError) as exc_info:
            arr_proxy.__iadd__(1.0)
        msg = str(exc_info.value)
        assert "blocked" in msg
        assert "mutation" in msg or "immutable" in msg

    def test_numpy_tofile_blocked(self):
        """Agent gets a clear error when trying to write array to file."""
        proxy, _ = make_proxy()
        arr_proxy = proxy["Temperature"]
        with pytest.raises(AttributeError) as exc_info:
            arr_proxy.tofile("/tmp/output.bin")
        msg = str(exc_info.value)
        assert "blocked" in msg
        assert "filesystem write" in msg

    def test_blacklist_names_type_in_message(self):
        """Error message includes the type name so the agent knows what object it called."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.save("output.vtk")
        msg = str(exc_info.value)
        # Should name the concrete type
        assert "ImageData" in msg, f"Expected type name in: {msg}"

    def test_blacklist_names_method_in_message(self):
        """Error message includes the method name called."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.save("output.vtk")
        msg = str(exc_info.value)
        assert "save" in msg


# ---------------------------------------------------------------------------
# 2. Not-whitelisted method violations
# ---------------------------------------------------------------------------

class TestNotWhitelistedErrorMessages:
    """Error messages for methods not in the WHITELIST (but also not blacklisted)."""

    def test_obscure_method_not_whitelisted(self):
        """Agent gets a clear error with vtk_escape workaround suggestion."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.some_obscure_method()
        msg = str(exc_info.value)
        assert "not in the whitelist" in msg, f"Expected 'not in the whitelist' in: {msg}"
        assert "vtk_escape" in msg, f"Expected 'vtk_escape' suggestion in: {msg}"

    def test_not_whitelisted_names_type(self):
        """Not-whitelisted error names the object type."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.some_obscure_method()
        msg = str(exc_info.value)
        assert "ImageData" in msg

    def test_not_whitelisted_names_method(self):
        """Not-whitelisted error names the method called."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.some_obscure_method()
        msg = str(exc_info.value)
        assert "some_obscure_method" in msg

    def test_not_whitelisted_includes_example_syntax(self):
        """Not-whitelisted error shows correct vtk_escape lambda syntax."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.some_obscure_method()
        msg = str(exc_info.value)
        # Should show how to call the blocked method via vtk_escape
        assert "lambda m" in msg or "vtk_escape(proxy" in msg, (
            f"Expected vtk_escape usage example in: {msg}"
        )


# ---------------------------------------------------------------------------
# 3. Proxy attribute mutation (setattr)
# ---------------------------------------------------------------------------

class TestProxySetAttrErrorMessages:
    """Error messages when trying to set attributes on a TrackedProxy."""

    def test_setattr_is_blocked_with_explanation(self):
        """Agent gets a clear error when trying to set an attribute."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.points = None
        msg = str(exc_info.value)
        assert "immutable" in msg, f"Expected 'immutable' in: {msg}"
        assert "vtk_escape" in msg, f"Expected 'vtk_escape' suggestion in: {msg}"

    def test_setattr_names_the_attribute(self):
        """Setattr error names the attribute that was set."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.some_field = 42
        msg = str(exc_info.value)
        assert "some_field" in msg

    def test_setattr_names_the_type(self):
        """Setattr error names the underlying object type."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError) as exc_info:
            proxy.n_points = 99
        msg = str(exc_info.value)
        assert "ImageData" in msg


# ---------------------------------------------------------------------------
# 4. Restricted namespace violations (execute_pipeline)
# ---------------------------------------------------------------------------

class TestRestrictedNamespaceErrorMessages:
    """Error messages when pipeline code tries to use blocked builtins."""

    def test_open_gives_permission_error(self):
        """open() raises PermissionError (not NameError) with actionable message."""
        dag = DAG()
        code = """
try:
    f = open('/etc/passwd')
    result = 'ERROR: no exception'
except PermissionError as e:
    result = f'PermissionError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = execute_pipeline(code, dag).output
        assert "PermissionError" in out, f"Expected PermissionError, got: {out}"
        assert "read(path)" in out or "read(" in out, (
            f"Expected read() suggestion in: {out}"
        )
        assert "vtk_escape" in out, f"Expected vtk_escape suggestion in: {out}"

    def test_open_not_available_message_mentions_alternative(self):
        """open() error message tells agent to use read() instead."""
        dag = DAG()
        code = """
try:
    open('/tmp/test.txt')
except Exception as e:
    print(str(e))
"""
        out = execute_pipeline(code, dag).output
        assert "read" in out.lower(), f"Expected read() mention in: {out}"

    def test_import_gives_informative_error(self):
        """import raises ImportError with explanation and alternatives."""
        dag = DAG()
        code = """
try:
    import os
    result = 'ERROR: no exception'
except ImportError as e:
    result = f'ImportError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = execute_pipeline(code, dag).output
        assert "ImportError" in out, f"Expected ImportError, got: {out}"

    def test_import_error_mentions_module_name(self):
        """Import error message includes the module name being imported."""
        dag = DAG()
        code = """
try:
    import subprocess
except Exception as e:
    print(str(e))
"""
        out = execute_pipeline(code, dag).output
        assert "subprocess" in out, f"Expected module name in: {out}"

    def test_import_error_mentions_numpy_alternative(self):
        """Import error message mentions that numpy is available as 'np'."""
        dag = DAG()
        code = """
try:
    import numpy
except Exception as e:
    print(str(e))
"""
        out = execute_pipeline(code, dag).output
        assert "np" in out, f"Expected numpy alternative mention in: {out}"

    def test_import_error_mentions_vtk_escape(self):
        """Import error message suggests vtk_escape as a workaround."""
        dag = DAG()
        code = """
try:
    import vtk
except Exception as e:
    print(str(e))
"""
        out = execute_pipeline(code, dag).output
        assert "vtk_escape" in out, f"Expected vtk_escape suggestion in: {out}"


# ---------------------------------------------------------------------------
# 5. inspect_exec-specific errors
# ---------------------------------------------------------------------------

class TestInspectExecErrorMessages:
    """Error messages specific to inspect_exec's restricted environment."""

    def _setup_dag(self):
        dag = DAG()
        mesh = make_mesh()
        h = stable_hash(("root", "test"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}
        return dag

    def test_show_in_inspect_exec_gives_nameerror_with_explanation(self):
        """show() in inspect_exec raises NameError explaining it's read-only."""
        dag = self._setup_dag()
        code = """
try:
    show(mymesh)
    result = 'ERROR: no exception'
except NameError as e:
    result = f'NameError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = inspect_exec(code, dag).output
        assert "NameError" in out, f"Expected NameError, got: {out}"
        assert "inspect_exec" in out or "read-only" in out, (
            f"Expected context about inspect_exec in: {out}"
        )

    def test_show_error_suggests_pipeline_script(self):
        """show() error in inspect_exec tells agent where show() belongs."""
        dag = self._setup_dag()
        code = """
try:
    show(mymesh)
except NameError as e:
    print(str(e))
"""
        out = inspect_exec(code, dag).output
        assert "pipeline" in out.lower(), f"Expected 'pipeline' in: {out}"

    def test_read_in_inspect_exec_gives_nameerror_with_explanation(self):
        """read() in inspect_exec raises NameError with explanation."""
        dag = self._setup_dag()
        code = """
try:
    read('/tmp/file.vtk')
    result = 'ERROR: no exception'
except NameError as e:
    result = f'NameError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = inspect_exec(code, dag).output
        assert "NameError" in out, f"Expected NameError, got: {out}"
        assert "pipeline" in out.lower() or "execute_pipeline" in out, (
            f"Expected pipeline context in: {out}"
        )

    def test_screenshot_in_inspect_exec_gives_nameerror_with_explanation(self):
        """screenshot() in inspect_exec raises NameError with explanation."""
        dag = self._setup_dag()
        code = """
try:
    screenshot('/tmp/out.png')
    result = 'ERROR: no exception'
except NameError as e:
    result = f'NameError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = inspect_exec(code, dag).output
        assert "NameError" in out, f"Expected NameError, got: {out}"

    def test_add_mesh_in_inspect_exec_gives_nameerror_with_explanation(self):
        """add_mesh() in inspect_exec raises NameError with explanation."""
        dag = self._setup_dag()
        code = """
try:
    add_mesh(mymesh)
    result = 'ERROR: no exception'
except NameError as e:
    result = f'NameError: {e}'
except Exception as e:
    result = f'OTHER: {type(e).__name__}: {e}'
print(result)
"""
        out = inspect_exec(code, dag).output
        assert "NameError" in out, f"Expected NameError, got: {out}"

    def test_undefined_variable_includes_available_names(self):
        """Unhandled NameError in inspect_exec includes list of available pipeline vars."""
        dag = self._setup_dag()
        with pytest.raises(NameError) as exc_info:
            inspect_exec("x = totally_undefined_variable", dag)
        msg = str(exc_info.value)
        # Should mention available pipeline variables
        assert "mymesh" in msg, f"Expected available var names in: {msg}"

    def test_undefined_variable_with_no_pipeline_vars_gives_hint(self):
        """NameError with no pipeline vars tells agent to run execute_pipeline first."""
        dag = DAG()
        dag.names = {}
        with pytest.raises(NameError) as exc_info:
            inspect_exec("x = totally_undefined_variable", dag)
        msg = str(exc_info.value)
        assert "execute_pipeline" in msg or "pipeline" in msg.lower(), (
            f"Expected pipeline hint in: {msg}"
        )

    def test_undefined_variable_error_still_contains_original_message(self):
        """Enhanced NameError still identifies the missing name."""
        dag = self._setup_dag()
        with pytest.raises(NameError) as exc_info:
            inspect_exec("x = my_typo_variable", dag)
        msg = str(exc_info.value)
        assert "my_typo_variable" in msg, f"Expected missing name in: {msg}"


# ---------------------------------------------------------------------------
# 6. Scalar-sensitive method raises ValueError without scalars=
# ---------------------------------------------------------------------------

class TestScalarSensitiveWarnings:
    """ValueError when scalar-sensitive methods are called without scalars= kwarg."""

    def test_threshold_without_scalars_raises(self):
        """threshold() without scalars= raises ValueError."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.threshold(value=500.0)
        msg = str(exc_info.value)
        assert "threshold" in msg
        assert "scalars=" in msg

    def test_threshold_error_explains_cache_hazard(self):
        """threshold() error explains why this is a caching hazard."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.threshold(value=500.0)
        msg = str(exc_info.value)
        assert "cache" in msg.lower() or "hidden state" in msg.lower(), (
            f"Expected cache/hidden state mention in: {msg}"
        )

    def test_threshold_error_gives_example_fix(self):
        """threshold() error shows the correct fix with scalars= example."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.threshold(value=500.0)
        msg = str(exc_info.value)
        # Should show an example like: scalars='FieldName'
        assert "scalars=" in msg

    def test_threshold_with_scalars_does_not_raise(self):
        """threshold() with explicit scalars= does NOT raise a ValueError."""
        proxy, _ = make_proxy()
        # Should not raise — just works normally
        result = proxy.threshold(value=500.0, scalars="Temperature")
        assert result is not None

    def test_contour_without_scalars_raises(self):
        """contour() without scalars= raises ValueError."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.contour(isosurfaces=[500.0])
        msg = str(exc_info.value)
        assert "scalars=" in msg

    def test_warp_by_scalar_without_scalars_raises(self):
        """warp_by_scalar() without scalars= raises ValueError."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.warp_by_scalar()
        msg = str(exc_info.value)
        assert "scalars=" in msg

    def test_clip_scalar_without_scalars_raises(self):
        """clip_scalar() without scalars= raises ValueError."""
        proxy, _ = make_proxy()
        with pytest.raises(ValueError) as exc_info:
            proxy.clip_scalar(value=500.0)
        msg = str(exc_info.value)
        assert "scalars=" in msg


# ---------------------------------------------------------------------------
# 7. vtk_escape type errors
# ---------------------------------------------------------------------------

class TestVtkEscapeTypeErrors:
    """Error messages when vtk_escape is called with wrong types."""

    def test_vtk_escape_non_proxy_gives_clear_typeerror(self):
        """vtk_escape with a non-proxy raises TypeError naming the type."""
        mesh = make_mesh()
        with pytest.raises(TypeError) as exc_info:
            vtk_escape(mesh, lambda m: m)
        msg = str(exc_info.value)
        assert "TrackedProxy" in msg, f"Expected TrackedProxy in: {msg}"
        assert "ImageData" in msg, f"Expected actual type in: {msg}"

    def test_vtk_escape_non_proxy_names_got_type(self):
        """vtk_escape type error specifies what type was passed."""
        with pytest.raises(TypeError) as exc_info:
            vtk_escape("not a proxy", lambda m: m)
        msg = str(exc_info.value)
        assert "str" in msg or "TrackedProxy" in msg

    def test_vtk_escape_multi_empty_sequence_gives_clear_error(self):
        """vtk_escape_multi with empty list gives clear TypeError."""
        with pytest.raises(TypeError) as exc_info:
            vtk_escape_multi([], lambda: None)
        msg = str(exc_info.value)
        assert "empty" in msg.lower() or "non-empty" in msg.lower()

    def test_vtk_escape_multi_mixed_types_gives_clear_error(self):
        """vtk_escape_multi with non-proxy element names the bad index."""
        proxy, _ = make_proxy()
        with pytest.raises(TypeError) as exc_info:
            vtk_escape_multi([proxy, "not a proxy"], lambda a, b: a)
        msg = str(exc_info.value)
        assert "1" in msg, f"Expected bad index in: {msg}"
        assert "TrackedProxy" in msg


# ---------------------------------------------------------------------------
# 8. Error messages propagate correctly through execute_pipeline
# ---------------------------------------------------------------------------

class TestErrorPropagationInPipeline:
    """Error messages are visible when pipeline code triggers them."""

    def test_save_blocked_visible_in_pipeline(self):
        """Blacklisted save() raises AttributeError that propagates from pipeline exec."""
        dag = DAG()
        import tempfile, os
        mesh = make_mesh()
        tmp = tempfile.mktemp(suffix=".vtk")
        mesh.save(tmp)
        try:
            code = f"""
mesh = read("{tmp}")
mesh.save("output.vtk")
"""
            with pytest.raises(AttributeError) as exc_info:
                execute_pipeline(code, dag)
            msg = str(exc_info.value)
            assert "save()" in msg
            assert "blocked" in msg
            assert "filesystem write" in msg
        finally:
            os.unlink(tmp)

    def test_not_whitelisted_visible_in_pipeline(self):
        """Not-whitelisted method raises AttributeError with vtk_escape suggestion."""
        dag = DAG()
        import tempfile, os
        mesh = make_mesh()
        tmp = tempfile.mktemp(suffix=".vtk")
        mesh.save(tmp)
        try:
            code = f"""
mesh = read("{tmp}")
mesh.nonexistent_method()
"""
            with pytest.raises(AttributeError) as exc_info:
                execute_pipeline(code, dag)
            msg = str(exc_info.value)
            assert "not in the whitelist" in msg
            assert "vtk_escape" in msg
        finally:
            os.unlink(tmp)

    def test_open_error_visible_in_pipeline_without_try_except(self):
        """open() raises PermissionError (not NameError) that propagates from pipeline."""
        dag = DAG()
        with pytest.raises(PermissionError) as exc_info:
            execute_pipeline("open('/etc/passwd')", dag)
        msg = str(exc_info.value)
        assert "read(" in msg or "read(path)" in msg
