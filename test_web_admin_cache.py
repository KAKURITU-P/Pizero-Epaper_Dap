import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_admin_server as admin  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TtlCacheTests(unittest.TestCase):
    def test_reuses_value_until_ttl_expires(self):
        clock = FakeClock()
        cache = admin.TtlCache(10.0, clock=clock)
        calls = []

        def compute():
            calls.append(1)
            return len(calls)

        self.assertEqual(cache.get(compute), (1, False))
        clock.now += 9.9
        self.assertEqual(cache.get(compute), (1, True))
        clock.now += 0.2
        self.assertEqual(cache.get(compute), (2, False))

    def test_force_bypasses_cache(self):
        cache = admin.TtlCache(60.0, clock=FakeClock())
        counter = iter(range(1, 10))
        self.assertEqual(cache.get(lambda: next(counter))[0], 1)
        self.assertEqual(cache.get(lambda: next(counter), force=True), (2, False))
        self.assertEqual(cache.get(lambda: next(counter)), (2, True))

    def test_failed_compute_is_not_cached(self):
        cache = admin.TtlCache(60.0, clock=FakeClock())

        def boom():
            raise RuntimeError("network down")

        with self.assertRaises(RuntimeError):
            cache.get(boom)
        self.assertEqual(cache.get(lambda: "ok"), ("ok", False))

    def test_concurrent_requests_compute_once(self):
        cache = admin.TtlCache(60.0)
        calls = []

        def slow_compute():
            calls.append(1)
            time.sleep(0.15)
            return "value"

        results = []
        threads = [
            threading.Thread(target=lambda: results.append(cache.get(slow_compute)[0]))
            for _ in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, ["value"] * 6)


class AdminEndpointTests(unittest.TestCase):
    """実際にHTTPサーバーを立て、重い処理がキャッシュされることを確認する。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), admin.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.originals = (admin.git_revision_info, admin._build_status)
        admin.STATUS_CACHE = admin.TtlCache(admin.STATUS_CACHE_TTL_SEC)
        admin.GITHUB_CHECK_CACHE = admin.TtlCache(admin.GITHUB_CHECK_TTL_SEC)
        self.fetches = 0
        self.status_builds = 0

        def fake_git_info(fetch=False):
            if fetch:
                self.fetches += 1
            commit = {"hash": "abc1234", "full_hash": "a" * 40, "subject": "test"}
            return {"remote": "origin", "branch": "main", "current": commit, "latest": commit}

        def fake_status():
            self.status_builds += 1
            return {"battery": "80%"}

        admin.git_revision_info = fake_git_info
        admin._build_status = fake_status

    def tearDown(self):
        admin.git_revision_info, admin._build_status = self.originals

    def get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_github_check_is_cached_until_forced(self):
        first = self.get("/api/update/check")
        self.assertEqual(first["state"], "latest")
        self.get("/api/update/check")
        self.get("/api/update/check")
        self.assertEqual(self.fetches, 1)            # page reloads do not hit GitHub again
        self.get("/api/update/check?force=1")
        self.assertEqual(self.fetches, 2)            # the manual button always fetches
        self.get("/api/update/check")
        self.assertEqual(self.fetches, 2)

    def test_status_is_cached_between_polls(self):
        for _ in range(5):
            self.assertEqual(self.get("/api/status")["battery"], "80%")
        self.assertEqual(self.status_builds, 1)


if __name__ == "__main__":
    unittest.main()
