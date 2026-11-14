"""Isolated analytics tests; never touch the production database."""
import importlib.util
import json
import threading
import urllib.request
import urllib.error
import uuid
import tempfile
import unittest
from pathlib import Path
from functools import partial
from http.server import ThreadingHTTPServer

MODULE = Path(__file__).with_name('site_analytics.py')


class AnalyticsTests(unittest.TestCase):
    def test_legacy_history_is_preserved_while_reports_only_expose_page_views(self):
        self.assertTrue(MODULE.exists(), 'first-party analytics service is not implemented')
        spec = importlib.util.spec_from_file_location('site_analytics', MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            store = module.Store(Path(directory) / 'test.sqlite3', b'x' * 32)
            catalog = {'/': '홈', '/magazine/test-episode/': '테스트 에피소드'}
            store.record('page_view', '/', 'browser-a', 'event-1', '2026-11-10')
            store.record('page_view', '/', 'browser-a', 'event-1', '2026-11-10')
            store.record('page_view', '/magazine/test-episode/', 'browser-a', 'event-2', '2026-11-10')
            store.record('episode_engaged', '/magazine/test-episode/', 'browser-a', 'event-3', '2026-11-10')
            store.record('page_view', '/', 'browser-a', 'event-4', '2026-11-11')
            store.record('page_view', '/magazine/test-episode/', 'browser-b', 'event-5', '2026-11-11')
            report = store.report('2026-11-10', '2026-11-11', catalog)
            self.assertEqual(report['mode'], 'pageviews_only')
            self.assertNotIn('visitors', report)
            self.assertEqual(report['page_views'], 4)
            self.assertNotIn('home_visitors', report)
            self.assertEqual(report['home_views'], 2)
            episode = report['episodes'][0]
            self.assertEqual(episode['views'], 2)
            self.assertNotIn('visitors', episode)
            self.assertNotIn('engaged', episode)
            with store.connect() as connection:
                data = repr([tuple(r) for r in connection.execute('SELECT * FROM events')])
            self.assertNotIn('browser-a', data)
            self.assertNotIn('browser-b', data)

    def test_retention_removes_only_events_older_than_90_days(self):
        spec = importlib.util.spec_from_file_location('site_analytics', MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            store = module.Store(Path(directory) / 'test.sqlite3', b'x' * 32)
            store.record('page_view', '/', 'browser-a', 'old-event', '2026-08-13')
            store.record('page_view', '/', 'browser-a', 'recent-event', '2026-08-14')
            store.record_page_view('/', '2026-08-13')
            store.record_page_view('/', '2026-08-14')
            self.assertTrue(hasattr(store, 'prune'), '90-day retention is not implemented')
            store.prune('2026-08-14')
            report = store.report('2026-01-01', '2026-12-31', {'/': '홈'})
            self.assertEqual(report['page_views'], 2)

    def test_idle_service_prunes_expired_events_without_a_visit(self):
        spec = importlib.util.spec_from_file_location('site_analytics', MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            server = module.create_server(Path(directory) / 'test.sqlite3', b'x'*32, {'/':'홈'}, port=0)
            try:
                server.store.record('page_view', '/', 'browser-a', 'expired', '2000-01-01')
                server.store.record_page_view('/', '2000-01-01')
                server.maintenance_day = ''
                server.service_actions()
                self.assertEqual(server.store.report('2000-01-01', '2099-01-01', {'/':'홈'})['page_views'], 0)
            finally:
                server.server_close()

    def test_build_injects_only_public_pages_and_generates_complete_catalog(self):
        helper = MODULE.with_name('prepare_site_analytics.py')
        self.assertTrue(helper.exists(), 'build integration is not implemented')
        spec = importlib.util.spec_from_file_location('prepare_site_analytics', helper)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, title in [('index.html', '홈'), ('magazine/index.html', '매거진'),
                                ('magazine/new-story/index.html', '새 에피소드')]:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f'<html><head><title>{title}</title></head><body>본문</body></html>')
            module.prepare(root)
            module.prepare(root)
            self.assertEqual((root / 'index.html').read_text().count('/site-analytics.js?v='), 1)
            catalog = json.loads((root / 'analytics-catalog.json').read_text())
            self.assertEqual(set(catalog), {'/', '/magazine/', '/magazine/new-story/'})
            self.assertEqual(catalog['/magazine/new-story/'], '새 에피소드')
            self.assertTrue((root / 'site-analytics.js').exists())

    def test_dashboard_asset_exists_and_has_no_external_dependencies(self):
        dashboard = MODULE.with_name('analytics_dashboard.html')
        self.assertTrue(dashboard.exists(), 'private dashboard is not implemented')
        text = dashboard.read_text()
        self.assertIn('/api/v1/analytics/report', text)
        self.assertNotIn('<script src="https://', text)
        self.assertIn('90일', text)

    def test_public_proxy_allows_event_only_and_never_exposes_reports(self):
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        analytics = load('site_analytics', MODULE)
        origin = load('serve_public_site', MODULE.with_name('serve_public_site.py'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = analytics.create_server(root / 'test.sqlite3', b'x'*32, {'/':'홈'}, port=0)
            class Enabled(origin.CanonicalStaticHandler):
                analytics_proxy_enabled = True
                analytics_upstream = ('127.0.0.1', collector.server_port)
                def log_message(self, *args): pass
            public = ThreadingHTTPServer(('127.0.0.1', 0), partial(Enabled, directory=str(root)))
            for server in [collector, public]:
                threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{public.server_port}'
            try:
                payload = dict(kind='page_view', path='/')
                req = urllib.request.Request(base+'/api/v1/analytics/event', data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json', 'Origin':'https://asteria.club'})
                try:
                    with urllib.request.urlopen(req) as response:
                        status = response.status
                except urllib.error.HTTPError as error:
                    status = error.code
                self.assertEqual(status, 200, 'event proxy must work when explicitly enabled')
                for route in ['/api/v1/analytics/report?days=7', '/api/v1/analytics/', '/api/v1/analytics/event?private=1']:
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base+route)
                    self.assertEqual(caught.exception.code, 404)
                self.assertFalse(origin.CanonicalStaticHandler.analytics_proxy_enabled, 'production opt-in must be explicit')
            finally:
                for server in [public, collector]:
                    server.shutdown()
                    server.server_close()

    def test_http_accepts_only_valid_events_and_keeps_reports_private(self):
        spec = importlib.util.spec_from_file_location('site_analytics', MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, 'create_server'), 'HTTP analytics service is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = {'/': '홈', '/magazine/test-episode/': '테스트'}
            server = module.create_server(root / 'test.sqlite3', b'x'*32, catalog, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(route, payload=None, origin='https://asteria.club', host=None):
                headers = {'Origin': origin}
                if host:
                    headers['Host'] = host
                if payload is not None:
                    headers['Content-Type'] = 'application/json'
                req = urllib.request.Request(base+route,
                    data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    return error.code, None
            event = dict(kind='page_view', path='/')
            try:
                self.assertEqual(request('/api/v1/analytics/event', event, origin='https://evil.example')[0], 403)
                self.assertEqual(request('/api/v1/analytics/event', {**event, 'path': '/?email=private@example.com'})[0], 400)
                self.assertEqual(request('/api/v1/analytics/event', {**event, 'email': 'private@example.com'})[0], 400)
                self.assertEqual(request('/api/v1/analytics/event', {**event, 'browser_id': str(uuid.uuid4()), 'event_id': str(uuid.uuid4())})[0], 400)
                self.assertEqual(request('/api/v1/analytics/event', {**event, 'kind': 'episode_engaged', 'path': '/magazine/test-episode/'})[0], 400)
                status, result = request('/api/v1/analytics/event', event)
                self.assertEqual(status, 200)
                assert result is not None
                self.assertTrue(result['counted'])
                repeat_status, repeated = request('/api/v1/analytics/event', event)
                self.assertEqual(repeat_status, 200)
                assert repeated is not None
                self.assertTrue(repeated['counted'])
                self.assertEqual(request('/api/v1/analytics/report', origin='https://asteria.club')[0], 403)
                self.assertEqual(request('/api/v1/analytics/report', origin='http://evil.example', host='evil.example')[0], 403)
                status, report = request('/api/v1/analytics/report?days=7', origin=base)
                self.assertEqual(status, 200)
                assert report is not None
                self.assertEqual(report['page_views'], 2)
                self.assertNotIn('visitors', report)
                self.assertFalse(report['is_preview'])
                server.allowed_origins.add('http://127.0.0.1:8795')
                preview_report = request('/api/v1/analytics/report?days=7', origin=base)[1]
                assert preview_report is not None
                self.assertTrue(preview_report['is_preview'])
                self.assertEqual(request('/api/v1/analytics/report?days=999', origin=base)[0], 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
