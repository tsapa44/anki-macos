# Fail closed, but provide a friction-gated Emergency unlock

When the tool cannot confirm the Daily quota (AnkiConnect down, add-on broken, Anki
won't open), the Block stays on (fail closed). But there is always one sanctioned
exit: an **Emergency unlock** that lifts the Block without meeting the quota after a
deliberate delay (starting value ~15 minutes). Each use is logged.

## Why this is not a contradiction with ADR-0002

SelfControl can safely offer *zero* escape because it is **time-boxed** (≤24h, the
user knows it ends). This tool is **indefinite** (in force until the quota is met)
and depends on a **fragile external API** (AnkiConnect). An indefinite block with a
fragile unlock condition and no relief valve is genuinely dangerous - a single
add-on bug could brick the machine even after the user studied correctly.

So fail-open is rejected (quitting Anki would become a trivial loophole) and
pure-fail-closed is rejected (catastrophic lockout). The Emergency unlock threads
the needle: the delay is calibrated so that *doing the 20 Reviews is almost always
the faster path*, preserving the deterrent, while guaranteeing an exit for genuine
lockouts and real emergencies.

## Considered Options

- **Effort gate** (type a long random string, Cold Turkey style) - rejected as
  primary: clever but gameable by muscle memory over time.
- **Hard cap** (N unlocks per month) - rejected: a hard limit re-introduces the
  lockout risk it was meant to remove.

Accountability comes from *logging and surfacing* unlock usage, not from blocking it.

## Note: the Block is machine-bound

The Block exists only on the laptop, so it constrains the user only while they are
*at* the laptop - and whenever they are, Anki is available to study. Being away
(travel without the machine) is therefore not a lockout vector, which is why no
separate "rest day" / "vacation" mode is needed: the Emergency unlock only has to
cover tool failure and genuine emergencies, not absence.
