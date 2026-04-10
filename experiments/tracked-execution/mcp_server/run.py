"""Run the tracked execution MCP server."""
import sys
import os

# Add tracked_execution to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
