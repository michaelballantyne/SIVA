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
    args = parser.parse_args()

    if args.offscreen:
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
