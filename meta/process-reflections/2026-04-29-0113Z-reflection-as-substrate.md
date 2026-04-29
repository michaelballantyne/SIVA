# Reflection as substrate: what the project does well, and what it's
# stopped doing

A process reflection on VisLang, April 29, 2026.

## The first thing to notice

Three weeks ago, the April 6 process reflection complained that the
project was writing thousands of words about one-line fixes instead of
making the one-line fix. "Today's six meta-entries to zero code changes
is not a sustainable ratio." The recommendation was to impose a
reflection-to-implementation ratio.

That recommendation was not adopted, and it was right not to be. Look
at what shipped between April 25 and April 29: cascade-skip contract
(`5cabe99`), wrapper validation through structured statuses (`9b5f5de`),
property-typo detection (`43c3562`), inline empty-output hints
(`6d8709d`), unified per-node status schema (`93c1315`), terse build
reports (`03a4729`), Vega-Lite-style display inference (`94bdcdc`),
hot-reload simplification (`1b02c82`), gamma-style determinacy tests
(`cdde51d`), curl/Vorticity cleanup (`fcf8f9b`), `extract_grid` ergonomics
(`3be5ccb`), `scene_preset` → `background` merge (`0301331`), color
function presets, BoolMacro guidance — plus 18 design and feedback
entries. About 60 commits in five days, almost all substantive code,
many of them executing on plans laid out in the very reflections that
preceded them.

The April 6 entry diagnosed the symptom (six reflections, zero code) but
got the disease wrong. The reflections weren't displacing implementation
— they were *the substrate* implementation runs on. Nearly every
high-priority backlog item that shipped traces to a feedback or design
entry written 1–7 days earlier. The "diagnostic spine" cluster
(cascade-skip → wrapper validation → property-typo → field-range
hints → unified schema) is one such trace: five commits, one shared
design articulated in `2026-04-25-unified-error-reporting.md` and
`2026-04-25-cascade-leak-downstream-builders.md`, then sequenced through.
This is what the reflective methodology looks like when it works.

## So what's actually working?

**The feedback-to-backlog-to-commit pipeline is real.**
`2026-04-25-cascade-leak-downstream-builders.md` was filed; it became
backlog item with implementation notes; it shipped as `5cabe99` with
15 new tests; the backlog entry was marked `[x]` with an exact account
of what was done. Same arc for property-typo, terse mode, vega-lite
inference, hot reload. The cycle is fast (often <72 hours from feedback
to ship), and the artifacts at each stage are readable enough that a
session resuming three days later can pick up cleanly.

**The CLAUDE.md "orchestrator delegates implementation" model is being
followed.** The git log shows tight, focused commits with consistent
message style and detailed `[x]` annotations in the backlog —
characteristic of subagent runs where the orchestrator writes a brief
and reviews `git diff --stat`. Several commits explicitly note "Full
suite: 707 passed" / "768 passed" / "743 passed", which is what you
get when a subagent runs tests as part of its own contract.

**Design reflections name structural tensions before they bite.** The
`2026-04-26-hot-reload-threading.md` entry identified that
`BuildRecord` was being asked to be both "an event" and "a cache entry";
that single sentence reframed three apparently-independent races as one
bug. Commit `1b02c82` ("simplify hot reload") is the result. This is
the most valuable thing reflection produces and you cannot get there by
writing more code faster.

## What's not working, or is starting to drift

**The single-dataset problem is no longer single, but it's still narrow
in a different way.** April 4's reflection complained that everything
was wildfire. Now there are eight datasets in `datasets/` and recent
sessions touch headsq (CT volume), bonsai, and a "rod" mesh. So that
specific concern resolved. But the *kind* of pressure these datasets
apply is similar — they're all single-timestep, single-file, structured
or image data viewed by isosurface/volume/threshold workflows. There
is still no time-varying data, no unstructured polydata of meaningful
complexity, no multi-block, no AMR. The April 4 worry that "design
choices that seem general may be specific to the data types tested so
far" has narrowed in scope but not gone away. It's been quietly
reclassified from "we test on one dataset" to "we test on N datasets
that all cover the same workflow shape."

**The backlog has a long tail that doesn't move.** "Split server.py"
has been on the backlog since at least April 6 (three reflections ago)
and is still there at 2,294 lines. "VISION.md refresh" is on the
backlog acknowledging the current Part 1 lies about tool counts
(claims ~35, reality 25). "Lazy view creation" has detailed
implementation notes from a session 2 days ago and is not yet started.
"LSP for the pipeline DSL" — the centerpiece of VISION.md Part 3 —
has been on the backlog since the project's first week and has never
been touched. The high-priority tier moves; the medium tier accumulates;
the long tail mineralizes. It would be honest to either delete the
mineralized items or promote one and ship it, instead of letting them
function as aspirational decoration.

**Reflection has a feature-creep problem of its own.** I count three
kinds of reflection happening here: feedback (session-grounded
observation), design reflection (code/API critique), and process
reflection (this document). Plus backlog refinement. Plus commit
messages, which are now substantial paragraphs. Plus the `Status /
Remaining` section CLAUDE.md asks for on WIP commits. Each of these
serves a distinct purpose, but the boundaries blur. The
`2026-04-27-readable-editable-specs.md` feedback entry is 344 lines
and is doing the work of a design proposal; the
`2026-04-26-contracts-and-build-report.md` entry is essentially a paper
draft. These are good documents, but they are no longer what
`meta/feedback/` was originally for (session observations). The
genre is melting.

**The values revealed by recent commits are slightly different from
the values stated in VISION.md.** VISION.md Part 4 names four
falsifiable research claims (declarative DSL vs raw VTK; whether
data-aware queries reduce iteration; whether humans co-author; cross-
dataset generality). None of those have been measured. What has
demonstrably been worked on is the *agent-computer interface* —
diagnostics, terse vs verbose modes, structured status schema,
property-typo detection, BoolMacro auto-translation. The project is
quietly becoming an ACI-design project rather than a research project
about declarative DSLs. That may be the right thing — SWE-agent style
ACI work is genuinely novel and the diagnostic-spine work is paying
off — but the gap between the stated research claims and the actual
work-product is widening. VISION.md Part 4 should probably be rewritten
to reflect what the project actually has evidence for, not what it
hoped to evaluate.

**The "human and AI share an intelligence layer via LSP+MCP" claim is
more aspirational than ever.** This is the central conceptual move in
VISION.md (§The central principle), and the LSP side has not advanced
at all. Every query/intelligence improvement made in the last month
landed in MCP; none landed in the editor. So in practice, the
"shared intelligence" claim is currently "intelligence for the agent,
the human still flies blind." The hot-reload + status-file work is the
closest thing to closing the gap (the human can at least *see* a build
report in a split pane), but autocomplete, hover info, inline
diagnostics, and the node info view all remain vapor. If LSP is real,
it needs to become a backlog item with a deadline; if it's not, the
"two consumers, one intelligence" claim should be downgraded to "one
consumer well-served, the other listed as future work."

## On the texture of human-AI collaboration in this codebase

Reading recent feedback (especially `2026-04-27-parallax-and-being-lost.md`
and `2026-04-27-readable-editable-specs.md`) something striking
emerges: the agent is now *the field researcher*. These entries are
not feedback from a human watching an agent. They are field notes by
an agent reflecting on its own session experience —
"I confirmed I don't reliably notice when I'm lost," "I would have
proceeded to take parallax samples *of the fog* if the user hadn't
asked." This is genuinely valuable: agents can articulate failure
modes that a human observer might miss, because the human can't see
the agent's internal state. It's also a slight epistemic concern —
the project's evidence base is now substantially the agent talking
about itself, which has known reliability issues. The parallax entry
is exemplary because it reports specific, testable observations
(±5° is right, ±1.5° is below noise, ±8° feels sequential). The
readable-specs entry is more speculative and reads more like a paper
draft than a session log.

The CLAUDE.md instructions assume a clean orchestrator/subagent
split. In practice the orchestrator does more analytical reading than
"~5–6 tool calls per task" budgets for, especially when picking the
next backlog item or evaluating a subagent's output. This isn't
necessarily wrong — the tight budget is for routine implementation
tasks; reflection days legitimately spend more on reading. But the
discipline of "if you're spending 20+ tool calls, you're doing too
much yourself" should probably be reframed as "20+ tool calls is fine
for design/triage turns but a smell during implementation turns."

## Things to act on or think about

- **Pick one mineralized backlog item per week and either ship it or
  delete it.** Server.py split, VISION.md refresh, lazy view creation,
  LSP scaffolding — pick one, set a date, retire the rest from the
  active backlog and move them to a "deferred / out of scope" section.
  Persistent unfinished items dilute the signal of what the backlog is
  for.

- **Rewrite VISION.md Part 4 to match the work that's actually being
  done.** The four falsifiable claims are honest but unmeasured. Either
  commit to an evaluation (SciVisAgentBench is right there as a
  benchmark) or rewrite the framing around ACI design as the
  contribution. Right now the document promises a research paper the
  project isn't writing.

- **Decide whether LSP is real or aspirational.** If it's real, file
  a concrete first step (a thin LSP that just supplies field-name
  autocomplete from `describe_data` for a single view, say) and
  schedule it. If it's aspirational, downgrade the "shared intelligence
  for human and AI" claim throughout VISION.md to match.

- **Add a non-trivial dataset shape.** Time-varying simulation,
  unstructured mesh, or multi-block. Eight static structured datasets
  apply the same kind of pressure eight times. The backlog has hinted
  at multi-timestep work for a month; making it concrete would be the
  first real test of cross-shape generality.

- **Tighten the reflection genre boundaries.** `meta/feedback/` for
  session-grounded observation; `meta/design-reflections/` for
  code/API critique; `meta/process-reflections/` for workflow
  reflection. Long design proposals (the readable-specs entry, the
  contracts entry) probably belong in a fourth bucket — `meta/proposals/`
  or similar — so they aren't competing with session feedback for the
  same shelf. This is mostly editorial but the current melting
  obscures what kind of evidence each document is.

- **Ask whether agents reflecting on their own sessions counts as
  evidence.** The parallax-views and readable-specs entries are
  rich, but they are agents talking about agents. A skeptic would
  note that the project's claim to know how its tools feel is grounded
  in the same model that uses the tools. At minimum, log when feedback
  comes from an agent vs. a human, and weight accordingly. At maximum,
  do a session with a human as primary driver and see whether the
  pain points align.

- **Notice that this reflection is itself an artifact of the substrate
  it describes.** I'm an agent writing about agents writing about
  agents. The recursion is fine — it produces useful structure — but
  it's worth periodically running a reflection that *isn't* a reflection
  agent: have the human write one, or have an external reviewer write
  one. The reflective layer can confirm itself indefinitely.
