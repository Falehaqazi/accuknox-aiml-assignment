"""A small read-only REST API that serves the student test-score dataset.

    GET /api/students   the dataset as JSON
    GET /health         {"status": "ok"}

Standard library only. Run it on its own with:

    python mock_scores_api.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("scores-api")

DATA_FILE = Path(__file__).resolve().parent / "data" / "students.json"


class ScoresHandler(BaseHTTPRequestHandler):
    server_version = "ScoresAPI/1.0"

    def __init__(self, *args: Any, payload: bytes, **kwargs: Any) -> None:
        self.payload = payload  # must be set before the base class starts handling the request
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 (name required by BaseHTTPRequestHandler)
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/students":
            self._send(HTTPStatus.OK, self.payload)
        elif path == "/health":
            self._send(HTTPStatus.OK, b'{"status": "ok"}')
        else:
            body = json.dumps({"error": f"no route for {path or '/'}"}).encode()
            self._send(HTTPStatus.NOT_FOUND, body)

    def _send(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (base-class signature)
        log.debug("%s %s", self.address_string(), format % args)


def make_server(
    host: str = "127.0.0.1", port: int = 0, data_file: Path = DATA_FILE
) -> ThreadingHTTPServer:
    """Build the server. Port 0 asks the OS for any free port."""
    payload = Path(data_file).read_bytes()
    json.loads(payload)  # refuse to start on a malformed dataset
    return ThreadingHTTPServer((host, port), partial(ScoresHandler, payload=payload))


@contextmanager
def running_server(data_file: Path = DATA_FILE) -> Iterator[str]:
    """Run the API on a background thread and yield the URL of its students endpoint."""
    server = make_server(host="127.0.0.1", port=0, data_file=data_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/students"
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the student scores dataset over HTTP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", type=Path, default=DATA_FILE, help="JSON file to serve")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    server = make_server(args.host, args.port, args.data)
    print(
        f"Serving {args.data.name} at http://{args.host}:{args.port}/api/students (Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
