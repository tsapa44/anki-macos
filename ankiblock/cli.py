"""Command-line interface.

  ankiblock run            run the daemon loop (this is what launchd calls)
  ankiblock tick           evaluate once and apply; print the result
  ankiblock status         show today's progress and Block state (read-only)
  ankiblock unlock         start an Emergency unlock (writes state; needs root in a real install)
  ankiblock cancel-unlock  cancel a pending Emergency unlock

Global: --config PATH  (defaults to $ANKIBLOCK_CONFIG or the installed location)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .config import Config
from .daemon import Daemon


def _fmt_clock(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def _print_status(d: Daemon) -> None:
    s = d.status()
    reviews = "?" if s["reviews"] is None else s["reviews"]
    print(f"Day:        {s['today']}  (Anki {'up' if s['anki_up'] else 'DOWN'})")
    print(f"Reviews:    {reviews} / {s['quota']}")
    print(f"Block:      {'ON' if s['blocked'] else 'off'}")
    if s["satisfied_today"]:
        print("            quota met today")
    if s["emergency_today"]:
        print("            freed today via emergency unlock")
    if s["emergency_release_at"] is not None:
        print(f"            emergency unlock pending, releases at {_fmt_clock(s['emergency_release_at'])}")
    print(f"Unlocks:    {s['unlocks_total']} total")
    print(f"Blocklist:  {', '.join(s['blocklist'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ankiblock", description="Anki Daily Blocker")
    parser.add_argument("--config", help="path to config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "tick", "status", "unlock", "cancel-unlock"):
        sub.add_parser(name)

    args = parser.parse_args(argv)
    config = Config.load(args.config)
    daemon = Daemon(config)

    if args.command == "run":
        daemon.run()
        return 0

    if args.command == "tick":
        result = daemon.tick()
        state = "BLOCKED" if result["blocked"] else "free"
        print(f"[{result['today']}] {state}: {result['reason']}"
              f"{' (hosts changed)' if result['changed'] else ''}")
        return 0

    if args.command == "status":
        _print_status(daemon)
        return 0

    if args.command == "unlock":
        res = daemon.request_emergency()
        if res["status"] == "already_free":
            print("Already free today - nothing to unlock.")
        elif res["status"] == "already_pending":
            print(f"Emergency unlock already pending, releases at {_fmt_clock(res['release_at'])}.")
        else:
            mins = res["delay_seconds"] // 60
            print(f"Emergency unlock scheduled. Block lifts at {_fmt_clock(res['release_at'])} "
                  f"(in ~{mins} min). Or just do your Reviews and it cancels itself.")
        return 0

    if args.command == "cancel-unlock":
        res = daemon.cancel_emergency()
        print("Cancelled pending emergency unlock." if res["status"] == "cancelled"
              else "No emergency unlock was pending.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
