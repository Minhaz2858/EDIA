#!/usr/bin/env python3
import argparse
import functools
import http.server
from pathlib import Path


class SpaRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        self._directory = directory
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        if self.path.endswith(".html") or self.path == "/":
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        requested = self.translate_path(self.path)
        path = Path(requested)

        if self.path.startswith("/assets/") or path.exists():
            super().do_GET()
            return

        self.path = "/index.html"
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve frontend dist with SPA fallback.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()

    handler = functools.partial(SpaRequestHandler, directory=args.dir)
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
