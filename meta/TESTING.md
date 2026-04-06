# VisLang Testing Guide

How to test new features and keep the test suite well structured.

## Test levels

### Level 1: Unit tests on core logic

Pure Python tests of queries, filters, and DSL builder functions. No MCP
protocol, no rendering. These verify that the logic is correct: does
`get_statistics` return the right numbers? Does `threshold` produce the
expected number of points? Does the DSL builder construct the right node
graph?

- Fast, no infrastructure beyond VTK + a small test dataset
- Most tests should be at this level
- Location: `tests/test_*.py` (existing pattern)

### Level 2: Stateful integration tests

Execute sequences of operations that exercise server state: load data,
set pipeline, create views, switch views, restore versions. These test
that stateful interactions work correctly — not just individual
functions in isolation.

Key scenarios to cover:
- Multi-view: create view, set pipeline, switch back, verify first view
  is intact
- Version history: set pipeline, modify, restore version, verify state
- Combined operations: load data, describe data, set pipeline, query a
  filtered node
- State divergence: mutation tools + pipeline rebuild interactions

These call the Python functions directly (no MCP protocol) since the
statefulness is in the server's Python state, not in the protocol.

### Level 3: MCP protocol tests

Call tools through the actual MCP protocol and verify responses
serialize correctly. These don't need to re-test the logic (levels 1-2
do that) — they verify that the MCP SDK can serialize the tool's return
value without errors.

A parameterized test iterating over all `@mcp.tool` functions with
minimal valid inputs would catch type annotation mismatches
systematically. This is the layer that would have caught the bonsai
session's Pydantic validation error (function returned `[str, Image]`
but was annotated `-> str`).

Coverage goal: every MCP tool called once with valid args, response
is well-formed and matches the declared return type.

### Level 4: Manual interactive testing

Some issues can only be found through interactive use — concurrency
between the MCP thread and VTK's main thread, responsiveness during
long operations, visual correctness. These aren't automated but should
be exercised deliberately.

## Manual testing for interactive/concurrent features

Features that touch threading, rendering, or stateful interactions
should be manually tested for responsiveness and state consistency.
Think about what happens when operations overlap: a rebuild during user
interaction, rapid successive edits, MCP requests arriving during a
build. Report issues in `meta/feedback/`.

### Testing interactive mode headlessly with Xvfb

Offscreen mode (`--offscreen`) bypasses the threading and windowing code
paths that interactive mode uses. Bugs like work queue deadlocks and
multi-window issues only manifest in interactive mode. **If your change
touches threading, the Renderer event loop, multi-view, or window
management, test in interactive mode — not just offscreen.**

Xvfb provides a virtual X display, so interactive mode works in headless
environments (CI, cloud, subagent sessions):

```bash
# Launch the server in interactive mode under Xvfb
xvfb-run -a python -m vislang.server   # no --offscreen!
```

You can exercise the server via JSON-RPC over stdin/stdout (the MCP
stdio transport), sending `tools/call` messages for sequences like
new_view → set_pipeline → focus → set_pipeline.

To verify what VTK actually rendered to the display (multiple windows,
correct updates), capture the Xvfb framebuffer:

```bash
# Capture the whole virtual display
import -window root -display :99 framebuffer.png

# Capture just the VisLang window
import -window "VisLang" -display :99 window.png
```

To simulate mouse interaction (rotate, click) use xdotool:

```bash
# Click-drag to rotate the trackball camera
xdotool mousemove 500 400
xdotool mousedown 1
xdotool mousemove --delay 50 600 350
xdotool mouseup 1
```

This lets you test real concurrency: interleave xdotool mouse drags
with JSON-RPC pipeline rebuilds to exercise the main-thread work queue
under contention.

## Writing new tests

- Prefer level 1-2 tests. Only use level 3 when testing the protocol
  boundary specifically.
- Use existing small test datasets rather than requiring large data
  downloads. Tests that need the wildfire dataset (1.1 GB) should be
  marked and skippable.
- Test error paths, not just happy paths. A test that verifies "invalid
  field name produces a clear error message" is often more valuable than
  another happy-path integration test.
- When fixing a bug, write a test that reproduces it before fixing it.
