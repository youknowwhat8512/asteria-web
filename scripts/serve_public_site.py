# task_id: t_082b0f7a
# 구현자: 덱[제품개발자·아스테리아자동화]
# 검증자: 레벨스 플러스[CEO·기업경영]
"""Serve Asteria's public tree with measurement proxying disabled by default."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CANONICAL_HOST = "asteria.club"
CANONICAL_ORIGIN = "https://asteria.club"
REDIRECT_HOSTS = {"www.asteria.club"}
MEASUREMENT_UPSTREAM = ("127.0.0.1", 8788)
API_PREFIX = "/api/v1/"
MAX_API_BODY = 4096


class CanonicalStaticHandler(SimpleHTTPRequestHandler):
    # Fail closed in production. A future inspection-only subclass may set this
    # true only behind independently validated access control; no query string,
    # public asset, or runtime request header can enable it here.
    measurement_proxy_enabled = False

    def _proxy_measurement(self) -> bool:
        if not self.measurement_proxy_enabled or not self.path.startswith(API_PREFIX):
            return False
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            length = MAX_API_BODY + 1
        if length < 0 or length > MAX_API_BODY:
            self.send_error(413, "Request body too large")
            return True
        body = self.rfile.read(length) if length else None
        headers = {"Host": f"{MEASUREMENT_UPSTREAM[0]}:{MEASUREMENT_UPSTREAM[1]}"}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        try:
            peer = ipaddress.ip_address(self.client_address[0])
            forwarded = self.headers.get("CF-Connecting-IP", "").strip()
            if peer.is_loopback and forwarded:
                headers["CF-Connecting-IP"] = str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
        connection = http.client.HTTPConnection(*MEASUREMENT_UPSTREAM, timeout=5)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for name in ("Content-Type", "Cache-Control"):
                value = response.getheader(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (OSError, http.client.HTTPException):
            self.send_error(503, "Measurement service unavailable")
        finally:
            connection.close()
        return True

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
        if not self._route_canonical() and not self._proxy_measurement():
            super().do_GET()

    def do_HEAD(self) -> None:
        if not self._route_canonical() and not self._proxy_measurement():
            super().do_HEAD()

    def do_POST(self) -> None:
        if not self._route_canonical() and not self._proxy_measurement():
            self.send_error(405, "Method Not Allowed")

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
