"""Logic tests - no root, no real /etc/hosts, no real Anki.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ankiblock.anki import AnkiClient, AnkiUnavailable
from ankiblock.blocker import HostsBlocker
from ankiblock.config import Config
from ankiblock.daemon import Daemon, day_string
from ankiblock.state import State

DAY1_10AM = datetime(2026, 6, 21, 10, 0, 0)  # after 4am cutoff -> day 2026-06-21
DAY2_10AM = datetime(2026, 6, 22, 10, 0, 0)


class FakeAnki:
    def __init__(self, count=None, fail=False):
        self.count = count
        self.fail = fail

    def reviews_today(self):
        if self.fail:
            raise AnkiUnavailable("fake down")
        return self.count


class DayStringTest(unittest.TestCase):
    def test_rollover_at_cutoff(self):
        self.assertEqual(day_string(4, datetime(2026, 6, 21, 2, 0)), "2026-06-20")
        self.assertEqual(day_string(4, datetime(2026, 6, 21, 4, 0)), "2026-06-21")
        self.assertEqual(day_string(4, datetime(2026, 6, 21, 23, 0)), "2026-06-21")


class HostsBlockerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hosts = os.path.join(self.tmp, "hosts")
        with open(self.hosts, "w") as f:
            f.write("127.0.0.1 localhost\n")
        self.b = HostsBlocker(self.hosts, flush_dns=False)

    def test_apply_preserves_and_blocks(self):
        changed = self.b.apply(["youtube.com"])
        self.assertTrue(changed)
        text = Path(self.hosts).read_text()
        self.assertIn("127.0.0.1 localhost", text)
        self.assertIn("0.0.0.0 youtube.com", text)
        self.assertIn("0.0.0.0 www.youtube.com", text)
        self.assertTrue(self.b.is_blocked())

    def test_apply_is_idempotent(self):
        self.assertTrue(self.b.apply(["youtube.com"]))
        self.assertFalse(self.b.apply(["youtube.com"]))  # no change second time

    def test_clear_removes_region_only(self):
        self.b.apply(["youtube.com"])
        self.assertTrue(self.b.clear())
        text = Path(self.hosts).read_text()
        self.assertIn("127.0.0.1 localhost", text)
        self.assertNotIn("youtube.com", text)
        self.assertFalse(self.b.is_blocked())
        self.assertFalse(self.b.clear())  # nothing left to remove


class StateTest(unittest.TestCase):
    def test_roundtrip(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "state.json")
        s = State(satisfied_day="2026-06-21", unlock_log=[1.0, 2.0])
        s.save(path)
        loaded = State.load(path)
        self.assertEqual(loaded.satisfied_day, "2026-06-21")
        self.assertEqual(loaded.unlock_log, [1.0, 2.0])

    def test_missing_file_is_empty_state(self):
        loaded = State.load("/nonexistent/state.json")
        self.assertIsNone(loaded.satisfied_day)


class DaemonTickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg(self):
        return Config(
            state_path=os.path.join(self.tmp, "state.json"),
            hosts_path=os.path.join(self.tmp, "hosts"),
            flush_dns=False,
            daily_quota=20,
            emergency_delay_seconds=900,
            day_cutoff_hour=4,
            blocklist=["youtube.com"],
        )

    def test_quota_met_unblocks(self):
        d = Daemon(self._cfg(), anki=FakeAnki(count=20))
        r = d.tick(now=DAY1_10AM)
        self.assertFalse(r["blocked"])
        self.assertEqual(State.load(d.config.state_path).satisfied_day, "2026-06-21")

    def test_quota_not_met_blocks(self):
        d = Daemon(self._cfg(), anki=FakeAnki(count=5))
        r = d.tick(now=DAY1_10AM)
        self.assertTrue(r["blocked"])
        self.assertTrue(d.blocker.is_blocked())

    def test_anki_unavailable_fails_closed(self):
        d = Daemon(self._cfg(), anki=FakeAnki(fail=True))
        r = d.tick(now=DAY1_10AM)
        self.assertTrue(r["blocked"])
        self.assertIn("fail-closed", r["reason"])

    def test_cached_satisfaction_survives_anki_going_down(self):
        cfg = self._cfg()
        Daemon(cfg, anki=FakeAnki(count=20)).tick(now=DAY1_10AM)  # meets quota
        # Later same day, Anki is down - must stay free from the cached flag.
        d2 = Daemon(cfg, anki=FakeAnki(fail=True))
        r = d2.tick(now=datetime(2026, 6, 21, 18, 0))
        self.assertFalse(r["blocked"])

    def test_new_day_reblocks(self):
        cfg = self._cfg()
        Daemon(cfg, anki=FakeAnki(count=20)).tick(now=DAY1_10AM)
        d2 = Daemon(cfg, anki=FakeAnki(count=0))
        r = d2.tick(now=DAY2_10AM)
        self.assertTrue(r["blocked"])

    def test_emergency_unlock_releases_after_delay(self):
        cfg = self._cfg()
        d = Daemon(cfg, anki=FakeAnki(fail=True))
        d.request_emergency(now=DAY1_10AM)
        # Before the delay elapses: still blocked.
        early = d.tick(now=datetime(2026, 6, 21, 10, 5))  # +5 min < 15
        self.assertTrue(early["blocked"])
        self.assertIn("pending", early["reason"])
        # After the delay: freed and logged.
        late = d.tick(now=datetime(2026, 6, 21, 10, 20))  # +20 min > 15
        self.assertFalse(late["blocked"])
        st = State.load(cfg.state_path)
        self.assertEqual(st.emergency_day, "2026-06-21")
        self.assertEqual(len(st.unlock_log), 1)

    def test_studying_cancels_pending_emergency(self):
        cfg = self._cfg()
        d = Daemon(cfg, anki=FakeAnki(fail=True))
        d.request_emergency(now=DAY1_10AM)
        # User studies before the delay elapses - quota wins, unlock is cancelled.
        d.anki = FakeAnki(count=20)
        r = d.tick(now=datetime(2026, 6, 21, 10, 5))
        self.assertFalse(r["blocked"])
        st = State.load(cfg.state_path)
        self.assertIsNone(st.emergency_release_at)
        self.assertEqual(len(st.unlock_log), 0)  # never counted as an unlock

    def test_stale_emergency_from_prior_day_is_dropped(self):
        cfg = self._cfg()
        d = Daemon(cfg, anki=FakeAnki(fail=True))
        d.request_emergency(now=DAY1_10AM)  # requested on day 1, never released
        r = d.tick(now=DAY2_10AM)  # next day, Anki still down
        self.assertTrue(r["blocked"])  # stale request must NOT free day 2
        self.assertIsNone(State.load(cfg.state_path).emergency_release_at)


class _FakeAnkiServer(ThreadingHTTPServer):
    replies: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length))
        reply = self.server.replies.get(req["action"], {"result": None, "error": "no"})
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet


class AnkiClientHttpTest(unittest.TestCase):
    """Validate the real HTTP/JSON path against a stand-in AnkiConnect server."""

    def setUp(self):
        self.server = _FakeAnkiServer(("127.0.0.1", 0), _Handler)
        self.server.replies = {}
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = AnkiClient(f"http://127.0.0.1:{self.port}", timeout=2.0)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_reads_review_count(self):
        self.server.replies["getNumCardsReviewedToday"] = {"result": 42, "error": None}
        self.assertEqual(self.client.reviews_today(), 42)

    def test_ankiconnect_error_raises_unavailable(self):
        self.server.replies["getNumCardsReviewedToday"] = {"result": None, "error": "boom"}
        with self.assertRaises(AnkiUnavailable):
            self.client.reviews_today()

    def test_connection_refused_raises_unavailable(self):
        dead = AnkiClient("http://127.0.0.1:1", timeout=1.0)
        with self.assertRaises(AnkiUnavailable):
            dead.reviews_today()


if __name__ == "__main__":
    unittest.main()
