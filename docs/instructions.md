# VisLang MCP Server Instructions

> Auto-generated from source by `python gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

This is the system-level guidance string shown to the AI assistant when the
VisLang MCP server starts.  It describes the workflow, critical rules, and
troubleshooting tips.

---

```
VisLang: Declarative VTK scientific visualization via conversation.

WORKFLOW:
1. Call list_data_files() to see what's available, then load("file.vts") to load it
2. load() auto-detects the reader and returns describe_data() output immediately
3. Write pipeline code to pipeline.py, then call set_pipeline()
3. State-changing tools (set_pipeline, set_camera, set_colormap, etc.)
   automatically return a screenshot — no separate screenshot() call needed
4. Edit the pipeline file to add layers incrementally
5. Use get_pipeline() to see current code if needed

Do NOT try to build a complex multi-layer pipeline in one shot. It will
likely fail due to wrong value ranges, bad seed positions, or field name
typos, and debugging is harder.

MULTIPLE VIEWS:
To show different aspects of the data side by side (e.g. temperature vs
oxygen, overview vs closeup), use new_view("name") to create additional
views. Each view gets its own window, pipeline, and camera. Use
focus("name") to switch which view you're editing.

CRITICAL RULES:
- Always query field ranges with get_statistics() BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- Use get_ground_z() to find valid z-coordinates for seed placement in
  structured grids (terrain-following or curvilinear)
- Call get_examples() to see working pipeline patterns you can copy

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Call suggest_opacity() to get histogram-guided opacity transfer functions
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

TROUBLESHOOTING:
- Empty output (0 points): check field ranges with get_statistics(), use suggest_isosurface()
- Wrong colors: check scalar_range, or just use color_by="fieldname" for auto defaults
- To color by one component of a vector: use component=0/1/2 or "x"/"y"/"z" in show()
- Volume looks empty: opacity too low, use suggest_opacity() or a preset like "fire"
- Volume too opaque: lower opacity parameter or adjust opacity_function control points
- Streamlines empty: seeds outside data, use seeds_near() or check get_ground_z()
- Slow pipeline: reduce volume_resolution, threshold before volume render
- Camera too far/close: use suggest_camera("overview") or set_camera(position=[x,y,z])

Call list_data_files() to see available datasets.

DSL forms (source, filter, show, threshold, contour, etc.) are used in pipeline .py files
run by set_pipeline(). Use get_dsl_reference('form_name') for detailed DSL docs.

Available tools: describe_data, get_array_info, get_field_summary, get_node_info, get_bounds, get_statistics, query_stats, get_histogram, get_spatial_extent, sample_points, profile, get_ground_z, suggest_scalar_range, suggest_opacity, suggest_isosurface, suggest_camera, get_camera, load, set_pipeline, reset_pipeline, set_camera, set_opacity, set_colormap, set_background, set_window_size, toggle_visibility, make_vector, curl, annotate, clear_annotations, screenshot, camera_orbit, quick_start, list_actors, get_actor_info, list_versions, get_pipeline, restore_version, export_standalone, list_capabilities, list_data_files, get_examples, get_dsl_reference, new_view, focus, close_view, list_views, render_chart
```
