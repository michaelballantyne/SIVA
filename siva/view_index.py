"""The view-index page for SIVA's trame rendering mode.

In ``--trame`` mode one shared trame server serves every view on a single
port (see ``siva/trame_backend.py``); each view is addressed by the ``ui``
URL query parameter (``/?ui=<view>``). This module provides the index page
mounted on that same server at ``/views``: a single HTML page listing every
live view with a link and a thumbnail (``/thumb/<name>``), so the user can
arrange/swap views via browser tabs without composing query strings by
hand.

The page-rendering logic here is plain stdlib; the HTTP layer is a pair of
aiohttp routes (``add_index_routes``) added to the shared trame server's
own aiohttp application, so the index page rides on the one port everything
else uses. aiohttp (a wslink dependency) is imported lazily inside
``add_index_routes`` — this module keeps no import-time dependency on
trame, aiohttp, or VTK, so ``render_index_html``/``resolve_url`` (and their
tests) work even when the ``trame`` extra isn't installed.

Design notes
------------
- The live view registry is owned by ``siva.server``; this module never
  imports it (would create a cycle). Instead ``add_index_routes`` is handed
  a ``snapshot_fn`` callable that returns a fresh list of ``ViewInfo`` on
  every request -- the registry's own thread-safety (a shallow dict copy)
  is the caller's responsibility, documented at the call site in
  ``server.py``.
- Thumbnails are served from a ``/thumb/<name>`` route that resolves *name*
  by membership in the live snapshot (never as a raw filesystem path) --
  this rules out path traversal by construction, since only view names
  already present in the registry can match.
- Proxy-aware links reuse ``resolve_url`` (also used by
  ``siva.trame_backend``) so the ``VSCODE_PROXY_URI`` substitution logic
  lives in exactly one place.
- The index is served at ``/views`` (no trailing slash) so the page's
  relative thumbnail links (``thumb/<name>``) resolve to ``/thumb/<name>``
  at the app root — including behind a prefix-stripping proxy such as
  code-server's ``/proxy/<port>/``.
"""

from __future__ import annotations

import html as _html
import logging
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("siva.view_index")

# Routes mounted on the shared trame server's aiohttp app. INDEX_ROUTE must
# stay a single path segment with no trailing slash (see module docstring).
INDEX_ROUTE = "/views"
THUMB_ROUTE = "/thumb"

# How often the browser re-fetches the index page (view list, thumbnails).
REFRESH_SECONDS = 4


def resolve_url(port: int, proxy_template: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Return ``(url, proxy_url)`` for a loopback service listening on *port*.

    *proxy_url* is ``None`` unless a ``VSCODE_PROXY_URI``-style template is
    available (``https://host/proxy/{{port}}/``) -- either passed explicitly
    via *proxy_template* or read from the ``VSCODE_PROXY_URI`` environment
    variable. ``{{port}}`` is substituted and a trailing slash is enforced
    (code-server's path-stripping proxy requires it). Shared by
    ``siva.trame_backend`` and this module so the substitution logic lives
    in one place.
    """
    url = f"http://localhost:{port}/"
    template = proxy_template if proxy_template is not None else os.environ.get("VSCODE_PROXY_URI")
    if not template:
        return url, None
    proxy_url = template.replace("{{port}}", str(port))
    if not proxy_url.endswith("/"):
        proxy_url += "/"
    return url, proxy_url


@dataclass
class ViewInfo:
    """A snapshot of one live view, as needed to render the index page."""

    name: str
    url: Optional[str]
    proxy_url: Optional[str]
    current: bool
    thumbnail_path: Optional[str] = None


def render_index_html(session_label: str, views: list[ViewInfo]) -> str:
    """Render the self-contained index HTML page for *views*.

    No external assets (fonts, scripts, stylesheets) -- everything is
    inline so the page works standalone behind a stripping proxy.
    """
    cards = []
    for v in sorted(views, key=lambda v: v.name):
        link = v.proxy_url or v.url or "#"
        safe_link = _html.escape(link, quote=True)
        safe_name = _html.escape(v.name)
        classes = "view current" if v.current else "view"
        marker = ' <span class="badge">focused</span>' if v.current else ""
        thumb_html = ""
        if v.thumbnail_path and Path(v.thumbnail_path).is_file():
            # Relative src (no leading slash) -- required for the link to
            # keep working when the whole page is served through a
            # prefix-stripping proxy such as code-server's /proxy/<port>/.
            thumb_src = f"thumb/{urllib.parse.quote(v.name)}"
            thumb_html = (
                f'<a href="{safe_link}" target="_blank">'
                f'<img class="thumb" src="{thumb_src}" alt="{safe_name} thumbnail"></a>'
            )
        else:
            thumb_html = '<div class="thumb placeholder">no preview yet</div>'
        cards.append(f"""
        <div class="{classes}">
          {thumb_html}
          <div class="label">
            <a href="{safe_link}" target="_blank">{safe_name}</a>{marker}
          </div>
        </div>""")

    body = "\n".join(cards) if cards else '<p class="empty">No live views.</p>'
    safe_label = _html.escape(session_label)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<title>SIVA views -- {safe_label}</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif;
          background: #1e1e1e; color: #ddd; margin: 2rem; }}
  h1 {{ font-size: 1.1rem; font-weight: normal; color: #999;
        margin-bottom: 1.5rem; word-break: break-all; }}
  .views {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
  .view {{ border: 1px solid #3a3a3a; border-radius: 8px; padding: 0.6rem;
           width: 240px; background: #2a2a2a; }}
  .view.current {{ border-color: #4ea1ff; box-shadow: 0 0 0 1px #4ea1ff; }}
  .thumb {{ width: 100%; height: 150px; object-fit: cover; border-radius: 4px;
            display: block; background: #111; }}
  .thumb.placeholder {{ display: flex; align-items: center; justify-content: center;
            color: #666; font-size: 0.85rem; }}
  .label {{ margin-top: 0.5rem; font-size: 0.95rem; display: flex;
            align-items: center; gap: 0.4rem; }}
  .badge {{ font-size: 0.7rem; background: #4ea1ff; color: #111;
            border-radius: 3px; padding: 0.05rem 0.35rem; }}
  .empty {{ color: #888; }}
  a {{ color: #4ea1ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>SIVA views -- {safe_label}</h1>
<div class="views">
{body}
</div>
</body>
</html>
"""


def add_index_routes(app, snapshot_fn: Callable[[], list[ViewInfo]],
                     session_label: str = "SIVA"):
    """Mount the index page and thumbnail routes on an aiohttp *app*.

    Called by ``siva.trame_backend`` with the shared trame server's own
    aiohttp application (via wslink's ``on_server_bind`` hook), before the
    server starts serving — so the index page shares the server's single
    port. *snapshot_fn* is called on every request; see the module
    docstring for its contract.
    """
    from aiohttp import web

    async def index(_request):
        return web.Response(
            text=render_index_html(session_label, snapshot_fn()),
            content_type="text/html",
            charset="utf-8",
        )

    async def thumb(request):
        # Validate strictly against the live registry snapshot -- the name
        # is never used as a filesystem path fragment directly, so a
        # traversal attempt (e.g. "../secret") simply won't match any live
        # view and 404s. (aiohttp's {name} segment also never matches
        # across "/".)
        name = request.match_info["name"]
        match = next((v for v in snapshot_fn() if v.name == name), None)
        if match is None or not match.thumbnail_path:
            raise web.HTTPNotFound(text="No thumbnail for this view")
        thumb_path = Path(match.thumbnail_path)
        if not thumb_path.is_file():
            raise web.HTTPNotFound(text="No thumbnail for this view")
        return web.Response(body=thumb_path.read_bytes(),
                            content_type="image/png")

    app.add_routes([
        web.get(INDEX_ROUTE, index),
        web.get(f"{THUMB_ROUTE}/{{name}}", thumb),
    ])
