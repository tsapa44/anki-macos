# Editing the Blocklist from the menu bar: add freely, remove only after the quota, daemon-enforced

The menu bar may **add** to the Blocklist at any time, but may **remove** an entry
only after the Daily quota has been met that Day (not when freed by an Emergency
unlock). Enforcement lives with root: the user-space menu bar never writes the
root-owned config directly.

## Why the asymmetry

Removing a site weakens the Block, which is a *bypass* - functionally identical to
unblocking it. A free one-click removal would be a cheaper bypass than the Emergency
unlock we deliberately made expensive (ADR-0003), gutting ADR-0002. Gating removal on
"quota actually met" means you can only curate the list *down* once you've already
done the work - so it can never serve as a lazy-moment escape. By then the Block is
already off, so removal grants no access today; it only shapes future Days. Adding
only ever strengthens the Block, so it carries no risk and stays frictionless.

## Why the daemon enforces it

The user-space menu bar cannot be trusted to enforce the rule (that is the whole
reason the config is root-owned, ADR-0002). So the menu bar drops `add` / `remove`
requests into a world-writable inbox that the root daemon polls each tick; the daemon
validates each request (add always; remove only if `satisfied_day == today`) and
applies it to its own config and in-memory Blocklist. A "remove while blocked" request
is simply rejected by root, so the world-writable inbox is **not** a hole.

## Considered Options

- **Menu bar writes the config via an admin-password prompt** (the `osascript` path the
  Emergency unlock falls back to). Rejected: it puts a password on every harmless add,
  and enforces the quota rule only client-side - exactly the user-space trust ADR-0002
  withholds.

## Consequences

- A new request-inbox channel between the menu bar and the daemon.
- Edits take effect within one poll interval (~30s), not instantly.
- The menu bar greys out "remove" until the quota is met as a UX hint, but the daemon
  is the real gate - the greying is not where the security lives.
