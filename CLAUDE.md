# VisLang - Development Guide

This file is for Claude Code when developing the VisLang MCP server and DSL.
Users of the MCP don't see this file -- they get guidance from the MCP server
instructions, tool descriptions, and domain files.

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

## Project Structure

- `BACKLOG.md` -- Prioritized work items. Pick from here during independent work.
- `feedback/` -- Dated feedback entries from agents and humans. Append-only.
- `design/` -- Dated design journal entries tracking project evolution.
- `domains/` -- Domain-specific knowledge files (e.g. wildfire interpretation).
- `datasets/` -- One folder per dataset, each with a `download.sh` and `README.md`.
- `.claude/agents/` -- Subagent definitions for development workflows.

## Datasets and Sessions

### Datasets
Each dataset lives in `datasets/<name>/` with a `download.sh` that fetches
files into `datasets/<name>/data/` (gitignored). If a dataset isn't present
locally, run its download script first.

Available datasets:
- `datasets/wildfire/` -- HIGRAD/FIRETEC fire simulation (output.30000.vts, ~1.1 GB)

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
Read recent feedback entries and the current backlog, then produce an updated
`BACKLOG.md` with new items, reprioritization, and completed items marked.
Trigger: ask Claude to "refine the backlog" or run the refine-backlog agent.

### Design reflection
Review where the project stands architecturally, what's been learned, and
where it should go next. Produces a new dated entry in `design/`.
Trigger: ask Claude to "do a design reflection" or run the reflect-design agent.

### Gather feedback
Try using the MCP to build a visualization, then write honest observations
about what worked and what didn't to `feedback/`.
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
  answer. Read `BACKLOG.md`, pick the highest-priority item you can make
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

**You decide what to work on and why.** Read `BACKLOG.md`, consider what's
been accomplished so far (check `git log --oneline`), and pick the next task.

**Subagents do the how.** They should be fully autonomous: read code, explore
the codebase, implement, run tests, commit, and update `BACKLOG.md`. Give
them a clear brief with enough context to work independently, then let them
handle the details.

**NEVER do implementation yourself.** No reading source code, no editing
files, no fixing bugs, no writing tests. If something needs to change,
launch a subagent. Your job is deciding *what* to work on and writing
good briefs -- not writing code.

**Invoking custom agents.** The agent types defined in `.claude/agents/`
(e.g. `gather-feedback`, `reflect-api`) are not registered in the Agent
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
1. Read `BACKLOG.md` (~1 tool call)
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
2. Read BACKLOG.md -- pick the highest-priority item
3. Launch a subagent to implement it (delegate the work, stay lean)
4. When it completes: review with git log/diff --stat, push
5. Run `date -u +%s` -- compare to deadline
6. If time remains -> go to step 2
7. If time is up -> write a final feedback entry, commit, push, stop
```

### Staying effective

- **Commit and push after each task** -- save progress continuously
- **Write observations to `feedback/`** -- capture what you learn as you go
- **Mark items done in `BACKLOG.md`** -- keep it current
- **Delegate implementation to subagents** -- keep your own context lean for
  decision-making. Use `isolation: "worktree"` for parallel work.
- **Don't ask the user questions** -- make your best judgment and document
  any uncertain decisions in your feedback entry

### Session continuity

Sessions can be interrupted by usage limits at any time. The next session
(possibly days later, with no shared context) must be able to pick up
cleanly from `git log` and `BACKLOG.md` alone.

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
