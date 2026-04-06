# Reflection Machines

A process reflection on the VisLang project, April 6, 2026.

## The reflection infrastructure is now the main product

Looking at today's git history, something striking jumps out. Of the 16
commits on April 6, ten are reflections, feedback entries, backlog updates,
and steering changes. Only three touch actual code: terrain-following grid
detection (`114f172`), the close_view fix (`d027f52`), and DSL discovery
consolidation (`54cf9dc`). The rest -- seven commits -- are about improving
tests, fixing test infrastructure, and updating CLAUDE.md steering.

The project has built an elaborate apparatus for thinking about itself. There
are seven agent definitions in `.claude/agents/`, a feedback directory with
twelve entries, a design journal with six entries, and now this -- the
second process reflection. The CLAUDE.md is 294 lines of instructions for
how autonomous agents should work, including detailed anti-patterns and
workflow templates. The VISION.md is a 500-line strategic document spanning
current design, next steps, longer-term vision, and research context.

This infrastructure is genuinely well-designed. The gather-feedback agent
produces detailed, honest session observations (the wildfire-vls-session-2
feedback at `7b3283e` is an exemplary piece of analysis, noting both what
worked and the specific `KeyError: 'num_points'` bug that recurs). The
reflect-api agent (`df42990`) systematically audited 45 tools and identified
concrete redundancies. The reflect-design agent (`3c53264`) synthesized
session evidence into architectural decisions. These are good artifacts that
contain real insight.

But there is a ratio question. The codebase is 8,288 lines of source and
7,628 lines of tests. The meta-layer -- CLAUDE.md, VISION.md, BACKLOG.md,
feedback, design, reflections, agent definitions, TESTING.md -- is
approaching comparable volume. The project is spending roughly as much
effort observing and steering itself as it spends building the thing being
observed and steered.

## The feedback loop works, but selectively

The previous reflection (`2026-04-04-velocity-and-blindspots`) identified
the central failure mode clearly: "The autonomous agent optimizes for visible
output -- new functions, new tests, new documentation entries. It does not
naturally gravitate toward the harder, less satisfying work." The human's
response noted this was a prediction about new infrastructure that hadn't
been tested yet.

Two days later, the evidence is mixed. The terrain-following grid detection
(`114f172`) is a clean example of the loop working: the wildfire session
exposed a failure mode, feedback documented it, code was fixed, and the next
session used the fix correctly on the first attempt. The design reflection
(`3c53264`) notes this explicitly as "the strongest evidence so far that the
feedback loop is working."

But the backlog's top items remain untouched. "Remove phantom tools" is
described as a "one-line fix with outsized impact on reliability." It has
been in the backlog since the API reflection identified it. "Remove
auto-screenshots from state-changing tools" was identified in the bonsai
session on April 4. "Merge overlapping query tools" was first noted in
sessions-1-and-2 feedback. None of these have shipped.

What did ship: terrain detection (real and valuable, but triggered by a
specific session failure, not by the backlog), close_view fix (`d027f52`,
also triggered by a session), DSL consolidation (`54cf9dc`), and a
substantial amount of test infrastructure work. The pattern from the
previous reflection persists: session-triggered fixes happen; backlog items
don't get picked up unless they cause an active session failure.

This is not a failure of the reflection infrastructure. The reflections
correctly identify the problem. The problem is that reflections and
backlogs don't have the same forcing function as a broken session. The
human runs a session, hits a bug, fixes the bug. The human does not
(yet) sit down and say "clear the phantom tools from the backlog."

## The planning-to-building ratio has inverted

The project's first three days (April 3-5) shipped the entire working
system: the DSL, the MCP server, 34+ tools, multi-view support, six
datasets, 514 tests. Commit `23b1156` ("Implement VisLang Phase 1")
was April 4. Everything from annotations to camera orbits to line probes
shipped in a dense burst of implementation.

April 5 shifted to vision and strategy: seven commits iterating on
VISION.md, domain knowledge, related work, and process infrastructure.
One code fix (multiview), one feature (headless interactive for tests).

April 6 is almost entirely reflection: twelve commits touching feedback,
design, reflections, backlog, and CLAUDE.md. Three code changes.

The trajectory is: build furiously, then reflect furiously. This is not
necessarily wrong -- the bonsai session on April 4 revealed enough
problems that pausing to think was warranted. But the risk is that
reflection becomes the comfortable mode. Writing about what should change
is easier than changing it. The project has generated more words about
removing phantom tools than it would take to remove them.

## The epistemological question

How does the project know it works? The evidence base is:

1. **Session observations**: Five documented sessions (two wildfire, one
   bonsai, two wildfire VLS) with detailed feedback. These are the richest
   evidence but sample a narrow range: one human (the project author), two
   datasets, similar exploration patterns.

2. **Test suite**: 514 tests, mostly using synthetic data via conftest.py
   helpers. The bonsai dataset tests (`test_bonsai_dataset.py`, 19 tests)
   are the only tests using a real non-synthetic dataset. The wildfire
   integration tests require a 1.1GB download. Test coverage is good for
   happy paths; error path coverage improved since the previous reflection
   (50 tests in `test_error_paths.py`).

3. **The reflection artifacts themselves**: But these are all written by AI
   agents reflecting on AI-generated artifacts observed by a single human.
   The threats-to-validity entry (`2026-04-01`) raised exactly the right
   concern: "the DSL is a new language with less training coverage...maybe
   we should provide good feedback on raw VTK code rather than inventing a
   DSL." This remains empirically untested.

The project has never been used by someone who didn't build it. No external
user, no usability test, no comparison against alternatives (raw VTK +
feedback, ParaView, vtk-prompt). The sessions are self-evaluation, not
independent evaluation. This is normal for a prototype at this stage, but
the reflection infrastructure creates an illusion of rigor that the evidence
base doesn't fully support.

## What the subagent model actually produces

The current session (`9a9ad52b`) shows the orchestration pattern clearly:
the human asks to run five reflection agents in sequence; the orchestrator
launches each, checks the output with `git log --oneline -3` and
`git diff --stat`, then launches the next. The agents produce solid
analytical documents.

But there is a sameness to their output. The API reflection, code quality
reflection, and design reflection all identify the same issues: phantom
tools, server.py too large, mutation tool divergence, overlapping query
tools. They identify them from different angles (API surface, code
structure, session evidence), which adds some perspective. But the
convergence also suggests diminishing returns -- the fifth reflection on
the same codebase is unlikely to reveal what the first four missed.

The gather-feedback agent, by contrast, operates on different input each
time (a new session log) and consistently produces novel observations. The
wildfire-vls-session-2 feedback identified the "Claude offering focus() as
user guidance" confusion -- a genuinely new insight about the interaction
model. The bonsai feedback identified the context bloat problem. Each
session surfaces different things because each session is different.

This suggests the project would benefit from more sessions and fewer
reflections-on-reflections. One new session with a new user or a new
dataset would produce more actionable insight than three more code quality
audits.

## Values in tension

The project explicitly values: feedback-driven development, user experience,
correctness over preserving patterns, and the "pipeline file as shared
artifact" principle.

It implicitly values: self-documentation, process sophistication, and
comprehensive analysis. The amount of infrastructure devoted to thinking
about the project (seven agent definitions, multi-level reflection, the
elaborate independent-mode instructions in CLAUDE.md) reveals a strong
preference for systematic process over ad hoc work.

The tension is that systematic process generates systematic artifacts
(reflections, backlogs, design entries) rather than the messy,
session-triggered fixes that actually move the needle. The terrain detection
fix -- the project's best example of feedback driving improvement -- happened
because someone ran a session and hit the bug, not because a reflection
agent identified it.

The VISION.md is ambitious and intellectually serious. The LSP vision, the
bidirectional editing ideas, the Lean InfoView-inspired node inspection --
these are genuinely interesting research directions. But the gap between
vision and implementation is widening. The vision now describes features
(hot reload, parameter scrubbing, LSP diagnostics) that are architecturally
distant from the current codebase, which still has phantom tools in its
tool list and a 3,048-line server.py.

## Things to act on or think about

- **Implement the phantom tool removal and one other top-backlog item before
  running another reflection agent.** The project has generated more analysis
  of the phantom tool problem than the fix requires. This is the simplest
  test of whether the feedback loop actually drives code changes.

- **Run a session with someone who didn't build the project.** Even an
  informal 30-minute test would produce evidence qualitatively different
  from self-evaluation. The gather-feedback agent is well-designed to
  analyze such a session.

- **Set a ratio target: at most one reflection artifact per two code-change
  commits.** The current ratio is inverted. Reflections are valuable but
  subject to diminishing returns; the fifth identification of the same
  problem adds less than fixing it.

- **Consider whether the CLAUDE.md independent-mode infrastructure is
  being tested.** The elaborate orchestrator instructions (the work loop,
  the anti-pattern catalog, the subagent delegation model) were designed
  for long autonomous sessions. How many such sessions have actually run
  under the current process? If the answer is few, the infrastructure may
  be over-designed for a workflow that doesn't happen often.

- **The gather-feedback agent is the project's most valuable reflective
  tool.** It operates on novel input (session logs) and produces novel
  output. The reflect-api and reflect-code-quality agents, by contrast,
  re-analyze the same codebase and converge on the same findings. Consider
  running gather-feedback more often and the code/API reflections less
  often, or only after significant code changes.

- **The threats-to-validity document raises testable hypotheses that remain
  untested.** "Does the DSL help more than raw VTK + good feedback?" is the
  most important open question for the project's research contribution. No
  amount of self-reflection answers it. An experiment does.
