"""A stable index page for SIVA's trame rendering mode.

In ``--trame`` mode each view runs its own trame server on its own
auto-picked localhost port. ``ViewIndexServer`` provides one small,
long-lived HTTP server -- started once per process -- that serves a single
HTML page listing every live view with a link to its trame URL, so the user
can arrange/swap views via browser tabs without hunting down ports.

This module is deliberately stdlib-only (``http.server``): no new
dependency is needed just to list a handful of links. It has no import-time
dependency on trame or VTK, so it (and its tests) work even when the
``trame`` extra isn't installed.

Design notes
------------
- Bound to ``127.0.0.1`` only (matches the per-view trame servers).
- The live view registry is owned by ``siva.server``; this module never
  imports it (would create a cycle). Instead ``ViewIndexServer`` is handed a
  ``snapshot_fn`` callable that returns a fresh list of ``ViewInfo`` on
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
"""

from __future__ import annotations

import html as _html
import logging
import os
import threading
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("siva.view_index")

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


def _make_handler_class(snapshot_fn: Callable[[], list[ViewInfo]], session_label: str):
    """Build a ``BaseHTTPRequestHandler`` subclass closing over server state.

    A closure (rather than stashing state on the ``HTTPServer`` instance) is
    simplest here since ``ThreadingHTTPServer`` only lets us pass a handler
    *class*, not an instance.
    """

    class _Handler(BaseHTTPRequestHandler):
        server_version = "SIVAViewIndex/1"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            # Route access logging through our logger, never stdout/stderr
            # (stdio is reserved for MCP JSON-RPC).
            logger.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path in ("/", ""):
                    self._serve_index()
                elif path.startswith("/thumb/"):
                    self._serve_thumb(path[len("/thumb/"):])
                else:
                    self.send_error(404, "Not found")
            except (BrokenPipeError, ConnectionResetError):
                # Client went away mid-response (e.g. page navigated away
                # during auto-refresh) -- not a server error.
                pass

        def _serve_index(self):
            views = snapshot_fn()
            body = render_index_html(session_label, views).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_thumb(self, raw_name):
            # Validate strictly against the live registry snapshot -- the
            # name is never used as a filesystem path fragment directly, so
            # a traversal attempt (e.g. "../secret") simply won't match any
            # live view and 404s.
            name = urllib.parse.unquote(raw_name)
            views = snapshot_fn()
            match = next((v for v in views if v.name == name), None)
            if match is None or not match.thumbnail_path:
                self.send_error(404, "No thumbnail for this view")
                return
            thumb_path = Path(match.thumbnail_path)
            if not thumb_path.is_file():
                self.send_error(404, "No thumbnail for this view")
                return
            data = thumb_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return _Handler


class ViewIndexServer:
    """A tiny stdlib HTTP server that serves the live-view index page.

    Started once per process in ``--trame`` mode (see ``server.py``'s
    ``main()``); one stable URL for the whole session regardless of how many
    views come and go.
    """

    def __init__(self, snapshot_fn: Callable[[], list[ViewInfo]],
                 session_label: str = "SIVA", host: str = "127.0.0.1",
                 port: int = 0):
        handler_cls = _make_handler_class(snapshot_fn, session_label)
        self._httpd = ThreadingHTTPServer((host, port), handler_cls)
        self._httpd.daemon_threads = True
        self._host = host
        self._port = self._httpd.server_address[1]
        self.url, self.proxy_url = resolve_url(self._port)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="siva-view-index",
            daemon=True,
        )
        self._thread.start()
        logger.info("SIVA view index serving at %s", self.url)
        if self.proxy_url:
            logger.info("SIVA view index proxied at %s", self.proxy_url)

    @property
    def port(self) -> int:
        return self._port

    def shutdown(self):
        """Stop serving and join the server thread. Safe to call twice."""
        try:
            self._httpd.shutdown()
        except Exception:  # pragma: no cover - defensive
            logger.debug("View index shutdown() failed", exc_info=True)
        try:
            self._httpd.server_close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("View index server_close() failed", exc_info=True)
        self._thread.join(timeout=5)
