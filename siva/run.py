"""Run a SIVA pipeline file directly, without the MCP server.

Usage:
    python -m siva.run pipeline.py                  # interactive window
    python -m siva.run pipeline.py -o screenshot.png  # save image and exit
    python -m siva.run pipeline.py --offscreen        # offscreen, print status only
"""

import argparse
import sys
from pathlib import Path

from .renderer import Renderer, RenderMode
from .compute import evaluate
from . import scene as _scene


def interpret(code, renderer, cache=None):
    """Interpret a DSL code string, build the pipeline, then render it.

    Fused composition of the construct + compute + render phases for
    single-threaded callers — all on the calling thread, which must own the
    renderer. The compute phase (``evaluate``) produces frozen values;
    the render phase (``siva.scene.render_scene``) applies them to the renderer.

    Returns ``(outputs_by_name, statuses, show_statuses, scene)`` where
    ``scene`` is the frozen :class:`~siva.spec.SceneSpec`.
    """
    result = evaluate(code, cache=cache)
    # Render phase: must run on the renderer-owning thread (here, the caller's).
    show_statuses = _scene.render_scene(
        result.scene, result.shows, result.outputs, renderer
    )
    return (
        result.outputs_by_name,
        result.statuses,
        show_statuses,
        result.scene,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run a SIVA pipeline file.",
        usage="python -m siva.run PIPELINE [options]",
    )
    parser.add_argument("pipeline", help="Path to a pipeline .py file")
    parser.add_argument(
        "-o", "--output",
        help="Save a screenshot to this path and exit (implies --offscreen)",
    )
    parser.add_argument(
        "--offscreen", action="store_true",
        help="Run without opening a window",
    )
    parser.add_argument(
        "--size", default="1920x1080",
        help="Window/image size as WIDTHxHEIGHT (default: 1920x1080)",
    )
    args = parser.parse_args()

    # Parse size
    try:
        w, h = args.size.split("x")
        width, height = int(w), int(h)
    except ValueError:
        print(f"Invalid size '{args.size}', expected WIDTHxHEIGHT", file=sys.stderr)
        sys.exit(1)

    # --output implies offscreen
    offscreen = args.offscreen or args.output is not None

    pipeline_path = Path(args.pipeline)
    if not pipeline_path.exists():
        print(f"File not found: {pipeline_path}", file=sys.stderr)
        sys.exit(1)

    code = pipeline_path.read_text()
    renderer = Renderer(width, height, mode=RenderMode.OFFSCREEN if offscreen else RenderMode.INTERACTIVE)

    result = interpret(code, renderer)
    vtk_objects_by_name, node_statuses, show_statuses, scene = result

    if args.output:
        renderer.screenshot(args.output)
        print(f"Saved {args.output}")
    elif offscreen:
        print("Pipeline built successfully (offscreen, no output path given).")
    else:
        # Interactive mode — show the window
        print("Pipeline built. Close the window to exit.")
        renderer.run_event_loop()


if __name__ == "__main__":
    main()
