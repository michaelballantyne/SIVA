"""Run the tracked execution MCP server."""
import sys
import os
import threading

# Add tracked_execution to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp_server import server
from mcp_server.server import mcp, run_event_loop

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tracked Execution MCP Server")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Run in offscreen mode (no interactive window). Default: interactive.",
    )
    parser.add_argument(
        "--trame",
        action="store_true",
        help="Serve visualization in browser via Trame (implies offscreen rendering).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Trame server port (only used with --trame). Default: 8080.",
    )
    args = parser.parse_args()

    if args.trame:
        # Trame mode: offscreen plotters + browser-based viewer served via Trame.
        # _offscreen stays True (the default).
        from mcp_server.trame_viewer import TrameViewer
        viewer = TrameViewer(port=args.port)
        server._trame_viewer = viewer
        # Start Trame in a background thread so the main thread can run the MCP
        # server over stdio.
        viewer.start(block=False)
        mcp.run(transport="stdio")
    elif args.offscreen:
        # Offscreen mode: run_on_main_thread calls fn() directly on the calling
        # thread. No event loop needed. _offscreen stays True (the default).
        mcp.run(transport="stdio")
    else:
        # Interactive mode: open real VTK windows. The MCP server runs on a
        # background thread; the main thread runs the event loop to pump VTK
        # events and dispatch VTK work from tool handlers.
        server._offscreen = False
        mcp_thread = threading.Thread(
            target=lambda: mcp.run(transport="stdio"),
            daemon=True,
        )
        mcp_thread.start()
        run_event_loop()
