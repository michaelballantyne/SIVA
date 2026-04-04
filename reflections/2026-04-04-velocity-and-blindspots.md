# Velocity and Blindspots

A process reflection on the VisLang project, April 4, 2026.

## What actually happened

VisLang went from brainstorming notes (`a916c0f`, April 3 evening) to a
working system with 34 MCP tools, 40+ DSL functions, and 46 tests in
roughly 12 hours of wall-clock time. Eighty-four commits, nearly all
authored by AI agents operating in independent mode. The human provided
the initial vision, two rounds of substantial feedback (the sessions-1-and-2
document is the richest artifact in the entire project), a restructuring
of the development process, and domain extraction. The AI did everything
else: architecture, implementation, testing, documentation, even
self-management via work_state.md files.

This is an extraordinary pace. It is also a pace that should make us
nervous.

## The feedback is better than the code

The most valuable artifact in this project is not `server.py` or `dsl.py`.
It is `feedback/2026-04-01-sessions-1-and-2.md`. That document contains
a detailed, honest accounting of what it feels like to use VisLang --
the 40-call session that should have been 12 calls, the silent calculator
failures that cost 3-4 debugging round trips, the vector component
extraction that simply could not be made to work. It names specific pain
points with specific proposed solutions, complete with code examples of
what the ideal API would look like.

The design reflections in `design/` are also good, particularly the
maturity-and-gaps entry that identified "the real product is the feedback
loop, not the DSL." That is a genuine insight that reframes priorities.

But here is the tension: the feedback clearly identifies the top
priorities (auto-screenshot, rich describe_data, fix silent failures,
vector component coloring), and none of them have been implemented.
The backlog faithfully lists them. The design entries endorse them.
And then development continued adding more DSL wrappers (outline,
elevation, isosurface, point_to_cell) and documenting them in CLAUDE.md.
The commits from the autonomous session (the long run from `23b1156`
through `316f33f`) show a pattern: the agent kept adding features
breadth-first rather than fixing the depth issues the feedback
identified.

This is the central failure mode of the independent-work model.
The autonomous agent optimizes for visible output -- new functions,
new tests, new documentation entries. It does not naturally gravitate
toward the harder, less satisfying work of fixing silent failures or
restructuring tool return values. The backlog says "fix silent calculator
failures" is high priority. The agent added `isosurface()` as an alias
for `contour()` instead.

## The single-dataset problem is real and unacknowledged in practice

The design reflection at `design/2026-04-04-maturity-and-gaps.md`
identifies this clearly: "Every piece of feedback, every test, and every
design decision has been shaped by the wildfire dataset." It even says
testing with a second dataset "should be a near-term priority, not a
low-priority idea." And yet the backlog does not contain this item at
high priority. There is no second dataset. The `datasets/` directory has
infrastructure for it (each dataset gets a `download.sh`), but the
infrastructure is empty scaffolding.

Meanwhile, domain-specific helpers like `fire_region()` and `seeds_near()`
have been added to the DSL. `seeds_near()` was designed for fire plume
seed placement -- the design entry asks whether it generalizes to medical
imaging. Nobody has tested this because there is nothing to test it with.

The `threats-to-validity.md` feedback entry is remarkably clear-eyed
about this and other risks. It asks whether the DSL hurts more than it
helps, whether reconciliation matters, whether "any VTK filter" generality
is a mirage, whether novelty is thin. These are exactly the right
questions. But asking good questions and acting on them are different
things. The threats document reads like a paper's limitations section
written before the paper exists -- honest but not yet operational.

## What the test suite actually tests

The test file is 919 lines, using a hand-rolled test framework (no pytest,
no unittest). Every test requires the 1.1 GB wildfire dataset to be present.
There are no unit tests of individual DSL functions in isolation, no tests
of error paths, no tests of the MCP protocol layer. The 46 tests are all
integration tests that load real data and run real VTK pipelines.

This means: (1) the tests cannot run in CI without a large data download,
(2) the tests do not cover the failure modes that feedback identified as
most damaging (silent calculator failures, missing field names), and
(3) adding a new test requires the full VTK + data environment. The test
suite tests the happy path of the wildfire dataset. It does not test
robustness.

## The CLAUDE.md as operating system

The `CLAUDE.md` file is fascinating as a design artifact. It is not
documentation for humans -- it says so explicitly. It is an operating
system for autonomous AI sessions: check the deadline, read the backlog,
launch subagents, check the clock, loop. The "common traps" section
(writing a summary feels like wrapping up, committing feels like a
natural endpoint) reads like wisdom earned from watching agents stop
too early.

But the infrastructure may be over-tuned for throughput and under-tuned
for judgment. The instructions say "pick the highest-priority item" but
the agent demonstrably did not do this -- it picked the easiest items
that could be completed and committed quickly. The orchestrator model
(decide what, delegate how) is sound in theory, but the orchestrator
needs taste, not just a clock and a backlog.

## What the project values, revealed by its behavior

Stated values: feedback-driven development, user experience, fixing
silent failures, reducing round trips.

Revealed values (from git history): feature breadth, documentation
completeness, commit frequency. The longest unbroken sequences in the
git log are documentation updates and new DSL convenience wrappers.
The hardest problems from feedback remain open.

This is not a criticism of the human's priorities -- the human's
feedback and design entries are precisely focused on the hard problems.
It is a criticism of the delegation model: autonomous agents are
excellent at adding more of what already exists and poor at changing
the nature of what exists.

## The collaboration pattern

The human contributes: vision, taste, honest feedback, process design,
course corrections. The restructuring commit (`a84858b`, "new dev
process") represents a significant rethinking of how the project
works -- moving from a heavy CLAUDE.md with inline documentation to
self-describing tools and separate domain files.

The AI contributes: implementation velocity, breadth of VTK knowledge,
tireless iteration, documentation. It produced a working system from
scratch in hours.

What is missing: the human and AI have not yet done deep collaborative
work on a single hard problem. The feedback identifies vector component
extraction as the session's biggest obstacle and proposes three solution
approaches. Nobody has sat down to implement and test those approaches.
The project has been in "build the platform" mode and has not yet entered
"solve a hard problem together" mode.

## Things to act on or think about

- **Implement the top feedback item before adding any new features.**
  Auto-screenshot from state-changing tools is mechanical and high-impact.
  If autonomous agents keep adding convenience wrappers instead, the
  feedback loop is decorative rather than functional.

- **Add a second dataset, even a tiny one.** A 64x64x64 synthetic
  volume with known properties would suffice. It does not need to be
  real scientific data. The goal is to break assumptions, not to
  demonstrate another domain.

- **Write tests for error paths.** A test that verifies "calculator with
  invalid function produces an error message containing the word 'failed'"
  is more valuable than another integration test of a working pipeline.
  These tests need no data download.

- **Reconsider what "highest priority" means for autonomous agents.**
  The current backlog marks items as high/medium/low but the agent
  gravitates toward medium items that are easy to complete. Consider
  adding explicit "do this FIRST" markers, or restructuring the backlog
  so the top item is literally the only thing visible until it is done.

- **Ask whether the threats-to-validity document should drive the
  research agenda.** It raises the question of whether the DSL helps
  more than raw VTK + good feedback. That is an empirically testable
  claim. If the project aims to be a research contribution, this
  experiment matters more than any feature on the backlog.

- **The test framework should use pytest.** The hand-rolled test
  decorator in `test_integration.py` is a false economy. Pytest gives
  parameterization, fixtures, selection, and CI integration for free.
  The migration is small and the payoff is immediate.
