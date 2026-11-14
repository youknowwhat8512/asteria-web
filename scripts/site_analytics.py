"""First-party daily page-view totals; preserve but never extend legacy IDs."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import threading
import time
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import hashlib
import hmac
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path, secret: bytes):
        if len(secret) < 32:
            raise ValueError('analytics secret must have at least 32 bytes')
        self.path, self.secret = path, secret
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    visitor TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_day_path ON events(day,path);
                CREATE TABLE IF NOT EXISTS page_view_totals (
                    day TEXT NOT NULL,
                    path TEXT NOT NULL,
                    views INTEGER NOT NULL,
                    PRIMARY KEY(day,path)
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def record(self, kind, path, browser_id, event_id, day):
        visitor = hmac.new(self.secret, browser_id.encode('ascii'), hashlib.sha256).hexdigest()
        with self.connect() as db:
            result = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                                (event_id, day, kind, path, visitor))
            return result.rowcount == 1

    def record_page_view(self, path, day):
        with self.connect() as db:
            db.execute('''INSERT INTO page_view_totals(day,path,views) VALUES (?,?,1)
                ON CONFLICT(day,path) DO UPDATE SET views=views+1''', (day, path))
        return True

    def prune(self, cutoff):
        with self.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM events WHERE day < ?', (cutoff,))
            db.execute('DELETE FROM page_view_totals WHERE day < ?', (cutoff,))

    def report(self, start, end, catalog):
        with self.connect() as db:
            legacy = db.execute('''SELECT day,path,COUNT(*) AS views FROM events
                WHERE day BETWEEN ? AND ? AND kind='page_view' GROUP BY day,path''', (start, end)).fetchall()
            anonymous = db.execute('''SELECT day,path,views FROM page_view_totals
                WHERE day BETWEEN ? AND ?''', (start, end)).fetchall()
            meta = dict(db.execute('SELECT key,value FROM metadata'))
        by_path, by_day = Counter(), Counter()
        for row in [*legacy, *anonymous]:
            by_path[row['path']] += row['views']
            by_day[row['day']] += row['views']
        episodes = [dict(path=path, title=title, views=by_path[path])
                    for path, title in catalog.items() if path.startswith('/magazine/') and path != '/magazine/']
        episodes.sort(key=lambda r: (-r['views'], r['title']))
        return dict(start=start, end=end, mode='pageviews_only', page_views=sum(by_path.values()),
                    home_views=by_path['/'], episode_views=sum(item['views'] for item in episodes),
                    episodes=episodes, daily=[dict(day=day, views=by_day[day]) for day in sorted(by_day)],
                    started_at=meta.get('started_at'), pageviews_only_since=meta.get('pageviews_only_since'))


KST = dt.timezone(dt.timedelta(hours=9))
EVENT_ROUTE = '/api/v1/analytics/event'
REPORT_ROUTE = '/api/v1/analytics/report'


def today():
    return dt.datetime.now(KST).date()


class AnalyticsServer(ThreadingHTTPServer):
    store: Store
    catalog: dict[str, str]
    catalog_file: Path | None
    allowed_origins: set[str]
    rate_window: deque
    rate_lock: threading.Lock
    maintenance_lock: threading.Lock
    maintenance_day: str

    def service_actions(self):
        # serve_forever calls this even with no requests: enforce retention while idle.
        if self.maintenance_day != today().isoformat():
            self.maintain()

    def maintain(self):
        with self.maintenance_lock:
            day = today().isoformat()
            if day != self.maintenance_day:
                self.store.prune((today() - dt.timedelta(days=89)).isoformat())
                self.maintenance_day = day
            if self.catalog_file:
                self.catalog = json.loads(self.catalog_file.read_text())


class Handler(BaseHTTPRequestHandler):
    server: AnalyticsServer  # type: ignore[assignment]

    # Never log URLs, request headers, IP addresses or request payloads.
    def log_message(self, format, *args):
        pass

    def reply(self, status, payload, content_type='application/json; charset=utf-8'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def private_request(self):
        host = self.headers.get('Host', '').split(':')[0]
        origin = self.headers.get('Origin')
        return (host in {'localhost', '127.0.0.1'} and
                (not origin or origin == f'http://{self.headers.get("Host")}'))

    def do_POST(self):
        if self.path != EVENT_ROUTE:
            return self.reply(404, {'error': 'not_found'})
        if self.headers.get('Origin') not in self.server.allowed_origins:
            return self.reply(403, {'error': 'origin'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'content_type'})
        if self.headers.get('Transfer-Encoding'):
            return self.reply(400, {'error': 'body'})
        self.server.maintain()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 2 <= length <= 1024:
                raise ValueError()
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {'kind', 'path'}:
                raise ValueError()
            kind, path = payload['kind'], payload['path']
            if kind != 'page_view' or not isinstance(path, str) or path not in self.server.catalog:
                raise ValueError()
        except (ValueError, TypeError, TimeoutError, OSError):
            return self.reply(400, {'error': 'invalid_event'})
        # One bounded, global memory window, with hashed client keys. No IP persists.
        ip = self.headers.get('CF-Connecting-IP', self.client_address[0])
        key = hmac.new(self.server.store.secret, ip.encode(), hashlib.sha256).digest()
        now = time.monotonic()
        with self.server.rate_lock:
            queue = self.server.rate_window
            while queue and queue[0][0] < now - 60:
                queue.popleft()
            if len(queue) >= 3000 or sum(item[1] == key for item in queue) >= 120:
                return self.reply(429, {'error': 'rate_limit'})
            queue.append((now, key))
        counted = self.server.store.record_page_view(path, today().isoformat())
        return self.reply(200, {'counted': counted})

    def do_GET(self):
        if not self.private_request():
            return self.reply(403, {'error': 'private'})
        url = urlsplit(self.path)
        if url.path == '/':
            dashboard = Path(__file__).with_name('analytics_dashboard.html')
            return self.reply(200, dashboard.read_bytes(), 'text/html; charset=utf-8')
        if url.path != REPORT_ROUTE:
            return self.reply(404, {'error': 'not_found'})
        try:
            query = parse_qs(url.query, strict_parsing=True)
            if set(query) - {'days'} or len(query.get('days', ['7'])) != 1:
                raise ValueError()
            days = int(query.get('days', ['7'])[0])
            if days not in {1, 7, 30, 90}:
                raise ValueError()
        except ValueError:
            return self.reply(400, {'error': 'invalid_period'})
        end = today()
        start = end - dt.timedelta(days=days - 1)
        # Catalog file follows future episode deployments, without a service restart.
        self.server.maintain()
        report = self.server.store.report(start.isoformat(), end.isoformat(), self.server.catalog)
        report['is_preview'] = any(origin.startswith('http://') for origin in self.server.allowed_origins)
        return self.reply(200, report)


def create_server(database, secret, catalog, *, port=8794, allowed_origins=None, catalog_file=None):
    server = AnalyticsServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.store = Store(database, secret)
    server.catalog = catalog
    server.catalog_file = catalog_file
    server.allowed_origins = set(allowed_origins or ['https://asteria.club'])
    server.rate_window = deque()
    server.rate_lock = threading.Lock()
    server.maintenance_lock = threading.Lock()
    server.maintenance_day = ''
    server.maintain()
    with server.store.connect() as db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('started_at',?)", (dt.datetime.now(KST).isoformat(),))
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('pageviews_only_since',?)", (dt.datetime.now(KST).isoformat(),))
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--secret-file', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8794)
    parser.add_argument('--preview-origin', action='append', default=[])
    args = parser.parse_args()
    if not args.secret_file.exists():
        args.secret_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_hex(32))
    if args.secret_file.stat().st_mode & 0o077:
        raise SystemExit('secret file permissions must be 0600')
    args.database.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(args.database.parent, 0o700)
    server = create_server(args.database, args.secret_file.read_bytes(), json.loads(args.catalog.read_text()),
                           port=args.port, catalog_file=args.catalog,
                           allowed_origins=['https://asteria.club', *args.preview_origin])
    server.serve_forever()


if __name__ == '__main__':
    main()
