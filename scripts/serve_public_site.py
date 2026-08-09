#!/usr/bin/env python3
"""Serve Asteria's allow-listed public tree with one canonical origin."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CANONICAL_HOST = "asteria.club"
CANONICAL_ORIGIN = "https://asteria.club"
REDIRECT_HOSTS = {"www.asteria.club"}


class CanonicalStaticHandler(SimpleHTTPRequestHandler):
    def _request_host(self) -> str:
        return self.headers.get("Host", "").split(":", 1)[0].lower().rstrip(".")

    def _forwarded_proto(self) -> str:
        return self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip().lower()

    def _route_canonical(self) -> bool:
        host = self._request_host()
        if host not in {CANONICAL_HOST, *REDIRECT_HOSTS, "127.0.0.1", "localhost"}:
            self.send_error(421, "Misdirected Request")
            return True
        if host in REDIRECT_HOSTS or (host == CANONICAL_HOST and self._forwarded_proto() != "https"):
            self.send_response(301)
            self.send_header("Location", CANONICAL_ORIGIN + self.path)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

    def do_GET(self) -> None:
        if not self._route_canonical():
            super().do_GET()

    def do_HEAD(self) -> None:
        if not self._route_canonical():
            super().do_HEAD()

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve(strict=True)
    handler = partial(CanonicalStaticHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
