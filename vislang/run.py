"""Run a VisLang pipeline file directly, without the MCP server.

Usage:
    python -m vislang.run pipeline.py                  # interactive window
    python -m vislang.run pipeline.py -o screenshot.png  # save image and exit
    python -m vislang.run pipeline.py --offscreen        # offscreen, print status only
"""

import argparse
import sys
from pathlib import Path

from .renderer import Renderer, RenderMode
from .dsl import interpret


def main():
    parser = argparse.ArgumentParser(
        description="Run a VisLang pipeline file.",
        usage="python -m vislang.run PIPELINE [options]",
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
    vtk_objects_by_name, node_statuses, show_statuses, builder = result

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
