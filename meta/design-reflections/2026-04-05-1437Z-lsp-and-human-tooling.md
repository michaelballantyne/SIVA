# Design Reflection: April 5, 2026

## The insight from the bonsai session

The bonsai CT scan session exposed a fundamental asymmetry: Claude has rich
tools for understanding the data (get_statistics, suggest_isosurface,
suggest_opacity, describe_data) but the human editing the pipeline file has
none of this. When the human edits `view-main.py` in their text editor,
they're flying blind — no field name completion, no value range hints, no
feedback until they ask Claude to set the pipeline and take a screenshot.

The friction isn't just about MCP tool design. It's about who has access to
information. Claude can query field ranges before choosing a threshold; the
human has to guess, save, wait for a rebuild, and check the result. Claude
gets error messages in tool results; the human has to ask Claude what
happened. The human and the AI should have the same information, just
surfaced through different channels.

## The pipeline file as a live program

The declarative, stateless nature of the DSL makes something powerful
possible: the pipeline file can be treated as a live program, re-executed
on every save. This is the ShaderToy / SwiftUI preview model. The server
watches the file, rebuilds the pipeline, and updates the render window.
Errors appear immediately — as terminal output, as an overlay on the render,
or as editor diagnostics.

File-watching hot reload is the foundation. It eliminates the set_pipeline
tool call entirely for the human, and reduces it to just a Write for Claude.
But hot reload alone only gives you fast feedback on errors. The bigger
opportunity is giving the human the same data-aware intelligence that Claude
gets from the MCP tools.

## A language server for the pipeline DSL

An LSP for the pipeline DSL could provide:

- **Autocomplete** for DSL form names, parameter names, and — critically —
  field names from the loaded dataset. When you type `color_by="`, the LSP
  offers the available fields.
- **Hover info**: hover over a field name to see its range, type, and
  component count. Hover over a threshold value to see what fraction of
  points it selects.
- **Inline diagnostics**: "field 'Temperture' not found, did you mean
  'Temperature'?", "threshold range [500, 600] selects 0 points",
  "isosurface value 300 is above field maximum of 255".
- **Code actions**: "suggest isosurface value for this field", "suggest
  opacity function for volume rendering".

The key observation is that the backend queries already exist. get_statistics,
suggest_isosurface, suggest_opacity, describe_data — these are exactly the
queries an LSP would make. The MCP tools and the LSP would share the same
underlying query layer; they're just different delivery channels for the
same data-aware intelligence.

## One query interface, two frontends

This suggests an architecture where the core intelligence lives in a query
layer that can be accessed through:

1. **LSP protocol** — for the human in their editor (completions,
   diagnostics, hover)
2. **MCP tools** — for Claude in conversation (get_statistics,
   suggest_isosurface, etc.)

Or more ambitiously: a single LSP server that Claude accesses through an
LSP-to-MCP bridge. Claude's "tools" become LSP requests under the hood.
This would guarantee that human and AI always have exactly the same
capabilities — no feature gets added for one without being available to
the other.

This also opens up a third channel: **parameter scrubbing**. Click a numeric
literal in the editor, drag to change it, see the render update live. The
declarative DSL makes this feasible because you can re-execute the full
pipeline on every value change. The scrubbing UI needs the same range
information the LSP already has.

## What this means for current work

This is a longer-term direction, not an immediate priority. But it should
inform near-term decisions:

- **File-watching hot reload** is the foundation and is worth building now.
  It simplifies the MCP interaction (no set_pipeline), improves the human
  editing experience, and is a prerequisite for the LSP.
- **Keep the query layer clean and reusable.** The functions behind
  get_statistics, suggest_isosurface, etc. should be callable without going
  through MCP. If they're just Python functions that take a dataset and
  return structured data, they can serve both the MCP tools and a future LSP.
- **Don't over-invest in MCP-specific interaction patterns** that the LSP
  would replace. For example, the "server instructions tell Claude to read
  the pipeline file before setting it" workaround becomes unnecessary if
  the server watches the file.
- **The DSL's declarative, stateless design is a strategic asset.** It's
  what makes hot reload, scrubbing, and re-execution safe. Resist adding
  imperative state that would break these properties.
