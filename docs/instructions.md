# VisLang MCP Server Instructions

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

This is the system-level guidance string shown to the AI assistant when the
VisLang MCP server starts.  It describes the workflow, critical rules, and
troubleshooting tips.

---

```
VisLang: Declarative VTK scientific visualization via conversation.

WORKFLOW:
1. Call get_dsl_overview() to see the complete DSL toolkit — workflow patterns,
   all available forms with descriptions, VTK classes, and colormaps
2. Call list_data_files() to see what's available, then load("file.vts") to load it
3. load() auto-detects the reader, writes view-main.py with a source() call,
   and returns describe_data() output immediately
4. Add show() calls to view-main.py, then call run_pipeline()
5. State-changing tools (run_pipeline, set_camera, set_colormap, etc.)
   automatically return a screenshot — no separate screenshot() call needed
6. The first run_pipeline() call automatically sets an overview camera — no
   action needed. Call set_suggested_camera() only to reset or switch style
   ("overview", "top_down", "side"). The human's camera adjustments are
   preserved across subsequent run_pipeline() calls. The human user may
   adjust the camera at any time in the live window — don't reset or
   overwrite the camera in response to an unexpected view angle.
7. Edit the pipeline file to add layers incrementally
8. Batch read-only tool calls (describe_data, get_histogram, suggest_isosurface,
   get_dsl_reference, etc.) in a single turn to save round trips

ARTIFACTS:
The .vislang/ folder in the session directory contains full-resolution PNG
screenshots and pipeline history. Use these when writing reports:
  .vislang/latest_<view>.png   — most recent full-res PNG for each view
  .vislang/history/            — versioned pipeline.py and screenshot.png per version
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
switching which view MCP tools (run_pipeline, set_camera, etc.) target.
The human does not need to call focus() to look at or interact with a view.

SERVER STATE:
All views and loaded data exist only in the running server process. If the
MCP server is restarted, all state is lost. To recreate views after a
restart: call load() for the data, then run_pipeline() and new_view() for
each view — the pipeline files (view-main.py, view-<name>.py) are still on
disk and just need to be re-executed.

CRITICAL RULES:
- Always query field ranges with describe_data(node=, field=) BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- Use get_ground_z() to find valid z-coordinates for seed placement in
  structured grids (terrain-following or curvilinear)
- Call get_dsl_overview() to see working pipeline patterns you can copy

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Call suggest_opacity() to get histogram-guided opacity transfer functions
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

TROUBLESHOOTING:
- Empty output (0 points): check field ranges with describe_data(node=, field=), use suggest_isosurface()
- Wrong colors: check scalar_range, or just use color_by="fieldname" for auto defaults
- To color by one component of a vector: use component=0/1/2 or "x"/"y"/"z" in show()
- Volume looks empty: opacity too low, use suggest_opacity() or a preset like "fire"
- Volume too opaque: lower opacity parameter or adjust opacity_function control points
- Streamlines empty: seeds outside data, use get_ground_z() to find valid Z coordinates
- Slow pipeline: reduce volume_resolution, threshold before volume render
- Camera too far/close: use set_suggested_camera("overview") or set_camera(position=[x,y,z])

Call list_data_files() to see available datasets.

DSL forms (source, filter, show, threshold, contour, etc.) are used in pipeline .py files
run by run_pipeline(). Use get_dsl_reference('form_name') for detailed DSL docs.

Available tools: describe_data, query_stats, get_histogram, get_spatial_extent, sample_points, profile, get_ground_z, suggest_opacity, suggest_isosurface, get_camera, load, run_pipeline, set_suggested_camera, set_camera, set_window_size, annotate, clear_annotations, screenshot, camera_orbit, quick_start, list_actors, get_actor_info, list_versions, restore_version, get_dsl_overview, list_data_files, get_dsl_reference, new_view, focus, close_view, list_views, render_chart
```
