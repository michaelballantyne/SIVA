# Process Reflection: April 6, 2026

## The feedback loop is the methodology

The most striking thing about how VisLang is being developed is that the
feedback loop -- session observation, feedback file, code fix, next session --
isn't just a practice the project follows. It is the development methodology.
The design journal entry from today documents this concretely: terrain-following
grid detection went from "invisible failure mode causing 9 pipeline iterations
and a human hint" to "correctly applied on the first attempt" across two
sessions and one code change. The third wildfire session built six views with
essentially no wasted iterations.

This is genuine evidence of the loop working. Not as a reporting mechanism, but
as the thing that decides what gets built next. The prioritization of backlog
items is overwhelmingly driven by what went wrong in the last session. The
bonsai session's Pydantic validation bug (55 wasted tool calls) led directly to
the auto-screenshot removal backlog item. The wildfire session's ground
extraction failure led to terrain detection. The vorticity threshold sweep led
to requests for spatial/conditional queries.

The risk is that the methodology is entirely reactive. Every improvement is a
response to a failure that already happened. There is almost no speculative
work -- no one is building features because they would enable new workflows,
only fixing features because they broke existing ones. The backlog's low
priority section has genuinely interesting items (LSP, multi-timestep, Jupyter
integration, Agents SDK feedback loop) that have sat untouched while the high
priority section grows with each session. The project is getting better at what
it already does while deferring what it might become.

## The planning-to-building ratio is inverted

I count six reflection and feedback entries from today alone (three wildfire
session feedbacks, an API reflection, a code quality reflection, and a design
reflection), plus the backlog refinement that synthesized them. The backlog has
grown to 35+ items across three priority tiers, with detailed implementation
notes for each.

Meanwhile, the git log shows the actual code changes today: a backlog
refinement commit, the code quality and design reflections themselves, and an
API reflection feedback entry. The previous day's work (close_view fix, terrain
detection, documentation consolidation) was substantive implementation. But
today has been almost entirely meta-work: reflecting on the code, reflecting on
the API, reflecting on the design, and now reflecting on the process of
reflecting.

There is a real question about whether the project has developed a reflection
habit that crowds out building. The backlog's top items -- remove phantom tools,
fix instructions string -- are described as "one-line fix with outsized impact
on reliability." If that is true, why has the project spent today writing
thousands of words about them instead of making the one-line fix?

The charitable interpretation is that today was deliberately a reflection day,
and the implementation days that follow will be sharper for it. The less
charitable interpretation is that the project has found a comfortable rhythm of
introspection that feels productive without requiring the harder work of
actually changing running code.

## What the project values, revealed vs. stated

The stated values in VISION.md are transparency, declarative design, and
parity between human and AI access to information. The CLAUDE.md independent
mode instructions value autonomy and throughput -- keep working, delegate
implementation, commit after every task.

The revealed values are different. The project most values *articulating
problems clearly*. The feedback entries are genuinely excellent -- specific,
grounded in session evidence, honest about what failed. The code quality
reflection identifies exact line numbers, counts duplicated code paths,
measures file sizes. The API reflection counts every tool and categorizes them.
This is careful, thorough analytical work.

What the project values less, in practice, is *resolving the problems it has
articulated*. The DSL alias question has been on the backlog for multiple days
and the design reflection today even says "this should be a 30-minute change,
not a recurring backlog item." The phantom tools in the instructions string are
acknowledged as a "straightforward bug." The dead functions in server.py have
been identified twice. These items recur in reflection after reflection because
they keep being analyzed rather than fixed.

There is also a tension around server.py's size. The code quality reflection
notes it has nearly doubled from 1,700 to 3,048 lines since the last review,
calls this "urgently needs splitting," and then the project... writes another
reflection about it. The file will presumably be at 3,500 lines by the next
reflection.

## The human-AI collaboration texture

Reading across the session feedback, the collaboration has a distinctive shape.
The human provides scientific direction and aesthetic judgment. The AI provides
tool selection, pipeline construction, and domain knowledge that is sometimes
surprisingly good (the VLS physics explanation that the user confirmed as
"exactly right"). The pipeline file is genuinely a shared artifact -- both
human and AI read and write it, and it serves as the authoritative record of
what is being visualized.

The roughest edges are around information asymmetry. The bonsai session
feedback notes that when the human edited the pipeline file, Claude did not
read it to understand what changed. The human had to ask "You can't see the
file?" This is a fundamental gap: the pipeline file is supposed to be the
shared artifact, but the MCP server does not notify Claude when the file
changes. The hot reload design (April 5 entry) would fix this, and it would
unify the human and AI editing experiences. But it remains in the "interesting
idea" category.

The CLAUDE.md independent mode infrastructure is elaborate -- detailed
instructions about orchestration, subagent delegation, clock-checking, WIP
commits. But the evidence from today's sessions suggests it may be
over-engineered relative to actual practice. The reflections and feedback
entries are being written by agents (or the human directing agents), not by
a fully autonomous orchestrator working through the backlog. The infrastructure
describes a workflow that seems more aspirational than actual.

## Epistemology: what counts as evidence

The project's evidence base is narrow in a specific way: almost all session
testing has been on the wildfire dataset. The bonsai session revealed genuinely
different problems (volume rendering workflow, scalar bar missing, isosurface +
volume composite pattern). The foot, CT head, and hydrogen atom datasets exist
but have no documented session feedback. The synthetic dataset is used for unit
tests but not for MCP session testing.

This means the project's "it works" confidence is really "it works for
structured grids with scalar fields where the primary workflow is isosurface
extraction and streamline tracing." Volume rendering, image data, and datasets
with different characteristics are much less exercised. The 514 unit tests
provide structural coverage, but the unit tests cannot catch the workflow-level
problems that session testing reveals (like the chicken-and-egg problem with
fresh views, or the vorticity threshold sweep inefficiency).

## Things to act on or think about

- **Impose a reflection:implementation ratio.** Something like: for every
  reflection entry written, at least two backlog items must be closed first.
  Today's six meta-entries to zero code changes is not a sustainable ratio.

- **Fix the known one-line bugs before the next reflection.** Phantom tools in
  the instructions string, dead functions in server.py, and the "legacy"
  label on compute_vorticity are all quick fixes that have been analyzed
  multiple times. Just do them.

- **Run a session on the foot or hydrogen atom dataset.** The project's
  evidence base is too concentrated on wildfire. A session on a different data
  type (ImageData, different field structure) would reveal whether the
  improvements are genuinely general or wildfire-specific.

- **Decide whether hot reload is next, or tool count reduction is next.**
  Both are well-analyzed. Both keep appearing in reflections. The design entry
  from today argues for start_session + tool reduction; the April 5 entry
  argues for hot reload as foundation. Pick one and build it instead of
  writing another entry about which to pick.

- **Audit the independent mode infrastructure against actual usage.** Is the
  orchestrator/subagent model described in CLAUDE.md actually being used as
  written? If not, simplify the instructions to match reality. Elaborate
  infrastructure for a workflow nobody follows is pure overhead.

- **The server.py split needs a deadline, not another description.** Every
  reflection notes the file is too large. Every reflection describes the same
  split strategy. Set a date. Do the split. Move on.
