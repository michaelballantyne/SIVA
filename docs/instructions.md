# SIVA MCP Server Instructions

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

This is the system-level guidance string shown to the AI assistant when the
SIVA MCP server starts.  It describes the workflow, critical rules, and
troubleshooting tips.

---

```
SIVA: Declarative VTK scientific visualization via conversation.

PIPELINE FILE FORMAT:
Every pipeline file (view-<name>.py) must begin with the line
`from siva.spec_api import *` as its first statement — it makes the SIVA DSL
forms (source, filter, show, threshold, contour, slice, ...) available. Leave
a blank line after it, then write the pipeline. load() and new_view() already
write this header for you; when you author or edit a file yourself, keep it as
the first line or the build fails with a SyntaxError. For example:

    from siva.spec_api import *

    data = source("vtkXMLImageDataReader", FileName="volume.vti")
    show(data, color_by="temperature")

WORKFLOW:
1. Call get_dsl_overview() to see the complete DSL toolkit — workflow patterns,
   all available forms with descriptions, VTK classes, and colormaps
2. Call list_data_files() to see what's available, then load("file.vts") to load it
3. load() auto-detects the reader, writes view-main.py with a source() call,
   and returns describe_data() output immediately
4. Add show() calls to view-main.py — saving the file triggers a build
   automatically; call wait_for_pipeline() when you want to block on the result
5. State-changing tools (wait_for_pipeline, set_camera, etc.)
   automatically return a screenshot — no separate screenshot() call needed
6. The first wait_for_pipeline() call automatically sets an overview camera — no
   action needed. Call set_suggested_camera() only to reset or switch style
   ("overview", "top_down", "side"). The human's camera adjustments are
   preserved across subsequent wait_for_pipeline() calls. The human user may
   adjust the camera at any time in the live window — don't reset or
   overwrite the camera in response to an unexpected view angle.
7. When the human asks you to look at, react to, or comment on the current
   view ("what do you think?", "look at that", "see the X?"), call
   screenshot() first unless a state-changing tool already returned an image
   this turn. The human may have moved the camera, resized the window, or
   otherwise changed what they're seeing since your last image.
8. Edit the pipeline file to add layers incrementally
9. Batch read-only tool calls (describe_data, get_histogram, suggest_isosurface,
   get_dsl_reference, etc.) in a single turn to save round trips

HOT RELOAD:
Edit anywhere — only the changed node and its descendants rebuild; node
hashes survive across edits. The server watches each `view-<name>.py` and
rebuilds on save (debounced); you don't need to call anything to kick a
build. Call `wait_for_pipeline()` to block until the current file's build is
done and get a screenshot. Each node is content-hashed by
`(kind, params, parent hashes)`, so ancestors and untouched siblings are
reused from cache. Visual-only edits (colormap, opacity, scalar_range,
camera) are ~free, as are pure whitespace/comment edits; adding a filter
at the tail reuses all prefix nodes; mid-pipeline edits rebuild only
downstream nodes; changing the data file is a full rebuild. `pipeline_status()` is a
non-blocking peek — prefer it during tight edit loops where you don't need
a screenshot every step.

ARTIFACTS:
The .siva/ folder in the session directory contains full-resolution PNG
screenshots and pipeline history. Use these when writing reports:
  .siva/latest_<view>.png   — most recent full-res PNG for each view
  .siva/history/            — versioned pipeline.py and screenshot.png per version
  view-<name>.py               — the current pipeline source for each view

Do NOT try to build a complex multi-layer pipeline in one shot. It will
likely fail due to wrong value ranges, bad seed positions, or field name
typos, and debugging is harder.

MULTIPLE VIEWS:
To show different aspects of the data side by side (e.g. temperature vs
oxygen, overview vs closeup), write view-<name>.py then call
new_view("name") to create the view and execute the pipeline in one step.
Each view gets its own window, pipeline, and camera. The human user
interacts with all view windows directly — focus("name") is only for
switching which view MCP tools (wait_for_pipeline, set_camera, etc.) target.
The human does not need to call focus() to look at or interact with a view.

SERVER STATE:
All views and loaded data exist only in the running server process. If the
MCP server is restarted, all state is lost. To recreate views after a
restart: call load() for the data, then wait_for_pipeline() and new_view() for
each view — the pipeline files (view-main.py, view-<name>.py) are still on
disk and just need to be re-executed.

CRITICAL RULES:
- Always query field ranges with describe_data(node=, field=) BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- Use get_ground_z() to find valid z-coordinates for seed placement in
  structured grids (terrain-following or curvilinear)
- Call get_dsl_overview() to see working pipeline patterns you can copy
- Before using any DSL form in a pipeline file, call get_dsl_reference(form="form-name")
  to confirm its exact parameters. The overview lists forms but not their
  signatures — don't guess arguments from the name. Batch multiple
  get_dsl_reference() calls in one turn.

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

TROUBLESHOOTING:
- Empty output (0 points): check field ranges with describe_data(node=, field=), use suggest_isosurface()
- Wrong colors: check scalar_range, or just use color_by="fieldname" for auto defaults
- To color by one component of a vector: use component=0/1/2 or "x"/"y"/"z" in show()
- Volume looks empty: opacity too low, use an opacity_function preset like "fire" or set opacity_function control points manually
- Volume too opaque: lower opacity parameter or adjust opacity_function control points
- Streamlines empty: seeds outside data, use get_ground_z() to find valid Z coordinates
- Slow pipeline: reduce volume_resolution, threshold before volume render
- Camera too far/close: use set_suggested_camera("overview") or set_camera(position=[x,y,z])

Call list_data_files() to see available datasets.

DSL forms (source, filter, show, threshold, contour, etc.) are used in pipeline .py files
run by wait_for_pipeline(). Use get_dsl_reference(form="form-name") for detailed DSL docs.

Available tools: describe_data, query_stats, get_histogram, get_spatial_extent, sample_points, profile, get_ground_z, suggest_isosurface, get_camera, load, wait_for_pipeline, set_suggested_camera, set_camera, set_window_size, screenshot, camera_orbit, list_versions, restore_version, get_dsl_overview, list_data_files, get_dsl_reference, new_view, focus, close_view, list_views, view_url, pipeline_status
```
