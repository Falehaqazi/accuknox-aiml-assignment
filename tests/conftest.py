"""Shared test fixtures: a throwaway local HTTP server that serves canned JSON."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# A route maps the query string to (status, body). Bodies are JSON-encoded unless bytes.
Route = Callable[[dict[str, list[str]]], tuple[int, Any]]


@pytest.fixture
def json_server():
    routes: dict[str, Route] = {}
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            parts = urlsplit(self.path)
            route = routes.get(parts.path)
            status, body = route(parse_qs(parts.query)) if route else (404, {"error": "not found"})
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{server.server_port}", routes=routes, hits=hits
        )
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def openlibrary(json_server):
    """Serve an Open Library-style result set at /search.json, paged by limit and page.

    The fixture holds nine hits in Open Library's response shape (made-up work ids,
    real books), including one hit with no title and one with no author or year.
    """
    recorded = json.loads((FIXTURES / "openlibrary_search.json").read_text())

    def search(query: dict[str, list[str]]) -> tuple[int, Any]:
        limit = int(query.get("limit", ["100"])[0])
        page = int(query.get("page", ["1"])[0])
        docs = recorded["docs"][(page - 1) * limit : page * limit]
        return 200, {"numFound": recorded["numFound"], "start": (page - 1) * limit, "docs": docs}

    json_server.routes["/search.json"] = search
    json_server.url = f"{json_server.base}/search.json"
    return json_server
