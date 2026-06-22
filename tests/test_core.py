"""Logic tests - no root, no real /etc/hosts, no real Anki.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ankiblock.anki import AnkiClient, AnkiUnavailable
from ankiblock.blocker import HostsBlocker
from ankiblock.config import Config, normalize_domain
from ankiblock.daemon import Daemon, day_string
from ankiblock.menubar import lines_for, removal_enabled, title_for, write_request
from ankiblock.state import State

DAY1_10AM = datetime(2026, 6, 21, 10, 0, 0)  # after 4am cutoff -> day 2026-06-21
DAY2_10AM = datetime(2026, 6, 22, 10, 0, 0)


class FakeAnki:
    def __init__(self, count=None, fail=False, nothing_left=False):
        self.count = count
        self.fail = fail
        self.nothing_left = nothing_left

    def reviews_today(self):
        if self.fail:
            raise AnkiUnavailable("fake down")
        return self.count

    def nothing_left_today(self):
        if self.fail:
            raise AnkiUnavailable("fake down")
        return self.nothing_left


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

    def test_saved_state_is_world_readable(self):
        # On a real install root writes this; the user's status/menu bar must read it.
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "state.json")
        State(satisfied_day="2026-06-21").save(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertTrue(mode & 0o044, f"state must be group/other-readable, got {oct(mode)}")


class DaemonTickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cfg(self):
        return Config(
            state_path=os.path.join(self.tmp, "state.json"),
            hosts_path=os.path.join(self.tmp, "hosts"),
            requests_path=os.path.join(self.tmp, "requests"),
            flush_dns=False,
            daily_quota=20,
            emergency_delay_seconds=900,
            day_cutoff_hour=4,
            blocklist=["youtube.com"],
        )

    def _save_cfg(self, cfg):
        path = os.path.join(self.tmp, "config.json")
        cfg.save(path)
        os.makedirs(cfg.requests_path, exist_ok=True)
        return path

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

    # --- blocklist edit requests (ADR-0005) ---
    def test_add_request_appends_normalized_and_blocks_now(self):
        cfg = self._cfg()  # blocklist ["youtube.com"]
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "add", "https://www.Reddit.com/r/x")
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(fail=True), config_path=cfg_path)
        d.tick(now=DAY1_10AM)
        self.assertIn("reddit.com", Config.load(cfg_path).blocklist)  # persisted + normalized
        self.assertIn("0.0.0.0 reddit.com", Path(cfg.hosts_path).read_text())  # applied this tick
        self.assertEqual(os.listdir(cfg.requests_path), [])  # request consumed

    def test_remove_rejected_when_quota_not_met(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "remove", "youtube.com")
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(count=0), config_path=cfg_path)
        d.tick(now=DAY1_10AM)
        self.assertIn("youtube.com", Config.load(cfg_path).blocklist)  # NOT removed
        self.assertEqual(os.listdir(cfg.requests_path), [])  # dropped, never queued

    def test_remove_applied_when_quota_met(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "remove", "youtube.com")
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(count=20), config_path=cfg_path)
        d.tick(now=DAY1_10AM)
        self.assertNotIn("youtube.com", Config.load(cfg_path).blocklist)

    def test_add_request_is_idempotent(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "add", "youtube.com")  # already present
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(fail=True), config_path=cfg_path)
        d.tick(now=DAY1_10AM)
        self.assertEqual(Config.load(cfg_path).blocklist.count("youtube.com"), 1)

    def test_garbage_request_is_discarded_without_crashing(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        with open(os.path.join(cfg.requests_path, "junk.json"), "w") as f:
            f.write("{not valid json")
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(fail=True), config_path=cfg_path)
        d.tick(now=DAY1_10AM)  # must not raise
        self.assertEqual(os.listdir(cfg.requests_path), [])

    # --- satisfaction floor (ADR-0006) ---
    def test_floor_satisfies_below_quota_when_nothing_left(self):
        d = Daemon(self._cfg(), anki=FakeAnki(count=5, nothing_left=True))
        r = d.tick(now=DAY1_10AM)
        self.assertFalse(r["blocked"])  # done via floor at 5/20
        self.assertEqual(State.load(d.config.state_path).satisfied_day, "2026-06-21")

    def test_no_floor_when_cards_remain(self):
        d = Daemon(self._cfg(), anki=FakeAnki(count=5, nothing_left=False))
        r = d.tick(now=DAY1_10AM)
        self.assertTrue(r["blocked"])  # 5/20 and cards remain -> blocked

    # --- configurable quota (ADR-0006) ---
    def test_set_quota_applied_when_done(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "set_quota", value=30)
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(count=20), config_path=cfg_path)
        d.tick(now=DAY1_10AM)  # 20/20 -> done
        self.assertEqual(Config.load(cfg_path).daily_quota, 30)

    def test_set_quota_rejected_when_not_done(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "set_quota", value=5)
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(count=0), config_path=cfg_path)
        d.tick(now=DAY1_10AM)  # 0/20, not done -> reject (also blocks the bypass)
        self.assertEqual(Config.load(cfg_path).daily_quota, 20)

    def test_set_quota_out_of_range_rejected(self):
        cfg = self._cfg()
        cfg_path = self._save_cfg(cfg)
        write_request(cfg.requests_path, "set_quota", value=9999)
        d = Daemon(Config.load(cfg_path), anki=FakeAnki(count=20), config_path=cfg_path)
        d.tick(now=DAY1_10AM)
        self.assertEqual(Config.load(cfg_path).daily_quota, 20)  # unchanged


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

    def test_nothing_left_when_all_deck_counts_zero(self):
        self.server.replies["deckNames"] = {"result": ["Default"], "error": None}
        self.server.replies["getDeckStats"] = {
            "result": {"1": {"new_count": 0, "learn_count": 0, "review_count": 0}},
            "error": None,
        }
        self.assertTrue(self.client.nothing_left_today())

    def test_cards_left_when_a_count_is_positive(self):
        self.server.replies["deckNames"] = {"result": ["Default"], "error": None}
        self.server.replies["getDeckStats"] = {
            "result": {"1": {"new_count": 0, "learn_count": 0, "review_count": 7}},
            "error": None,
        }
        self.assertFalse(self.client.nothing_left_today())


class MenubarTest(unittest.TestCase):
    def _status(self, **over):
        base = dict(
            today="2026-06-21", anki_up=True, reviews=12, quota=20, blocked=True,
            satisfied_today=False, emergency_today=False, emergency_release_at=None,
            unlocks_total=0, blocklist=["youtube.com"],
        )
        base.update(over)
        return base

    def test_title_free(self):
        self.assertEqual(title_for(self._status(blocked=False)), "✅")

    def test_title_blocked_with_count(self):
        self.assertEqual(title_for(self._status(reviews=12)), "🔒 12/20")

    def test_title_blocked_anki_down_shows_question_mark(self):
        self.assertEqual(title_for(self._status(reviews=None)), "🔒 ?/20")

    def test_title_emergency_pending_counts_down(self):
        now = datetime(2026, 6, 21, 10, 0, 0)
        st = self._status(emergency_release_at=now.timestamp() + 600)
        self.assertEqual(title_for(st, now=now), "⏳ 10m")

    def test_lines_include_progress_and_unlock_count(self):
        rows = lines_for(self._status(reviews=5, unlocks_total=3))
        self.assertIn("Reviews today: 5/20", rows)
        self.assertIn("Block: ON", rows)
        self.assertIn("Emergency unlocks used: 3", rows)

    def test_removal_only_offered_when_quota_met(self):
        self.assertTrue(removal_enabled({"satisfied_today": True}))
        self.assertFalse(removal_enabled({"satisfied_today": False}))
        self.assertFalse(removal_enabled({}))


class NormalizeDomainTest(unittest.TestCase):
    def test_strips_scheme_path_query_and_www(self):
        self.assertEqual(normalize_domain("https://www.YouTube.com/feed?x=1"), "youtube.com")
        self.assertEqual(normalize_domain("  Reddit.com  "), "reddit.com")
        self.assertEqual(normalize_domain("http://x.com"), "x.com")

    def test_keeps_meaningful_subdomains(self):
        self.assertEqual(normalize_domain("web.telegram.org"), "web.telegram.org")

    def test_empty_input(self):
        self.assertEqual(normalize_domain(""), "")


if __name__ == "__main__":
    unittest.main()
