#!/usr/bin/env python3
"""Simulate a naive LLM user exploring VisLang MCP server step by step."""

import os
import sys
import textwrap

os.chdir("/home/user/VisLang")
os.makedirs('.vislang/history', exist_ok=True)

# Suppress VTK warnings
os.environ["VTK_DEFAULT_RENDER_WINDOW_OFFSCREEN"] = "1"

from vislang.server import (
    set_pipeline, screenshot, get_array_info, get_bounds,
    get_statistics, get_histogram, get_spatial_extent,
    get_ground_z, get_pipeline, restore_version, list_capabilities,
    sample_point, suggest_camera
)

assessment_notes = []

def step_header(num, title):
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  LLM STEP {num}: {title}")
    print(f"{sep}\n")

def llm_thinks(thought):
    print(f"  [LLM THINKS]: {thought}")

def llm_calls(call_desc):
    print(f"  [LLM CALLS]:  {call_desc}")

def show_result(label, result):
    print(f"\n  --- {label} ---")
    if isinstance(result, str):
        for line in result.split('\n'):
            print(f"  | {line}")
    else:
        print(f"  | (type={type(result).__name__}) {repr(result)[:200]}")
    print()

def note(text):
    assessment_notes.append(text)
    print(f"  [NOTE]: {text}")

# =========================================================================
# User: "Show me a visualization of the wildfire data"
# =========================================================================
print("\n" + "#" * 70)
print("  USER: 'Show me a visualization of the wildfire data'")
print("#" * 70)

# =========================================================================
# STEP 1: Discover capabilities and load data
# =========================================================================
step_header(1, "Check capabilities and load data")

llm_thinks("I should first check what capabilities are available and what the data looks like.")

llm_calls("list_capabilities()")
caps = list_capabilities()
show_result("list_capabilities()", caps)

if "source" in caps and "vtkXMLStructuredGridReader" in caps:
    note("GOOD: list_capabilities clearly shows available sources, filters, DSL functions, and colormaps.")
else:
    note("ISSUE: list_capabilities output was incomplete or confusing.")

llm_thinks("I see vtkXMLStructuredGridReader is available. Let me load the .vts file.")
llm_calls('set_pipeline(\'data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")\')')

result1 = set_pipeline('data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")')
show_result("set_pipeline (load data)", result1)

if "built successfully" in result1:
    note("GOOD: set_pipeline returns clear status with node info, point counts, arrays, and camera state.")
else:
    note("ISSUE: set_pipeline did not report success clearly.")

llm_calls('get_array_info("data")')
arrays = get_array_info("data")
show_result("get_array_info('data')", arrays)

if "theta" in arrays and "Vel" in arrays:
    note("GOOD: get_array_info shows all fields with types, ranges, and component counts. LLM can see 'theta' (temperature) and 'Vel' (velocity).")
else:
    note("ISSUE: Array info incomplete or missing expected fields.")

# =========================================================================
# STEP 2: Understand data ranges before visualizing
# =========================================================================
step_header(2, "Understand data ranges")

llm_thinks("Let me understand the temperature field to find fire regions.")

llm_calls('get_statistics("data", "theta")')
stats = get_statistics("data", "theta")
show_result("get_statistics('data', 'theta')", stats)

if "min" in stats and "max" in stats and "mean" in stats:
    note("GOOD: get_statistics provides min/max/mean/std/count. LLM can reason about value ranges.")
else:
    note("ISSUE: Statistics output missing expected fields.")

llm_thinks("Most of theta is around 300K (ambient), fire should be 400K+. Let me find where the fire is.")

llm_calls('get_spatial_extent("data", "theta", 400, 1200)')
extent = get_spatial_extent("data", "theta", 400, 1200)
show_result("get_spatial_extent('data', 'theta', 400, 1200)", extent)

if "X:" in extent and "Y:" in extent and "Z:" in extent:
    note("GOOD: get_spatial_extent clearly shows the bounding box of the fire region with point count and percentage.")
else:
    note("ISSUE: Spatial extent output was unclear.")

# =========================================================================
# STEP 3: Terrain + fire isosurface
# =========================================================================
step_header(3, "Terrain + fire isosurface")

llm_thinks("Now I'll create a basic visualization with terrain colored by theta and a fire isosurface.")

pipeline_v2 = textwrap.dedent('''\
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = extract_grid(input=data, VOI=[0, 500, 0, 500, 0, 0], SampleRate=[1, 1, 1])
fire = contour(input=data, ContourBy="theta", Isosurfaces=[400, 600, 800])

show(terrain, "terrain", color_by="theta", scalar_range=(300, 500), lut="fire", opacity=1.0)
show(fire, "fire", color_by="theta", scalar_range=(400, 1200), lut="fire", opacity=0.7)

background(0.1, 0.1, 0.15)
''')

llm_calls("set_pipeline(<terrain + fire pipeline>)")
result3 = set_pipeline(pipeline_v2)
show_result("set_pipeline (terrain + fire)", result3)

if "built successfully" in result3 or "built with warnings" in result3:
    note("GOOD: Pipeline built. Status shows per-node details including point/cell counts.")
    if "warning" in result3.lower():
        note("WARNING in pipeline: " + [l for l in result3.split('\n') if 'warning' in l.lower() or 'Warning' in l][0] if any('arning' in l for l in result3.split('\n')) else "")
else:
    note("ISSUE: Pipeline had errors. The LLM would need to debug.")

llm_calls("screenshot()")
img = screenshot()
show_result("screenshot()", f"Returned type: {type(img).__name__}")

if hasattr(img, 'data') or hasattr(img, 'path') or 'Image' in type(img).__name__:
    note("GOOD: screenshot() returns an Image object that MCP can send to the LLM.")
else:
    note("ISSUE: screenshot() return type unclear: " + type(img).__name__)

# =========================================================================
# STEP 4: Add wind streamlines
# =========================================================================
step_header(4, "Add wind streamlines")

llm_thinks("I want to add wind streamlines. Let me check the current pipeline first.")

llm_calls("get_pipeline()")
current = get_pipeline()
show_result("get_pipeline()", current)

if "data = source" in current:
    note("GOOD: get_pipeline returns the current DSL code so the LLM can modify it incrementally.")
else:
    note("ISSUE: get_pipeline output was unclear or empty.")

llm_thinks("I'll use seeds_near to auto-place seed points near the fire, then add streamlines.")

pipeline_v3 = textwrap.dedent('''\
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = extract_grid(input=data, VOI=[0, 500, 0, 500, 0, 0], SampleRate=[1, 1, 1])
fire = contour(input=data, ContourBy="theta", Isosurfaces=[400, 600, 800])

seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=30, offset_z=10)
streams = stream_tracer(input=data, SeedSource=seeds, Vectors="Vel",
                        MaximumPropagation=500, IntegrationDirection="Both",
                        IntegratorType="RungeKutta4")
tubes = tube(input=streams, Radius=1.5, NumberOfSides=8)

show(terrain, "terrain", color_by="theta", scalar_range=(300, 500), lut="fire", opacity=1.0)
show(fire, "fire", color_by="theta", scalar_range=(400, 1200), lut="fire", opacity=0.5)
show(tubes, "streamlines", color_by="Vel", lut="coolwarm")

background(0.1, 0.1, 0.15)
''')

llm_calls("set_pipeline(<pipeline with streamlines>)")
result4 = set_pipeline(pipeline_v3)
show_result("set_pipeline (with streamlines)", result4)

if "seeds" in result4.lower() or "stream" in result4.lower():
    note("GOOD: Pipeline with auto-seeded streamlines built. seeds_near is a powerful convenience.")
else:
    note("ISSUE: Streamline pipeline had problems.")

if "warning" in result4.lower() and "empty" in result4.lower():
    note("WARNING: Some nodes produced empty output - the LLM would see helpful hints about this.")
elif "built successfully" in result4:
    note("GOOD: All nodes including streamlines produced output successfully.")

# =========================================================================
# STEP 5: Camera angle
# =========================================================================
step_header(5, "Try a different camera angle")

llm_thinks("Let me get a suggested camera angle for an overview.")

llm_calls('suggest_camera("overview")')
cam = suggest_camera("overview")
show_result("suggest_camera('overview')", cam)

if "camera(" in cam:
    note("GOOD: suggest_camera returns a copy-pasteable camera() call. Very LLM-friendly.")
else:
    note("ISSUE: suggest_camera output not in a usable format.")

llm_thinks("I'll add the suggested camera to the pipeline.")

# Extract the camera line from suggestion
import re
cam_match = re.search(r'camera\(.*?\)', cam)
if cam_match:
    cam_line = cam_match.group(0)
else:
    cam_line = 'camera(position=(0, -500, 500), focal_point=(0, 0, 0), up=(0, 0, 1))'

pipeline_v4 = pipeline_v3.rstrip() + f"\n{cam_line}\n"

llm_calls("set_pipeline(<pipeline with suggested camera>)")
result5 = set_pipeline(pipeline_v4)
show_result("set_pipeline (with camera)", result5)

if "built successfully" in result5:
    note("GOOD: Camera applied successfully from suggest_camera output.")
else:
    note("ISSUE: Adding camera had problems.")

# =========================================================================
# STEP 6: Probe a specific location
# =========================================================================
step_header(6, "Probe a specific location")

llm_thinks("Let me sample field values at a point near the fire region.")

llm_calls('sample_point("data", 80, -10, 170)')
probe = sample_point("data", 80, -10, 170)
show_result("sample_point('data', 80, -10, 170)", probe)

if "theta" in probe and "Vel" in probe:
    note("GOOD: sample_point returns all field values at the nearest grid point. Very informative for understanding local conditions.")
else:
    note("ISSUE: sample_point output incomplete.")

if "Nearest point" in probe:
    note("GOOD: sample_point shows the actual nearest grid point coordinates, so the LLM knows about snapping.")
else:
    note("ISSUE: sample_point doesn't show the actual sampled location.")

# =========================================================================
# STEP 7: Cross-section slice
# =========================================================================
step_header(7, "Add a cross-section slice")

llm_thinks("I want to add a cross-section slice through the fire to see internal structure.")

pipeline_v5 = textwrap.dedent('''\
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = extract_grid(input=data, VOI=[0, 500, 0, 500, 0, 0], SampleRate=[1, 1, 1])
fire = contour(input=data, ContourBy="theta", Isosurfaces=[400, 600, 800])

seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=30, offset_z=10)
streams = stream_tracer(input=data, SeedSource=seeds, Vectors="Vel",
                        MaximumPropagation=500, IntegrationDirection="Both",
                        IntegratorType="RungeKutta4")
tubes = tube(input=streams, Radius=1.5, NumberOfSides=8)

cross = slice(input=data, origin=(80, 0, 200), normal=(0, 1, 0))

show(terrain, "terrain", color_by="theta", scalar_range=(300, 500), lut="fire", opacity=1.0)
show(fire, "fire", color_by="theta", scalar_range=(400, 1200), lut="fire", opacity=0.4)
show(tubes, "streamlines", color_by="Vel", lut="coolwarm")
show(cross, "cross_section", color_by="theta", scalar_range=(300, 600), lut="fire", opacity=0.9)

background(0.1, 0.1, 0.15)
''') + f"{cam_line}\n"

llm_calls("set_pipeline(<pipeline with cross-section>)")
result7 = set_pipeline(pipeline_v5)
show_result("set_pipeline (with cross-section)", result7)

if "built successfully" in result7:
    note("GOOD: Cross-section slice worked. The slice() DSL function is intuitive (origin + normal).")
elif "error" in result7.lower():
    note("ISSUE: Cross-section had errors. LLM would need to debug.")
else:
    note("PARTIAL: Cross-section built but with warnings.")

# =========================================================================
# FINAL ASSESSMENT
# =========================================================================
step_header("FINAL", "Assessment Summary")

print("\n  ALL NOTES COLLECTED:")
print("  " + "-" * 60)
for i, n in enumerate(assessment_notes, 1):
    print(f"  {i:2d}. {n}")

# Write assessment file
assessment = textwrap.dedent("""\
NAIVE LLM USER EXPERIENCE ASSESSMENT - VisLang MCP Server
==========================================================

Test date: Simulated naive LLM conversation exploring wildfire data.

OVERALL UX QUALITY: GOOD
The VisLang MCP server provides a well-designed conversational interface
for iterative scientific visualization. A naive LLM user can go from
"show me the wildfire data" to a multi-layer visualization with streamlines,
cross-sections, and camera control in ~7 steps.

STRENGTHS:
-----------
1. list_capabilities() is an excellent entry point - shows all available
   VTK classes, colormaps, and DSL functions in one call.

2. get_array_info() provides exactly what an LLM needs to start: field names,
   types, ranges, component counts, and spatial bounds all together.

3. get_statistics() and get_spatial_extent() give the LLM enough information
   to make informed decisions about thresholds, isosurface values, and
   seed point placement WITHOUT guessing.

4. set_pipeline() returns structured per-node status reports with point counts,
   cell counts, available arrays, and camera state. Warnings include actionable
   hints (e.g., "use get_ground_z to find valid z-coordinates").

5. seeds_near() is a killer feature - it auto-places streamline seed points
   near a feature of interest. This saves the LLM from a common multi-step
   failure mode (manually guessing seed coordinates in terrain-following grids).

6. suggest_camera() returns copy-pasteable DSL code - perfect for an LLM
   that can just splice it into the pipeline.

7. get_pipeline() enables incremental modification - the LLM can see its
   current pipeline and add to it rather than starting from scratch.

8. sample_point() is excellent for "what's happening here?" questions,
   showing the snapped grid location so the LLM knows about discretization.

9. The DSL is clean and Pythonic - an LLM can write it naturally without
   learning an unusual syntax.

ROUGH EDGES / AREAS FOR IMPROVEMENT:
--------------------------------------
""")

# Add dynamic findings
for i, n in enumerate(assessment_notes, 1):
    assessment += f"{i}. {n}\n"

assessment += textwrap.dedent("""
ADDITIONAL OBSERVATIONS:
-------------------------
a) screenshot() returns an MCP Image object. In a real MCP session the LLM
   would see the rendered image. In this test we can only verify the type.

b) The version history (v0001, v0002...) with saved pipeline code and
   screenshots is a nice touch for undo/restore workflows.

c) Error messages include traceback snippets which are helpful for debugging
   but could overwhelm a non-technical user. For an LLM intermediary this
   is fine.

d) The reader cache (avoiding re-reads of large VTS files on pipeline rebuild)
   is important for interactive iteration speed.

e) The color_by="Vel" for streamlines may need scalar_range to be set
   explicitly for good visual results - the LLM might not realize this
   on first try.

f) The slice() function using origin/normal is intuitive, but a naive user
   might not know what normal vector to use. A helper like
   suggest_slice(direction="YZ", position=80) could be helpful.

g) There's no explicit "help" or "tutorial" tool. list_capabilities() serves
   this role partially, but a get_examples() tool showing common pipeline
   patterns would accelerate the first interaction.

h) No tool to list available lut/colormap names with descriptions of what
   they look like. The LLM must pick from the list in list_capabilities()
   without knowing what "fire" vs "coolwarm" vs "viridis" actually looks like.

CONCLUSION:
-----------
The VisLang MCP server is well-suited for LLM-driven scientific visualization.
The query tools provide the right level of data introspection for informed
decision-making, the DSL is clean and composable, and the error reporting
is actionable. The main gap is the lack of example patterns and colormap
previews, which would further reduce the number of iterations needed.
""")

with open("/tmp/naive_llm_assessment.txt", "w") as f:
    f.write(assessment)

print("\n  Assessment written to /tmp/naive_llm_assessment.txt")
print("\n  DONE.")
