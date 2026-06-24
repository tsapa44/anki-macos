"""Optional menu-bar indicator (user-space app).

Shows today's Review progress and the Block state in the macOS menu bar, and lets
you start the Emergency unlock without a terminal. This is NOT the daemon - it only
READS status (config and state are world-readable; AnkiConnect is on localhost) and,
for the unlock, writes state or prompts for admin in a real install.

The daemon stays dependency-free; only this app needs `rumps`:
    python3 -m pip install rumps
    python3 -m ankiblock.menubar

The formatting helpers below are pure and unit-tested; `rumps` is imported lazily in
main() so this module imports fine (and is testable) without it.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import sys
import tempfile
import time
from datetime import datetime

from .config import DEFAULT_CONFIG_PATH, Config, normalize_domain
from .daemon import Daemon


def title_for(status: dict, now: datetime | None = None) -> str:
    """The short menu-bar title that summarises state at a glance."""
    if not status["blocked"]:
        return "✅"
    release = status.get("emergency_release_at")
    if release is not None:
        now = now or datetime.now()
        mins = max(1, math.ceil((release - now.timestamp()) / 60))
        return f"⏳ {mins}m"
    reviews = status["reviews"]
    count = "?" if reviews is None else reviews
    return f"🔒 {count}/{status['quota']}"


def lines_for(status: dict) -> list[str]:
    """The detail rows shown in the dropdown."""
    reviews = "?" if status["reviews"] is None else status["reviews"]
    rows = [
        f"Reviews today: {reviews}/{status['quota']}",
        f"Anki: {'connected' if status['anki_up'] else 'not running'}",
        f"Block: {'ON' if status['blocked'] else 'off'}",
    ]
    release = status.get("emergency_release_at")
    if release is not None and status["blocked"]:
        when = datetime.fromtimestamp(release).strftime("%H:%M")
        rows.append(f"Emergency unlock releases at {when}")
    rows.append(f"Emergency unlocks used: {status['unlocks_total']}")
    return rows


def _admin_unlock_command(config_path: str) -> str:
    """The shell command to run `unlock` with the package importable."""
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return (
        f"PYTHONPATH={shlex.quote(pkg_parent)} "
        f"ANKIBLOCK_CONFIG={shlex.quote(config_path)} "
        f"{shlex.quote(sys.executable)} -m ankiblock unlock"
    )


def removal_enabled(status: dict) -> bool:
    """Removal from the Blocklist is offered only once the quota is met today (ADR-0005)."""
    return bool(status.get("satisfied_today"))


def apply_overlay(blocklist, quota, pending_add, pending_remove, pending_quota):
    """What the menu should DISPLAY: the daemon's config plus the user's not-yet-applied
    changes, so the dropdown reflects an edit instantly (optimistic UI)."""
    effective = [d for d in blocklist if d not in pending_remove]
    for d in pending_add:
        if d not in effective:
            effective.append(d)
    return effective, (pending_quota if pending_quota is not None else quota)


def prune_overlay(blocklist, quota, pending_add, pending_remove, pending_quota):
    """Drop overlay entries the daemon has now applied (config caught up), so the
    optimistic state self-heals back to the source of truth."""
    pending_add = {d for d in pending_add if d not in blocklist}
    pending_remove = {d for d in pending_remove if d in blocklist}
    if pending_quota is not None and quota == pending_quota:
        pending_quota = None
    return pending_add, pending_remove, pending_quota


def write_request(inbox: str, action: str, domain: str | None = None,
                  value: int | None = None) -> str:
    """Drop a request for the daemon (add/remove a site, or set_quota), written
    atomically as *.json so the daemon never reads a half-written file. The daemon
    validates and applies it."""
    payload: dict = {"action": action}
    if domain is not None:
        payload["domain"] = domain
    if value is not None:
        payload["value"] = value
    os.makedirs(inbox, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=inbox, prefix="req-", suffix=".json.tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    final = tmp[:-4]  # ".json.tmp" -> ".json"
    os.replace(tmp, final)
    return final


def main() -> None:
    import rumps  # lazy: only needed to actually run the bar

    config_path: str = os.environ.get("ANKIBLOCK_CONFIG", DEFAULT_CONFIG_PATH)
    config = Config.load(config_path)
    daemon = Daemon(config)

    class BlockerBar(rumps.App):
        def __init__(self):
            super().__init__("AnkiBlock", title="…", quit_button=None)
            # Optimistic overlay: changes the user made that the daemon hasn't applied yet.
            self._pending_add: set = set()
            self._pending_remove: set = set()
            self._pending_quota = None
            self._pending_since = 0.0
            self.timer = rumps.Timer(self._tick, max(5, config.poll_interval_seconds))
            self.timer.start()
            self._tick(None)

        @staticmethod
        def _to_front():
            # A menu-bar (accessory) app doesn't auto-focus its dialogs, so an alert or
            # input box opens behind the active app. Activate first so it comes forward.
            from AppKit import NSApplication
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        def _tick(self, _):
            try:
                live = Config.load(config_path)  # re-read so shown settings are current
                daemon.config = live
                # Self-heal the optimistic overlay: drop changes the daemon has applied,
                # and clear anything that never landed within a grace window.
                self._pending_add, self._pending_remove, self._pending_quota = prune_overlay(
                    live.blocklist, live.daily_quota,
                    self._pending_add, self._pending_remove, self._pending_quota)
                if (self._pending_add or self._pending_remove or self._pending_quota is not None) \
                        and time.time() - self._pending_since > 20:
                    self._pending_add, self._pending_remove, self._pending_quota = set(), set(), None
                status = daemon.status()
                self.title = title_for(status)
                rows = lines_for(status)
                offer_unlock = status["blocked"] and status.get("emergency_release_at") is None
                inbox, can_edit = live.requests_path, removal_enabled(status)
                blocklist, quota = apply_overlay(
                    list(live.blocklist), live.daily_quota,
                    self._pending_add, self._pending_remove, self._pending_quota)
            except Exception as e:  # never let a refresh crash the bar
                self.title = "⚠️"
                rows, offer_unlock = [f"error: {e}"], False
                blocklist, inbox, quota, can_edit = [], None, None, False

            self.menu.clear()
            for line in rows:
                self.menu.add(rumps.MenuItem(line))  # no callback => info-only row
            self.menu.add(rumps.separator)
            self.menu.add(self._blocklist_menu(blocklist, inbox, can_edit))
            qlabel = f"Change daily quota… ({quota})" if quota is not None else "Change daily quota…"
            self.menu.add(rumps.MenuItem(qlabel, callback=self._make_set_quota(inbox, quota))
                          if (can_edit and inbox and quota is not None) else rumps.MenuItem(qlabel))
            self.menu.add(rumps.separator)
            if offer_unlock:
                self.menu.add(rumps.MenuItem("Emergency unlock", callback=self._unlock))
            self.menu.add(rumps.MenuItem("Refresh now", callback=self._tick))
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

        def _blocklist_menu(self, blocklist, inbox, can_remove):
            # A "Blocklist" submenu: Add is always live; removing is greyed (no callback
            # => AppKit disables it) until the quota is met. The daemon is the real gate.
            sub = rumps.MenuItem("Blocklist")
            sub.add(rumps.MenuItem("Add site…", callback=self._make_add(inbox))
                    if inbox else rumps.MenuItem("Add site…"))
            sub.add(rumps.separator)
            if not blocklist:
                sub.add(rumps.MenuItem("(empty)"))
            elif not can_remove:
                sub.add(rumps.MenuItem("Finish your Reviews to remove"))
            for domain in blocklist:
                sub.add(rumps.MenuItem(domain, callback=self._make_remove(inbox, domain))
                        if (can_remove and inbox) else rumps.MenuItem(domain))
            return sub

        def _make_add(self, inbox):
            def callback(_):
                self._to_front()
                win = rumps.Window(
                    message="Site to block (e.g. youtube.com)", title="Add to blocklist",
                    ok="Add", cancel="Cancel", default_text="", dimensions=(220, 24),
                )
                resp = win.run()
                if resp.clicked and resp.text.strip():
                    write_request(inbox, "add", resp.text.strip())
                    d = normalize_domain(resp.text)
                    self._pending_add.add(d)
                    self._pending_remove.discard(d)
                    self._pending_since = time.time()
                    self._tick(None)
            return callback

        def _make_remove(self, inbox, domain):
            def callback(_):
                self._to_front()
                if rumps.alert(
                    title="Stop blocking this site?",
                    message=f"{domain} won't be blocked anymore. Add it again to re-block it.",
                    ok="Remove", cancel="Cancel",
                ) == 1:
                    write_request(inbox, "remove", domain)
                    self._pending_remove.add(domain)
                    self._pending_add.discard(domain)
                    self._pending_since = time.time()
                    self._tick(None)
            return callback

        def _make_set_quota(self, inbox, current):
            def callback(_):
                self._to_front()
                win = rumps.Window(
                    message="Reviews required per day (1-999). Applies tomorrow.",
                    title="Daily quota", ok="Set", cancel="Cancel",
                    default_text=str(current), dimensions=(120, 24),
                )
                resp = win.run()
                if not resp.clicked:
                    return
                text = resp.text.strip()
                if text.isdigit() and 1 <= int(text) <= 999:
                    write_request(inbox, "set_quota", value=int(text))
                    self._pending_quota = int(text)
                    self._pending_since = time.time()
                    self._tick(None)
                else:
                    self._to_front()
                    rumps.alert("AnkiBlock", "Enter a whole number from 1 to 999.")
            return callback

        def _unlock(self, _):
            self._to_front()
            try:
                res = daemon.request_emergency()
            except (PermissionError, OSError):
                # Root-owned state in a real install: prompt for admin via the CLI.
                script = (
                    f'do shell script {shlex.quote(_admin_unlock_command(config_path))} '
                    f"with administrator privileges"
                )
                import subprocess

                subprocess.run(["osascript", "-e", script], capture_output=True)
                self._tick(None)
                return
            except Exception as e:
                rumps.alert("AnkiBlock", f"Could not start unlock: {e}")
                return
            if res.get("status") == "scheduled":
                mins = res["delay_seconds"] // 60
                rumps.alert(
                    "AnkiBlock",
                    f"Emergency unlock started. The Block lifts in ~{mins} min. "
                    "Do your Reviews and it cancels itself.",
                )
            self._tick(None)

    BlockerBar().run()


if __name__ == "__main__":
    main()
