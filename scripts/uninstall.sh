#!/bin/bash
# Remove the Anki Daily Blocker daemon and lift any active Block.
#
#   sudo scripts/uninstall.sh
#
# Stops the daemon, strips the Block region from /etc/hosts, and removes the plist
# and installed package. Config, state, and logs are left in place; delete them
# manually if you want a clean slate (see the paths printed at the end).
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: must run as root (use: sudo $0)" >&2
  exit 1
fi

LIBDIR="/usr/local/lib/ankiblock"
PLIST="/Library/LaunchDaemons/com.ankiblock.daemon.plist"

echo "Stopping daemon"
launchctl bootout system "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true

echo "Lifting any active Block (stripping /etc/hosts region)"
PYTHON="${ANKIBLOCK_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
if [[ -d "$LIBDIR/ankiblock" ]]; then
  PYTHONPATH="$LIBDIR" "$PYTHON" -c "from ankiblock.blocker import HostsBlocker; HostsBlocker('/etc/hosts').clear()" || true
fi

echo "Removing plist and package"
rm -f "$PLIST"
rm -rf "$LIBDIR"

echo
echo "Uninstalled. Left in place (delete manually if you want):"
echo "  config: /usr/local/etc/ankiblock"
echo "  state:  /usr/local/var/ankiblock"
echo "  logs:   /usr/local/var/log/ankiblock"
