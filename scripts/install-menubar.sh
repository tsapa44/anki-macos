#!/bin/bash
# Install the optional menu-bar indicator as a per-user LaunchAgent.
#
#   scripts/install-menubar.sh        # NO sudo - this runs as you, in your GUI session
#
# Homebrew's Python is externally managed (PEP 668), so we install `rumps` into an
# isolated venv rather than system-wide. Run scripts/install.sh (the daemon) first.
# To remove the menu bar:
#   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.ankiblock.menubar.plist
#   rm ~/Library/LaunchAgents/com.ankiblock.menubar.plist
#   rm -rf ~/.ankiblock/venv
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "error: do NOT run this with sudo - the menu bar runs as you, not root" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# Where the daemon install put the package/config; override for dev runs.
LIBDIR="${ANKIBLOCK_LIBDIR:-/usr/local/lib/ankiblock}"
CONFIG="${ANKIBLOCK_CONFIG:-/usr/local/etc/ankiblock/config.json}"
BASE_PYTHON="${ANKIBLOCK_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
VENV="$HOME/.ankiblock/venv"
VENV_PYTHON="$VENV/bin/python"
AGENT="$HOME/Library/LaunchAgents/com.ankiblock.menubar.plist"
LOG="$HOME/Library/Logs/ankiblock-menubar.log"

if [[ ! -d "$LIBDIR/ankiblock" ]]; then
  echo "note: $LIBDIR/ankiblock not found - run scripts/install.sh first," >&2
  echo "      or set ANKIBLOCK_LIBDIR=$REPO to run the menu bar from this checkout." >&2
fi

echo "Creating venv + installing rumps -> $VENV"
"$BASE_PYTHON" -m venv "$VENV"
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet rumps

echo "Writing LaunchAgent -> $AGENT"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
# The venv python imports rumps (from the venv) and ankiblock (from PYTHONPATH=LIBDIR).
sed -e "s#__PYTHON__#$VENV_PYTHON#g" \
    -e "s#__LIBDIR__#$LIBDIR#g" \
    -e "s#__CONFIG__#$CONFIG#g" \
    -e "s#__LOG__#$LOG#g" \
    "$REPO/launchd/com.ankiblock.menubar.plist.template" > "$AGENT"

echo "Loading menu-bar agent"
launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT"

echo "Done. Look for the AnkiBlock item in your menu bar. Log: $LOG"
