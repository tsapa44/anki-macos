"""The daemon: decide, each tick, whether the Block should be on, and enforce it.

Precedence on every tick (when not already free for the Day):
  1. Live read from AnkiConnect - studying ALWAYS wins and cancels a pending unlock.
  2. Otherwise, an Emergency unlock that has finished its delay frees the Day.
  3. Otherwise (quota not met, or Anki unavailable) - stay blocked. Fail closed.

The Day is anchored to Anki's cutoff hour, so "today" matches Anki (CONTEXT.md).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from .anki import AnkiClient, AnkiUnavailable
from .blocker import HostsBlocker
from .config import Config
from .state import State


def day_string(cutoff_hour: int, now: datetime) -> str:
    """The Anki-day `now` falls in, as an ISO date string."""
    return (now - timedelta(hours=cutoff_hour)).date().isoformat()


class Daemon:
    def __init__(self, config: Config, anki=None, blocker=None):
        self.config = config
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
            # 1. Studying always wins.
            try:
                reviews = self.anki.reviews_today()
                if reviews >= cfg.daily_quota:
                    state.satisfied_day = today
                    freed = True
                    reason = f"quota met ({reviews}/{cfg.daily_quota})"
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

        changed = self.blocker.clear() if freed else self.blocker.apply(cfg.blocklist)
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
