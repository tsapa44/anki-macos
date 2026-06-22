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

import math
import os
import shlex
import sys
from datetime import datetime

from .config import DEFAULT_CONFIG_PATH, Config
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


def main() -> None:
    import rumps  # lazy: only needed to actually run the bar

    config_path: str = os.environ.get("ANKIBLOCK_CONFIG", DEFAULT_CONFIG_PATH)
    config = Config.load(config_path)
    daemon = Daemon(config)

    class BlockerBar(rumps.App):
        def __init__(self):
            super().__init__("AnkiBlock", title="…", quit_button=None)
            self.timer = rumps.Timer(self._tick, max(5, config.poll_interval_seconds))
            self.timer.start()
            self._tick(None)

        def _tick(self, _):
            try:
                status = daemon.status()
                self.title = title_for(status)
                rows = lines_for(status)
                offer_unlock = status["blocked"] and status.get("emergency_release_at") is None
            except Exception as e:  # never let a refresh crash the bar
                self.title = "⚠️"
                rows, offer_unlock = [f"error: {e}"], False

            self.menu.clear()
            for line in rows:
                self.menu.add(rumps.MenuItem(line))  # no callback => info-only row
            self.menu.add(rumps.separator)
            if offer_unlock:
                self.menu.add(rumps.MenuItem("Emergency unlock", callback=self._unlock))
            self.menu.add(rumps.MenuItem("Refresh now", callback=self._tick))
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

        def _unlock(self, _):
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
