# VisLang - Development Guide

This file is for Claude Code when developing the VisLang MCP server and DSL.
Users of the MCP don't see this file -- they get guidance from the MCP server
instructions, tool descriptions, and domain files.

## Design Philosophy

VisLang is in active prototyping and design phase. There are no backwards
compatibility constraints. Demos, tests, and examples exist to validate
ideas -- they do not lock us into bad behavior. If a better design emerges,
change the API and update everything that depends on it. Prefer consistency
and correctness over preserving existing patterns.

## Python Environment

All dependencies are installed in `.venv/`. Always use `.venv/bin/python`
(or activate the venv) when running scripts, tests, or the server directly.

### Worktree / subagent setup

Git worktrees don't share the parent's `.venv/`. If `.venv/bin/python`
doesn't exist (e.g. you're in a fresh worktree), create it first:

```bash
python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
```

**When working in a worktree, always pass the worktree's absolute path to
subagents** (in prompts, `path` parameters for Glob/Grep/Read, etc.). The
outer repo may have diverged — reading files or searching from the default
directory can silently pick up stale code from the wrong copy.

### Cloud / web environment setup

When running in Claude Code cloud or web environments where the venv and
system dependencies aren't already installed, run:

```bash
bash scripts/cloud-env-setup.sh
```

This installs Xvfb, creates the `.venv/`, and pip-installs the project.

## Server Launch Modes

```bash
# Interactive (default) -- opens a live VTK window
python -m vislang.server

# Off-screen -- headless rendering, returns screenshots only
python -m vislang.server --offscreen
```

**For development and testing (CI, subagents, automated work), always use
`--offscreen`.** The interactive window requires a display and will block in
headless environments.

The server logs to `.vislang/server.log` in the working directory (DEBUG
level). Stderr is reserved for the MCP protocol, so all diagnostic output
goes to this file.

### Offscreen rendering requires Xvfb

VTK's offscreen rendering needs an X server for OpenGL context creation.
In headless environments (CI, remote servers, Claude Code web), use `xvfb-run`:

```bash
# Always prefix offscreen commands with xvfb-run in headless environments
xvfb-run -a python -m vislang.server --offscreen

# For running tests that involve rendering
xvfb-run -a python -m pytest tests/ -q
```

Without `xvfb-run`, VTK will segfault on `render()` or `screenshot()` calls.
This applies to subagent sessions too — any script that calls the renderer
directly needs the `xvfb-run -a` wrapper.

## Project Structure

- `README.md` -- User-facing project description and setup guide.
- `VISION.md` -- Current design, longer-term vision, and research context. Strategic
  document — read for understanding, but don't edit without human review.
- `meta/BACKLOG.md` -- Prioritized work items. Pick from here during independent work.
- `meta/feedback/` -- Dated feedback entries from agents and humans. Append-only.
- `meta/design-reflections/` -- Dated design reflections (code quality, API, direction).
- `meta/process-reflections/` -- Dated process reflections (development workflow, values).
- `domains/` -- Domain-specific knowledge files (e.g. wildfire interpretation).
- `datasets/` -- One folder per dataset, each with a `download.sh` and `README.md`.
- `.claude/agents/` -- Subagent definitions for development workflows.
- `docs/` -- Generated documentation. **Do not edit directly.** See below.
- `scripts/gen_docs.py` -- Generates `docs/` and parts of `README.md` from source docstrings.
- `meta/TESTING.md` -- Testing philosophy, test levels, and guidance for manual
  interactive testing. Read this before writing tests or implementing features
  that touch threading/rendering/state.

## Documentation

Files in `docs/` (including `mcp-reference.md`, `dsl-reference.md`,
`getting-started.md`, `instructions.md`) are **generated** by `scripts/gen_docs.py`.
Never edit them by hand — your changes will be overwritten. `README.md` is
hand-written and should be edited directly.

**Any time you modify docstrings** in `vislang/server.py`, `vislang/dsl.py`,
or other source files that feed into docs, you must regenerate before you're
done:

```bash
python scripts/gen_docs.py
```

## Datasets and Sessions

### Datasets
Each dataset lives in `datasets/<name>/` with a `download.sh` that fetches
files into `datasets/<name>/data/` (gitignored). If a dataset isn't present
locally, run its download script first.

Available datasets:
- `datasets/wildfire/` -- HIGRAD/FIRETEC fire simulation (output.30000.vts, ~1.1 GB, StructuredGrid)
- `datasets/bonsai/` -- CT scan of a bonsai tree (uint8 volume, ImageData)
- `datasets/cthead/` -- Stanford CT head scan (256x256x113, uint16 raw binary)
- `datasets/foot/` -- Rotational C-arm x-ray of a human foot (.vti, ImageData)
- `datasets/hydrogen_atom/` -- Electron probability distribution in magnetic field (uint8, ImageData)
- `datasets/synthetic/` -- Procedurally generated 64x64x64 test volume with temperature, density, velocity fields (no download needed, runs generate.py)

### Sessions
When working with the MCP server (testing, feedback gathering, visualization
work), create a session folder and work from there:

```bash
# Create a session folder
mkdir -p sessions/my-session-name

# Symlink in the dataset
ln -s ../../datasets/wildfire/data/output.30000.vts sessions/my-session-name/

# Run the MCP server from the session folder
cd sessions/my-session-name && python -m vislang.server --offscreen
```

The MCP server's `list_data_files()` finds files in its working directory.
Each session gets its own pipeline.py, screenshots, and history.
The `sessions/` directory is gitignored.

## Development Workflows

### Refine the backlog
Read VISION.md, the current backlog, recent design and process reflections,
new feedback entries, and recent git history, then produce an updated
`meta/BACKLOG.md` with new items, reprioritization, and completed items marked.
Trigger: ask Claude to "refine the backlog" or run the refine-backlog agent.

### Design reflection
Review code quality, API surface, and design direction in a single pass.
Produces a dated entry in `meta/design-reflections/`.
Trigger: ask Claude to "do a design reflection" or run the reflect-design agent.

### Process reflection
Step back and reflect on the project's development process, direction,
epistemology, and values. Reads session transcripts, project artifacts, and
git history. Produces a dated essay in `meta/process-reflections/`.
Trigger: ask Claude to "do a process reflection" or run the reflect-process agent.

### Gather feedback
Analyze a Claude Code session log (JSONL) from a VisLang MCP session and
write structured observations about what worked and what didn't to
`meta/feedback/`. Requires a path to the log file.
Trigger: ask Claude to "gather feedback" or run the gather-feedback agent.

---

## Independent Mode

**Check for `SESSION_END` in the project root.** If it exists, you are in
independent mode. The file contains a UTC unix timestamp (output of
`date -u +%s` with an offset) -- that is your deadline. The user is not
watching and will not respond to questions. They're trusting you to work
autonomously until that time.

### How to stay on track

The hardest part of independent work is knowing when you're done. The answer
is simple: **you're done when `date -u +%s` exceeds the timestamp in
`SESSION_END`.** That is the only stopping condition. Everything else --
finishing a task, writing a summary, committing code, hitting an error -- is
just a moment in the middle of the session. Keep going.

Common traps to watch for:

- **Writing a summary feels like wrapping up.** It isn't. Write your notes,
  then start the next task. Summaries are progress tracking, not conclusions.
- **Committing feels like a natural endpoint.** It isn't. Commit often to
  save your work, then immediately pick up the next item.
- **Hitting an error feels like a reason to stop and report.** The user isn't
  there to read your report. Diagnose, try another approach, or if truly stuck
  after 2-3 attempts, move to the next backlog item.
- **Not knowing what to do next feels like a reason to ask.** The user can't
  answer. Read `meta/BACKLOG.md`, pick the highest-priority item you can make
  progress on. If the backlog feels empty, run the backlog refinement agent.
  If you genuinely cannot find anything useful to do (unlikely), work on test
  coverage or documentation.
- **Feeling "done enough" partway through.** There's always more to do. The
  backlog, the feedback, the tests. Trust the deadline, not your intuition
  about completeness.

### Your role as orchestrator

Your context window is the session's continuity -- it's what connects one task
to the next and keeps the work coherent. Protect it by delegating the heavy
work.

**You decide what to work on and why.** Read `meta/BACKLOG.md`, consider what's
been accomplished so far (check `git log --oneline`), and pick the next task.

**Subagents do the how.** They should be fully autonomous: read code, explore
the codebase, implement, run tests, commit, and update `meta/BACKLOG.md`. Give
them a clear brief with enough context to work independently, then let them
handle the details.

**NEVER do implementation yourself.** No reading source code, no editing
files, no fixing bugs, no writing tests. If something needs to change,
launch a subagent. Your job is deciding *what* to work on and writing
good briefs -- not writing code.

**Invoking custom agents.** The agent types defined in `.claude/agents/`
(e.g. `gather-feedback`, `reflect-design`) are not registered in the Agent
tool's `subagent_type` parameter. To invoke them, use
`subagent_type: "implement"` and paste the agent file's instructions
into the prompt. Only the built-in types (`implement`, `refine-backlog`,
`reflect-design`, `reflect-process`, `gather-feedback`, `Explore`, `Plan`)
are available as `subagent_type` values.

**Check results lightly.** After a subagent finishes, `git log --oneline -5`
and `git diff --stat` tell you what changed without filling your context with
source code. If the work looks wrong, launch a follow-up subagent with the
error context -- don't try to fix it yourself.

**Your per-task cycle should be roughly:**
1. Read `meta/BACKLOG.md` (~1 tool call)
2. Pick a task and write a brief (~0 tool calls, just thinking)
3. Launch subagent (~1 tool call, then wait)
4. Review result (~2-3 tool calls: git log, git diff --stat)
5. Check clock with `date -u +%s` (~1 tool call)
6. Loop

That's ~5-6 tool calls per task. If you're routinely spending 20+ tool calls
between subagent launches, you're doing too much yourself.

### The work loop

```
1. Read SESSION_END -- note your deadline
2. Read meta/BACKLOG.md -- pick the highest-priority item
3. Launch a subagent to implement it (delegate the work, stay lean)
4. When it completes: review with git log/diff --stat, push
5. Run `date -u +%s` -- compare to deadline
6. If time remains -> go to step 2
7. If time is up -> write a final feedback entry, commit, push, stop
```

### Staying effective

- **Commit and push after each task** -- save progress continuously
- **Write observations to `meta/feedback/`** -- capture what you learn as you go
- **Mark items done in `meta/BACKLOG.md`** -- keep it current
- **Delegate implementation to subagents** -- keep your own context lean for
  decision-making.
- **Always use `isolation: "worktree"` for background subagents** -- any agent
  launched with `run_in_background: true` MUST use `isolation: "worktree"`,
  even if you believe it touches different files. Without isolation, concurrent
  agents cause problems in two ways: git operations (staging, committing) in
  the shared working tree conflict unpredictably, and agents running tests
  concurrently can see each other's uncommitted changes, leading to confusing
  failures or false passes that don't reflect the agent's own work.
- **Don't ask the user questions** -- make your best judgment and document
  any uncertain decisions in your feedback entry

### Session continuity

Sessions can be interrupted by usage limits at any time. The next session
(possibly days later, with no shared context) must be able to pick up
cleanly from `git log` and `meta/BACKLOG.md` alone.

**WIP commits.** If you commit work that isn't finished, prefix the message
with `WIP:` and include a short "Status / Remaining" section in the body:

```
WIP: extract_component helper

Adds extract_component to DSL and exposes it as an MCP tool.

Status / Remaining:
- [x] DSL function and server tool
- [ ] Integration tests for edge cases
- [ ] Update MCP tool description with examples
```

This tells the next session exactly where to resume.

**Backlog partial progress.** When a task is in progress but not complete,
mark it `[~]` instead of `[ ]` and add a short inline note:

```
- [~] extract_component helper — DSL + tool done, needs tests
```

This prevents the next session from re-implementing or skipping it.
