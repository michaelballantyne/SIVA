# Roadmap

Where VisLang is heading, so today's choices stay consistent with it.

## Query DSL with predicate pushdown (the main thrust)
The next layer is a **query DSL**: natural language → a typed query IR over the
`DatasetInfo` schema → predicate pushdown into the actual reads (hyperslab
selections, partition/column pruning) so only the needed bytes are touched. The
current `dimensions={...}` selection in `load` is the seed of this; the universal
`Selection` in `adapters.py` already pushes selections into reads where the
library allows.

## Where the AST / compile-time verification idea belongs
The "parse to an AST and verify at compile time" idea is for the **query DSL**,
NOT for file parsing. Parsing is considered solved enough via the trust ladder
(trusted libraries already read production formats; see
`vislang://instructions/adapters`). The AST's real value is statically checking
a *query* against the schema before any data is read — typed fields, valid
predicates, satisfiable selections.

## Rendering
- Port the particle/point path to a headless k3d renderer too (today it still
  uses the trame `render_server`, which is blank on GL-less nodes).
- Optionally restore live, camera-preserving updates on top of the k3d snapshot
  approach (snapshots currently rebuild the whole page per render).

## Guiding constraints (unchanged)
Keep the soundness gate (`vislang://instructions/soundness`) in front of every
new LLM use, and keep `DatasetInfo` as the format boundary so new formats and
new query features compose without touching each other.
