# Anki Daily Blocker

A macOS tool that withholds your chosen distractions until you've done your daily
Anki Reviews. A SelfControl-style block, but the timer is replaced by a condition:
**do your 20 Reviews and you're free.**

The design and the reasoning behind it live in [`CONTEXT.md`](./CONTEXT.md) (the
glossary) and [`docs/adr/`](./docs/adr) (the decisions). Read those first - they
explain *why* it works the way it does.

## How it works

A root `launchd` daemon wakes every ~30s and:

1. Asks Anki, via the **AnkiConnect** add-on, how many Reviews you've logged today.
2. If you've hit the **Daily quota** (default 20), it lifts the **Block**.
3. Otherwise it writes your **Blocklist** into `/etc/hosts` (pointed at `0.0.0.0`)
   and re-asserts it every tick, so editing the file back doesn't help.

If it can't reach Anki it **stays blocked** (fail closed). The one way out without
studying is a friction-gated **Emergency unlock**: a ~15-minute delayed release for
genuine lockouts. See [ADR-0003](./docs/adr/0003-friction-gated-emergency-unlock.md).

## Requirements

- macOS, desktop Anki, and the **AnkiConnect** add-on (Anki → Tools → Add-ons →
  Get Add-ons → code `2055492159`).
- Python 3 (stdlib only - no pip packages).

## Try it safely first (no root, no real /etc/hosts)

Point it at a sandbox so nothing on your system changes:

```bash
mkdir -p /tmp/ankiblock-sandbox
cat > /tmp/ankiblock-sandbox/config.json <<'JSON'
{ "hosts_path": "/tmp/ankiblock-sandbox/hosts",
  "state_path": "/tmp/ankiblock-sandbox/state.json",
  "flush_dns": false,
  "daily_quota": 20,
  "blocklist": ["youtube.com", "reddit.com"] }
JSON

ANKIBLOCK_CONFIG=/tmp/ankiblock-sandbox/config.json python3 -m ankiblock status
ANKIBLOCK_CONFIG=/tmp/ankiblock-sandbox/config.json python3 -m ankiblock tick
cat /tmp/ankiblock-sandbox/hosts   # see the Block region it would write
```

With Anki closed you'll see it fail closed and "block" the sandbox hosts file. Open
Anki (with AnkiConnect) and do 20 Reviews, run `tick` again, and the region clears.

## Install for real

This is the privileged step. It starts a daemon that blocks distractions until you
study, and survives reboots. Make sure you've read the ADRs.

```bash
sudo scripts/install.sh        # installs + starts the daemon
sudo scripts/uninstall.sh      # stops it and lifts any active Block
```

Edit your real Blocklist and quota in `/usr/local/etc/ankiblock/config.json`
(root-owned on purpose, so you can't quietly weaken it mid-block).

## Commands

```
python3 -m ankiblock status         # today's Reviews, Block state, unlock count
python3 -m ankiblock tick           # evaluate once and apply (the daemon loops this)
python3 -m ankiblock unlock         # start the ~15-min Emergency unlock
python3 -m ankiblock cancel-unlock  # changed your mind? cancel it
```

(After a real install, prefix with `PYTHONPATH=/usr/local/lib/ankiblock
ANKIBLOCK_CONFIG=/usr/local/etc/ankiblock/config.json`, and use `sudo` for
`unlock`/`tick` since state is root-owned.)

## Menu-bar indicator (optional)

A small menu-bar app shows your progress at a glance (`🔒 12/20` when blocked, `✅`
when free, `⏳ 8m` while an Emergency unlock counts down) and offers the unlock from a
dropdown. It is a separate user-space app - only it needs `rumps`; the daemon stays
stdlib-only.

From the **Blocklist ▸** submenu you can **add** a site at any time, but **removing**
one is greyed out until you've met today's quota ([ADR-0005](./docs/adr/0005-blocklist-editing-from-menu-bar.md)).
The menu bar only drops a request; the root daemon validates and applies it, so the
gate is enforced where it can't be clicked away.

```bash
scripts/install-menubar.sh     # no sudo: venv + rumps + a login LaunchAgent

# or run it once in the foreground, pointed at your sandbox. Homebrew's Python is
# externally managed (PEP 668), so rumps goes in a venv:
python3 -m venv /tmp/ankiblock-venv && /tmp/ankiblock-venv/bin/pip install rumps
ANKIBLOCK_CONFIG=/tmp/ankiblock-sandbox/config.json \
  PYTHONPATH="$PWD" /tmp/ankiblock-venv/bin/python -m ankiblock.menubar
```

## Honest limits

- **Not unbypassable.** With your own admin/sudo you can always defeat it (recovery
  mode, `sudo` editing root files). That's deliberate - see
  [ADR-0002](./docs/adr/0002-harden-but-not-selfcontrol-grade.md). The goal is to
  beat a *lazy moment*, not a determined you.
- **Hostname-level only.** DNS-over-HTTPS, a VPN, or hardcoded IPs can slip the
  `/etc/hosts` block ([ADR-0004](./docs/adr/0004-hosts-file-enforcement.md)).
- **The backlog can grow.** The quota enforces showing up, not draining the pile.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
