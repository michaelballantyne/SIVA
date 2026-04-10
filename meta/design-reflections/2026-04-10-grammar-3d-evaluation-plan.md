# Grammar of 3D Visualization: Evaluation Plan

*Supplement — April 10, 2026*

How would we evaluate whether the grammar actually delivers on its
promises? This document outlines concrete experiments.

---

## Hypothesis

A GoG-inspired grammar for 3D scientific visualization will produce
measurable improvements over PyVista and VisLang's current DSL in:

1. **Composability** — more visualization patterns expressible with fewer
   language constructs
2. **Readability** — domain scientists understand grammar pipelines
   faster than PyVista/VTK-shaped code
3. **LLM writability** — an LLM produces correct grammar code with
   fewer errors and fewer iterations
4. **Edit efficiency** — common refinements (change colormap, adjust
   threshold, add a layer) require smaller edits
5. **Diffability** — version diffs are semantically meaningful

## Experiment 1: Expressiveness Coverage

**Method:** Take every visualization from VisLang's session history
and attempt to express it in the grammar.

**Source material:**
- Bonsai CT session (volume rendering, isosurface, multi-view)
- Wildfire simulation sessions (threshold, streamlines, terrain, multi-field)
- Any other sessions in `sessions/` or documented in feedback

**Metrics:**
- Can the grammar express the visualization? (coverage)
- How many lines of code? (conciseness)
- Are VTK escape hatches needed? (abstraction completeness)
- Does the grammar make the visualization's structure clearer? (structural clarity — subjective but important)

**Expected result:** The grammar should cover 90%+ of existing
visualizations without escape hatches. The remaining cases (very
specialized VTK operations) should be expressible via `vtk_filter()`.

## Experiment 2: LLM Comparative Trial

**Method:** Give an LLM the same visualization task and compare results
using (a) the current VisLang DSL, (b) the GoG grammar, (c) raw PyVista.

**Tasks:**
1. "Volume-render the bonsai CT scan with terrain colormap"
2. "Show the fire front as an isosurface and overlay streamlines"
3. "Create a three-panel comparison: volume, isosurface, and slice"
4. "Change the colormap from hot to inferno and make it more transparent"
5. "Show the same field from three camera angles"

**Metrics per task:**
- Number of LLM attempts before correct output
- Number of tool calls consumed
- Size of the generated code (tokens)
- Number of errors encountered and their types
- Quality of the final visualization (subjective, but rate 1-5)

**Control:** Use the same LLM model (Claude) with the same temperature
and system prompt for all three conditions. Vary only the DSL reference
documentation provided.

**Expected result:** The grammar should require fewer attempts and tool
calls, especially for tasks 3-5 (composition, modification, faceting)
which the current DSL handles poorly.

## Experiment 3: Edit Distance for Common Refinements

**Method:** Start from a working pipeline and measure the edit required
for common refinements.

**Refinements to test:**
1. Change the colormap
2. Adjust the scalar range
3. Make a layer more transparent
4. Add a bounding box outline
5. Change an isosurface threshold value
6. Add a second representation of the same data
7. Switch from surface to volume rendering
8. Add a color legend
9. Change the camera angle
10. Create a side-by-side comparison

**Metrics:**
- Lines changed / lines unchanged ratio
- Number of keyword arguments the edit touches
- Risk of accidentally breaking something (does the edit touch
  unrelated parameters?)

**Expected result:** Grammar edits should be more localized (change one
line, not rewrite a multi-line function call). Encoding separation should
mean that visual property changes never touch the data pipeline.

## Experiment 4: Human Readability

**Method:** Show domain scientists (not visualization experts) pipeline
code and ask them to answer questions:

1. "What data is being visualized?"
2. "What color scheme is used?"
3. "What threshold range is being applied?"
4. "How many visual layers are in this scene?"
5. "What would you change to see a different temperature range?"

**Conditions:**
- (a) Current VisLang DSL pipeline
- (b) GoG grammar pipeline
- (c) PyVista script

**Metrics:**
- Time to answer each question
- Accuracy of answers
- Confidence rating (1-5)

**Expected result:** The grammar should produce faster and more accurate
answers for structural questions (Q4, Q5). For factual extraction (Q1-Q3),
differences may be smaller.

## Experiment 5: Version Control Diff Quality

**Method:** Create a series of pipeline versions (v1 through v5) with
progressive refinements. Generate diffs between consecutive versions.
Rate the diffs for semantic clarity.

**Rating criteria:**
- Can you understand what changed from the diff alone? (1-5)
- Does the diff contain irrelevant noise? (1-5, inverted)
- Could you revert a specific change based on the diff? (yes/no)

**Expected result:** Grammar diffs should be clearer because encoding
objects change independently of data pipelines. Current DSL diffs show
changes to keyword arguments in multi-line show() calls, mixing visual
and structural changes.

## Experiment 6: Error Diagnostic Quality

**Method:** Intentionally introduce errors into pipelines and evaluate
the error messages produced.

**Error types:**
1. Misspelled field name
2. Value outside field range
3. Wrong representation for data type (volume on polydata)
4. Missing required parameter
5. Incompatible encoding for representation

**Metrics:**
- Does the error message identify the problem? (yes/no)
- Does it suggest a fix? (yes/no)
- Could an LLM self-correct from this message alone? (yes/no)

**Expected result:** Grammar-level validation should produce better
diagnostics because the spec is structured enough to check before
execution. VTK-level errors (which PyVista and current VisLang sometimes
produce) are not actionable.

---

## Priority Order

If time is limited, run experiments in this order:
1. **Experiment 1** (Expressiveness Coverage) — validates the grammar
   can actually express what we need
2. **Experiment 3** (Edit Distance) — quantitative and easy to measure
3. **Experiment 2** (LLM Comparative Trial) — the most impactful for
   VisLang's mission
4. **Experiment 5** (Diff Quality) — quick to evaluate
5. **Experiment 6** (Error Diagnostics) — depends on compiler maturity
6. **Experiment 4** (Human Readability) — requires human subjects,
   harder to run

Experiments 1 and 3 can be run as soon as the prototype grammar
compiles. Experiment 2 requires MCP integration. Experiment 4 requires
recruiting domain scientists.
