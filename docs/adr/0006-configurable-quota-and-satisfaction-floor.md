# Configurable Daily quota, gated like the Blocklist, with a satisfaction floor

The Daily quota becomes user-configurable from the menu bar, and the Day's "done"
condition gains a floor: done = `reviews >= quota` **OR** Anki has nothing left to
study.

## Editing the quota

Changing the quota follows the Blocklist rules (ADR-0005): the change rides the
daemon's request inbox as a `set_quota` request, and the daemon applies it only when
the Day is done. The new value takes effect the **next Day** - today's done-state is
sticky, so a change never re-blocks or unblocks the current Day. Both raising and
lowering are gated: lowering below your current review count would otherwise be a
one-click bypass, and raising is gated too so the rule is a single uniform "editable
only when done" (a change affects only future Days regardless, so free raising buys
nothing). Bounds: integers 1-999 (0 would mean the Block never engages).

## The satisfaction floor, and why it is required

Without a floor, a quota you cannot reach - a light day with few cards, or a quota
set above your daily card flow - leaves you stuck blocked. Worse, because edits are
gated on being "done," you could never reach the gate to *lower* the unreachable
quota: a deadlock. The floor breaks it: once Anki has nothing left to study you are
done, so any quota is reachable and adjustable.

"Nothing left" is the sum of `new + learn + review` across decks (AnkiConnect's
`getDeckStats`, which respects Anki's daily limits) reaching 0. The floor sets the
satisfied state, so edits stay gated on *genuine* done - never on an Emergency unlock.
It does not reintroduce the heavy-day punishment we rejected at the very start: when
cards exist you still owe only `quota`. This also retroactively fixes the original
fixed-quota tool, which had the same latent light-day lockout.

## Considered Options

- **Rigid rule, rely on the Emergency unlock when stuck.** Rejected: it punishes days
  you legitimately finished early, and does nothing for the edit deadlock.
- **Let raising the quota be free anytime** (by the ADR-0005 add/remove asymmetry).
  Rejected: a change only ever affects future Days, so free raising gains nothing, and
  one uniform gate is simpler to understand and to show in the menu.

## Consequences

- One extra `getDeckStats` call per tick.
- The menu bar gains a "Change daily quota…" item, gated and pre-filled like Blocklist
  removal.
