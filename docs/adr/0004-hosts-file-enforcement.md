# Enforce the Block by editing /etc/hosts

The daemon enforces the Block by writing `0.0.0.0` entries for the Blocklist domains
into a marked region of `/etc/hosts` (and flushing the DNS cache), rather than using
the `pf` packet filter the way SelfControl does. The blocking mechanism sits behind a
small interface so `pf` can replace it later without touching the daemon logic.

## Why

- `/etc/hosts` is dead simple, atomic to write, trivial to test (point the code at a
  temp file), and easy to fully reverse (strip the marked region).
- `pf` blocks by IP, which means resolving domains to IPs and chasing CDN/IP churn -
  much more code and more ways to fail, for a tool whose threat model is "beat a lazy
  moment," not "stop a determined adversary" (see ADR-0002).

## Consequences

- Hostname-level only: an app using hardcoded IPs, a VPN, or DNS-over-HTTPS in the
  browser can slip past. Accepted for the MVP - that is deliberate effort, not a lazy
  reflex. If a specific escape becomes a habit, that domain/app gets handled then, or
  we swap in the `pf` backend behind the same interface.
- The daemon re-asserts the region every tick, so manual edits to `/etc/hosts` are
  undone within one poll interval.
