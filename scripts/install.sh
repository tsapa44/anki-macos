#!/bin/bash
# Install the Anki Daily Blocker as a root launchd daemon.
#
#   sudo scripts/install.sh
#
# This is the privileged, lockout-capable step. After this runs, the daemon starts
# at boot and re-applies the Block until you do your daily Reviews. Read the README
# before running. To remove it: sudo scripts/uninstall.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: must run as root (use: sudo $0)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

LIBDIR="/usr/local/lib/ankiblock"
CONFDIR="/usr/local/etc/ankiblock"
CONFIG="$CONFDIR/config.json"
VARDIR="/usr/local/var/ankiblock"
LOGDIR="/usr/local/var/log/ankiblock"
PLIST="/Library/LaunchDaemons/com.ankiblock.daemon.plist"

# Find a python3. Prefer Homebrew, fall back to PATH, then system.
PYTHON="${ANKIBLOCK_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for c in /opt/homebrew/bin/python3 "$(command -v python3 || true)" /usr/bin/python3; do
    if [[ -n "$c" && -x "$c" ]]; then PYTHON="$c"; break; fi
  done
fi
if [[ -z "$PYTHON" ]]; then echo "error: no python3 found" >&2; exit 1; fi
echo "Using python: $PYTHON"

echo "Installing package -> $LIBDIR"
mkdir -p "$LIBDIR" "$CONFDIR" "$VARDIR" "$VARDIR/requests" "$LOGDIR"
rm -rf "${LIBDIR:?}/ankiblock"
cp -R "$REPO/ankiblock" "$LIBDIR/ankiblock"

echo "Writing config (preserving your settings) -> $CONFIG"
# Idempotent migration: keep every recognised value the user already has (quota,
# blocklist, ...), fill in defaults for any newly-added fields, and drop keys we no
# longer recognise. Re-running install.sh therefore never resets your quota/blocklist.
PYTHONPATH="$LIBDIR" "$PYTHON" - "$CONFIG" <<'PY'
import dataclasses, json, sys
from ankiblock.config import Config

path = sys.argv[1]
known = {f.name for f in dataclasses.fields(Config)}
try:
    data = json.load(open(path))
except (FileNotFoundError, ValueError):
    data = {}
Config(**{k: v for k, v in data.items() if k in known}).save(path)
PY

echo "Rendering launchd plist -> $PLIST"
sed -e "s#__PYTHON__#$PYTHON#g" \
    -e "s#__LIBDIR__#$LIBDIR#g" \
    -e "s#__CONFIG__#$CONFIG#g" \
    -e "s#__LOGDIR__#$LOGDIR#g" \
    "$REPO/launchd/com.ankiblock.daemon.plist.template" > "$PLIST"

chown root:wheel "$PLIST"; chmod 644 "$PLIST"
chown -R root:wheel "$LIBDIR" "$CONFDIR" "$VARDIR"
# World-writable, sticky inbox: the user's menu bar drops requests here; the root
# daemon validates and applies them (ADR-0005), so this is not a hole.
chmod 1777 "$VARDIR/requests"

echo "Loading daemon"
launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"

echo
echo "Installed. The Block is now active until you complete your daily Reviews."
echo "  Status:  PYTHONPATH=$LIBDIR ANKIBLOCK_CONFIG=$CONFIG $PYTHON -m ankiblock status"
echo "  Logs:    $LOGDIR/daemon.err.log"
echo "Make sure the AnkiConnect add-on is installed in Anki (code 2055492159)."
