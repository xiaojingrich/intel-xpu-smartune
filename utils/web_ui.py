# Copyright (c) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Serve the built dashboard (dashboard/dist) from the same Flask process that
# exposes the API, so a single origin (e.g. https://localhost:9001) renders the
# UI *and* answers its /api/* calls. This is what lets the packaged product open
# in a browser without the separate Vite dev server.
#
# The built dashboard issues every request under /api (axios baseURL '/api').
# In development the Vite dev server rewrites '^/api' -> '' before proxying to
# the backend, whose routes live at the root (/dynamic_info, /app/..., etc.).
# ApiPrefixMiddleware reproduces exactly that rewrite at the WSGI layer, so the
# same build works unchanged whether it is served by Vite or by this process.

import os

from flask import send_from_directory
from werkzeug.exceptions import NotFound

# Flask endpoint name of the dashboard static-file handler. The access-token gate
# in smartune_api exempts exactly this endpoint (and nothing else) so the login
# page can load before a token exists. Keying the exemption on the resolved
# endpoint — not on the URL — is what keeps the API protected: the API routes are
# ALSO mounted at the root (not only under /api), so a path-prefix rule would let
# `GET /dynamic_info` slip past auth. The endpoint is unambiguous.
DASHBOARD_ENDPOINT = "smartune_dashboard"

# dashboard/dist sits next to this package:
#   <root>/utils/web_ui.py  ->  <root>/dashboard/dist
_DIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "dist"
)


class ApiPrefixMiddleware:
    """Strip a leading /api from the request path so the built dashboard (which
    calls /api/*) reaches the API handlers registered at the root, exactly as the
    Vite dev server's '^/api' -> '' rewrite does in development."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/api" or path.startswith("/api/"):
            environ["PATH_INFO"] = path[len("/api"):] or "/"
        return self.wsgi_app(environ, start_response)


def mount_dashboard(app, dist_dir=_DIST_DIR):
    """Serve the built dashboard from ``dist_dir`` and route /api/* to the API.

    Registers a catch-all GET handler (endpoint ``DASHBOARD_ENDPOINT``) that
    returns a static file when one exists under ``dist_dir`` and otherwise falls
    back to index.html for client-side routes, then wraps the WSGI app with
    ApiPrefixMiddleware. If the build is absent the handler is skipped but the
    middleware is still installed, so API clients keep working.

    A fully static API rule (e.g. /smartune/capabilities) outranks the
    ``/<path:path>`` converter in Werkzeug's matcher, so this catch-all never
    shadows — nor exposes — a real endpoint.
    """
    if not os.path.isdir(dist_dir):
        app.logger.warning(
            "Dashboard build not found at %s; serving API only (no UI).", dist_dir
        )
        app.wsgi_app = ApiPrefixMiddleware(app.wsgi_app)
        return

    @app.route("/", defaults={"path": ""}, endpoint=DASHBOARD_ENDPOINT)
    @app.route("/<path:path>", endpoint=DASHBOARD_ENDPOINT)
    def _serve_dashboard(path):
        # send_from_directory uses werkzeug.safe_join, which rejects absolute
        # paths and ../ traversal (NotFound). Anything that is not an existing
        # file under dist_dir falls back to index.html for client-side routing —
        # never to a file outside the build.
        if path:
            try:
                return send_from_directory(dist_dir, path)
            except NotFound:
                pass
        return send_from_directory(dist_dir, "index.html")

    app.wsgi_app = ApiPrefixMiddleware(app.wsgi_app)
