from __future__ import annotations

import argparse
import http.client
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_STATIC_ROOT = Path("/var/tmp/edia_static")
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 3000


class FrontendProxyHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, directory: str, backend_host: str, backend_port: int, **kwargs):
        self._backend_host = backend_host
        self._backend_port = backend_port
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:
        if self.path == "/health" or self.path.startswith("/api/"):
            self._proxy_request()
            return
        super().do_GET()

    def do_POST(self) -> None:
        self._proxy_request()

    def do_PUT(self) -> None:
        self._proxy_request()

    def do_PATCH(self) -> None:
        self._proxy_request()

    def do_DELETE(self) -> None:
        self._proxy_request()

    def do_OPTIONS(self) -> None:
        self._proxy_request()

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.exists(path):
            return super().send_head()

        index_path = os.path.join(self.directory, "index.html")
        if os.path.exists(index_path):
            file_handle = open(index_path, "rb")
            fs = os.fstat(file_handle.fileno())
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(fs.st_size))
            self.end_headers()
            return file_handle

        self.send_error(404, "File not found")
        return None

    def _proxy_request(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length > 0 else None

        upstream = http.client.HTTPConnection(
            self._backend_host,
            self._backend_port,
            timeout=30,
        )
        try:
            headers = {key: value for key, value in self.headers.items()}
            headers["Host"] = f"{self._backend_host}:{self._backend_port}"
            headers["Connection"] = "close"
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            payload = response.read()

            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                lower_key = key.lower()
                if lower_key in {"transfer-encoding", "connection", "server", "date"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception as exc:
            encoded = str(exc).encode("utf-8", errors="replace")
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        finally:
            upstream.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve frontend static files and proxy API requests.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--static-root", default=str(DEFAULT_STATIC_ROOT))
    parser.add_argument("--backend-host", default=DEFAULT_BACKEND_HOST)
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = partial(
        FrontendProxyHandler,
        directory=args.static_root,
        backend_host=args.backend_host,
        backend_port=args.backend_port,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Serving frontend proxy on http://{args.host}:{args.port} "
        f"-> {args.backend_host}:{args.backend_port} with static root {args.static_root}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()