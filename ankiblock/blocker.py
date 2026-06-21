"""Enforcement: the Block, implemented over /etc/hosts (ADR-0004).

The Blocklist domains are written into a clearly marked region of the hosts file,
pointed at 0.0.0.0. Everything outside the markers is left untouched. Writes are
atomic and idempotent - re-applying an identical Block is a no-op (so we don't
flush DNS on every tick). The DNS cache is flushed only when the file changes.

The class is the swap point for a future `pf` backend: anything with
apply()/clear()/is_blocked() can replace it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

START_MARKER = "# >>> ankiblock start >>>"
END_MARKER = "# <<< ankiblock end <<<"


class HostsBlocker:
    def __init__(self, hosts_path: str = "/etc/hosts", flush_dns: bool = True):
        self.hosts_path = hosts_path
        self.flush_dns = flush_dns

    # --- public API -------------------------------------------------------
    def is_blocked(self) -> bool:
        return START_MARKER in self._read()

    def apply(self, domains) -> bool:
        """Ensure the Block region lists exactly `domains`. Returns True if changed."""
        current = self._read()
        desired = self._render(current, domains)
        if current == desired:
            return False
        self._write(desired)
        return True

    def clear(self) -> bool:
        """Remove the Block region. Returns True if anything was removed."""
        current = self._read()
        if START_MARKER not in current:
            return False
        self._write(self._strip_region(current))
        return True

    # --- internals --------------------------------------------------------
    def _read(self) -> str:
        try:
            with open(self.hosts_path) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    @staticmethod
    def _strip_region(text: str) -> str:
        out, inside = [], False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == START_MARKER:
                inside = True
                continue
            if stripped == END_MARKER:
                inside = False
                continue
            if not inside:
                out.append(line)
        body = "\n".join(out).rstrip("\n")
        return body + "\n" if body else ""

    @staticmethod
    def _expand(domains) -> list[str]:
        """Each domain plus its www. variant (hosts has no wildcards), deduped."""
        hosts, seen = [], set()
        for raw in domains:
            d = raw.strip().lower()
            if not d:
                continue
            for h in (d, "" if d.startswith("www.") else "www." + d):
                if h and h not in seen:
                    seen.add(h)
                    hosts.append(h)
        return hosts

    def _render(self, current: str, domains) -> str:
        base = self._strip_region(current).rstrip("\n")
        region = "\n".join(
            [START_MARKER, *(f"0.0.0.0 {h}" for h in self._expand(domains)), END_MARKER]
        )
        return f"{base}\n\n{region}\n" if base else f"{region}\n"

    def _write(self, text: str) -> None:
        directory = os.path.dirname(self.hosts_path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hosts-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.hosts_path)  # atomic
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._flush()

    def _flush(self) -> None:
        if not self.flush_dns:
            return
        for cmd in (["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]):
            try:
                subprocess.run(cmd, check=False, capture_output=True)
            except FileNotFoundError:
                pass
