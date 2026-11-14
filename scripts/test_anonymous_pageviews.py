"""Identifier-free page-view integration; only temporary databases/servers."""
from pathlib import Path
import importlib.util,json,tempfile,threading,unittest,urllib.request,urllib.error
MODULE=Path(__file__).with_name('site_analytics.py')
class AnonymousPageViewsTest(unittest.TestCase):
    def test_anonymous_requests_only_increment_daily_path_totals_and_keep_history(self):
        spec=importlib.util.spec_from_file_location('analytics_anonymous_test',MODULE)
        assert spec is not None and spec.loader is not None
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            server=module.create_server(Path(directory)/'test.sqlite3',b'x'*32,{'/':'홈','/magazine/story/':'이야기'},port=0)
            server.store.record('page_view','/','legacy-browser','legacy-event',module.today().isoformat())
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                for _ in range(2):
                    req=urllib.request.Request(base+'/api/v1/analytics/event',data=json.dumps({'kind':'page_view','path':'/'}).encode(),headers={'Origin':'https://asteria.club','Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req) as response:status=response.status
                    except urllib.error.HTTPError as error:status=error.code
                    self.assertEqual(status,200,'Identifier-free page view must be accepted')
                with urllib.request.urlopen(base+'/api/v1/analytics/report?days=7') as response:report=json.loads(response.read())
                self.assertEqual(report['mode'],'pageviews_only')
                self.assertEqual((report['page_views'],report['home_views']),(3,3))
                self.assertNotIn('visitors',report)
                self.assertNotIn('engaged',report['episodes'][0])
                with server.store.connect() as db:
                    columns=[r['name'] for r in db.execute('PRAGMA table_info(page_view_totals)')]
                    self.assertEqual(columns,['day','path','views'])
                    self.assertEqual(db.execute('SELECT views FROM page_view_totals').fetchone()[0],2)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1,'Legacy data must remain intact')
                with urllib.request.urlopen(base+'/') as response:dashboard=response.read().decode()
                self.assertNotIn('사이트 전체 방문자',dashboard)
                self.assertNotIn('읽기 지표',dashboard)
            finally:server.shutdown();server.server_close();thread.join(timeout=2)
if __name__=='__main__':unittest.main(verbosity=2)
