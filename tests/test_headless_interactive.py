"""Regression test for multi-view in interactive threading mode.

Uses --headless-interactive to exercise the real event loop + work queue
dispatch path without needing a display. This is a Level 3 test (MCP
protocol boundary) that caught the multi-view deadlock bug where each
Renderer had its own work queue but only the first's was drained.

The test launches the server as a subprocess, sends JSON-RPC messages
over stdin/stdout, and verifies responses come back without deadlocking.
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest


def _venv_python():
    """Return the path to the venv Python."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, ".venv", "bin", "python")


def _send(proc, msg):
    """Send a JSON-RPC message to the server."""
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()


def _recv(proc, timeout=15):
    """Read a JSON-RPC response line, with timeout."""
    result = [None]
    error = [None]

    def _read():
        try:
            line = proc.stdout.readline()
            if line:
                result[0] = json.loads(line)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"No response within {timeout}s — possible deadlock")
    if error[0]:
        raise error[0]
    return result[0]


def _call_tool(proc, tool_name, arguments=None, call_id=None):
    """Send a tools/call request and return the response."""
    if call_id is None:
        call_id = tool_name
    msg = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {},
        },
    }
    _send(proc, msg)
    return _recv(proc)


def _start_server():
    """Launch the server in headless-interactive mode and complete handshake."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        [_venv_python(), "-m", "vislang.server", "--headless-interactive"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=here,
    )
    # Send initialize
    _send(proc, {
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        },
    })
    resp = _recv(proc, timeout=10)
    if resp is None or "result" not in resp:
        # Capture stderr for diagnostics
        proc.kill()
        stderr = proc.stderr.read()
        raise RuntimeError(f"Server failed to initialize. stderr: {stderr}")

    # Send initialized notification
    _send(proc, {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })
    time.sleep(0.5)
    return proc


def _stop_server(proc):
    """Shut down the server, capturing stderr for diagnostics."""
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    stderr = proc.stderr.read()
    return stderr


class TestHeadlessInteractiveMultiView(unittest.TestCase):
    """Test multi-view operations through the real threading path."""

    @classmethod
    def setUpClass(cls):
        """Launch the server in headless-interactive mode."""
        # Write a simple pipeline file
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pipeline = 'data = source("vtkSphereSource")\nshow(data, "sphere")\nscene_preset("dark")'
        with open(os.path.join(here, "view-main.py"), "w") as f:
            f.write(pipeline)
        pipeline2 = 'data = source("vtkSphereSource", Radius=2.0)\nshow(data, "sphere2")'
        with open(os.path.join(here, "view-second.py"), "w") as f:
            f.write(pipeline2)

        cls.proc = _start_server()
        cls._stderr = ""

    @classmethod
    def tearDownClass(cls):
        cls._stderr = _stop_server(cls.proc)
        # Clean up pipeline files
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in ["view-main.py", "view-second.py"]:
            path = os.path.join(here, f)
            if os.path.exists(path):
                os.unlink(path)

    def _assert_server_alive(self):
        """Check the server process is still running."""
        ret = self.proc.poll()
        if ret is not None:
            stderr = self.proc.stderr.read()
            self.fail(f"Server crashed (exit code {ret}). stderr:\n{stderr}")

    def test_01_run_pipeline_on_main_view(self):
        """run_pipeline on the default main view should work."""
        self._assert_server_alive()
        resp = _call_tool(self.proc, "run_pipeline", {"file": "view-main.py"}, call_id="pipe1")
        self.assertIsNotNone(resp, "No response from run_pipeline — possible deadlock")
        self.assertIn("result", resp, f"run_pipeline failed: {resp}")
        content = resp["result"]["content"]
        text_parts = [c["text"] for c in content if c.get("type") == "text"]
        full_text = "\n".join(text_parts)
        self.assertTrue(
            "built successfully" in full_text or " ok." in full_text,
            f"Expected build success indicator in: {full_text!r}"
        )

    def test_02_new_view_and_run_pipeline(self):
        """Creating a second view and setting its pipeline must not deadlock."""
        self._assert_server_alive()
        resp = _call_tool(self.proc, "new_view", {"name": "second"}, call_id="nv1")
        self._assert_server_alive()
        self.assertIsNotNone(resp, "No response from new_view — possible deadlock")
        self.assertIn("result", resp, f"new_view failed: {resp}")

        # This is the critical call — it deadlocked before the shared work queue fix
        resp = _call_tool(self.proc, "run_pipeline", {"file": "view-second.py"}, call_id="pipe2")
        self._assert_server_alive()
        self.assertIsNotNone(resp, "No response from run_pipeline on second view — DEADLOCK")
        self.assertIn("result", resp, f"run_pipeline on second view failed: {resp}")
        content = resp["result"]["content"]
        text_parts = [c["text"] for c in content if c.get("type") == "text"]
        full_text = "\n".join(text_parts)
        self.assertTrue(
            "built successfully" in full_text or " ok." in full_text,
            f"Expected build success indicator in: {full_text!r}"
        )

    def test_03_focus_back_to_main(self):
        """Switching back to the main view should work."""
        self._assert_server_alive()
        resp = _call_tool(self.proc, "focus", {"name": "main"}, call_id="focus1")
        self.assertIsNotNone(resp, "No response from focus — possible deadlock")
        self.assertIn("result", resp, f"focus failed: {resp}")

    def test_04_set_suggested_camera_with_scalar_bar(self):
        """set_suggested_camera should not crash when scalar bars are present."""
        self._assert_server_alive()
        # Set up a pipeline with a scalar bar
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pipeline = 'data = source("vtkSphereSource")\nshow(data, "sphere", scalar_bar="Test")'
        with open(os.path.join(here, "view-main.py"), "w") as f:
            f.write(pipeline)
        resp = _call_tool(self.proc, "run_pipeline", {"file": "view-main.py"}, call_id="pipe3")
        self._assert_server_alive()

        resp = _call_tool(self.proc, "set_suggested_camera", {"style": "overview"}, call_id="cam1")
        self.assertIsNotNone(resp, "No response from set_suggested_camera")
        self.assertIn("result", resp, f"set_suggested_camera failed: {resp}")
        content = resp["result"]["content"]
        text_parts = [c["text"] for c in content if c.get("type") == "text"]
        full_text = "\n".join(text_parts)
        self.assertIn("Camera set to", full_text)


if __name__ == "__main__":
    unittest.main()
