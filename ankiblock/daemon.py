"""The daemon: decide, each tick, whether the Block should be on, and enforce it.

Precedence on every tick (when not already free for the Day):
  1. Live read from AnkiConnect - studying ALWAYS wins and cancels a pending unlock.
  2. Otherwise, an Emergency unlock that has finished its delay frees the Day.
  3. Otherwise (quota not met, or Anki unavailable) - stay blocked. Fail closed.

The Day is anchored to Anki's cutoff hour, so "today" matches Anki (CONTEXT.md).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

from .anki import AnkiClient, AnkiUnavailable
from .blocker import HostsBlocker
from .config import Config, normalize_domain
from .state import State


def day_string(cutoff_hour: int, now: datetime) -> str:
    """The Anki-day `now` falls in, as an ISO date string."""
    return (now - timedelta(hours=cutoff_hour)).date().isoformat()


class Daemon:
    def __init__(self, config: Config, anki=None, blocker=None, config_path: str | None = None):
        self.config = config
        self._config_path = config_path  # set for the running daemon so edits persist
        self.anki = anki or AnkiClient(config.anki_connect_url)
        self.blocker = blocker or HostsBlocker(config.hosts_path, config.flush_dns)

    def _today(self, now: datetime) -> str:
        return day_string(self.config.day_cutoff_hour, now)

    # --- one evaluation ---------------------------------------------------
    def tick(self, now: datetime | None = None) -> dict:
        cfg = self.config
        now = now or datetime.now()
        nowts = now.timestamp()
        today = self._today(now)
        state = State.load(cfg.state_path)

        # Drop a pending Emergency unlock left over from a previous Day, so a stale
        # request can never auto-free today.
        if state.emergency_release_at is not None and state.emergency_requested_at is not None:
            req_day = self._today(datetime.fromtimestamp(state.emergency_requested_at))
            if req_day != today:
                state.emergency_release_at = None
                state.emergency_requested_at = None

        freed = state.satisfied_day == today or state.emergency_day == today
        reviews: int | None = None
        reason = "already free today" if freed else ""

        if not freed:
            # 1. Studying always wins - by hitting the quota, or by clearing everything
            #    Anki has left to study (the satisfaction floor, ADR-0006). Both set
            #    satisfied_day, so editing stays gated on genuine done, never emergency.
            try:
                reviews = self.anki.reviews_today()
                if reviews >= cfg.daily_quota:
                    state.satisfied_day = today
                    freed = True
                    reason = f"quota met ({reviews}/{cfg.daily_quota})"
                elif self.anki.nothing_left_today():
                    state.satisfied_day = today
                    freed = True
                    reason = f"nothing left to study ({reviews}/{cfg.daily_quota})"
                else:
                    reason = f"quota not met ({reviews}/{cfg.daily_quota})"
            except AnkiUnavailable as e:
                reason = f"anki unavailable, fail-closed ({e})"

            # 2. Emergency unlock, only if still blocked.
            if not freed and state.emergency_release_at is not None:
                if nowts >= state.emergency_release_at:
                    state.emergency_day = today
                    state.unlock_log.append(nowts)
                    state.emergency_release_at = None
                    state.emergency_requested_at = None
                    freed = True
                    reason = "emergency unlock released"
                else:
                    remaining = int(round(state.emergency_release_at - nowts))
                    reason = f"emergency unlock pending ({remaining}s left)"

        # Studying (or being already free) cancels any pending unlock request.
        if freed and state.emergency_release_at is not None:
            state.emergency_release_at = None
            state.emergency_requested_at = None

        # Apply pending Blocklist edits before enforcing: add always; remove only if
        # the quota was met today (ADR-0005). Root is the gate, not the menu bar.
        self._process_requests(quota_met=state.satisfied_day == today)

        changed = self.blocker.clear() if freed else self.blocker.apply(self.config.blocklist)
        state.save(cfg.state_path)

        return {
            "today": today,
            "blocked": not freed,
            "reason": reason,
            "reviews": reviews,
            "quota": cfg.daily_quota,
            "changed": changed,
            "emergency_release_at": state.emergency_release_at,
        }

    # --- blocklist edit requests (ADR-0005) ------------------------------
    def _process_requests(self, quota_met: bool) -> None:
        """Drain the request inbox. Adding to the Blocklist always applies; removing
        from it and changing the quota apply only when the quota was met today. The
        daemon (root) is the enforcement point, so a weakening request that arrives
        while blocked is simply discarded, never queued."""
        inbox = self.config.requests_path
        try:
            names = sorted(os.listdir(inbox))
        except OSError:
            return  # no inbox -> nothing to do
        changed = False
        for name in names:
            if not name.endswith(".json"):
                continue  # ignore in-progress ".json.tmp" writes
            path = os.path.join(inbox, name)
            try:
                with open(path) as f:
                    req = json.load(f)
                action = req["action"]
            except (OSError, ValueError, KeyError, TypeError):
                self._discard(path)
                continue
            if action == "add":
                domain = normalize_domain(req.get("domain", ""))
                if domain and domain not in self.config.blocklist:
                    self.config.blocklist.append(domain)
                    changed = True
            elif action == "remove":
                domain = normalize_domain(req.get("domain", ""))
                if quota_met and domain in self.config.blocklist:
                    self.config.blocklist.remove(domain)
                    changed = True
            elif action == "set_quota" and quota_met:
                n = self._valid_quota(req.get("value"))
                if n is not None and n != self.config.daily_quota:
                    self.config.daily_quota = n
                    changed = True
            self._discard(path)
        if changed and self._config_path:
            self.config.save(self._config_path)

    @staticmethod
    def _valid_quota(value) -> int | None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if 1 <= n <= 999 else None

    @staticmethod
    def _discard(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # --- emergency unlock control ----------------------------------------
    def request_emergency(self, now: datetime | None = None) -> dict:
        cfg = self.config
        now = now or datetime.now()
        nowts = now.timestamp()
        today = self._today(now)
        state = State.load(cfg.state_path)

        if state.satisfied_day == today or state.emergency_day == today:
            return {"status": "already_free"}
        if (
            state.emergency_release_at is not None
            and state.emergency_requested_at is not None
            and self._today(datetime.fromtimestamp(state.emergency_requested_at)) == today
        ):
            return {"status": "already_pending", "release_at": state.emergency_release_at}

        state.emergency_requested_at = nowts
        state.emergency_release_at = nowts + cfg.emergency_delay_seconds
        state.save(cfg.state_path)
        return {
            "status": "scheduled",
            "release_at": state.emergency_release_at,
            "delay_seconds": cfg.emergency_delay_seconds,
        }

    def cancel_emergency(self) -> dict:
        state = State.load(self.config.state_path)
        had = state.emergency_release_at is not None
        state.emergency_release_at = None
        state.emergency_requested_at = None
        state.save(self.config.state_path)
        return {"status": "cancelled" if had else "nothing_pending"}

    # --- read-only snapshot ----------------------------------------------
    def status(self, now: datetime | None = None) -> dict:
        cfg = self.config
        now = now or datetime.now()
        today = self._today(now)
        state = State.load(cfg.state_path)
        try:
            reviews: int | None = self.anki.reviews_today()
            anki_up = True
        except AnkiUnavailable:
            reviews, anki_up = None, False
        freed = state.satisfied_day == today or state.emergency_day == today
        return {
            "today": today,
            "anki_up": anki_up,
            "reviews": reviews,
            "quota": cfg.daily_quota,
            "blocked": not freed,
            "satisfied_today": state.satisfied_day == today,
            "emergency_today": state.emergency_day == today,
            "emergency_release_at": state.emergency_release_at,
            "unlocks_total": len(state.unlock_log),
            "blocklist": cfg.blocklist,
        }

    # --- run loop (launchd entrypoint) -----------------------------------
    def run(self) -> None:
        interval = self.config.poll_interval_seconds
        while True:
            try:
                self.tick()
            except Exception as e:  # never let a tick error kill the daemon
                print(f"[ankiblock] tick error: {e}", file=sys.stderr, flush=True)
            time.sleep(interval)
