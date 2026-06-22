# Anki Daily Blocker

A tool that withholds the user's chosen distractions until they have completed a
fixed amount of Anki studying that day. The point is to make a *lazy* person show
up to study daily by attaching a cost to not doing so.

## Language

**Review**:
One answer event in Anki - a single press of an answer button, as recorded in
Anki's review log. This is the unit the tool counts.
_Avoid_: "card" (as a unit of work), "rep".

**Card**:
A single question/answer prompt Anki schedules. One Card can be Reviewed many
times in a day (failures and learning steps each produce a Review), so Cards and
Reviews are not 1:1.
_Avoid_: using "card" to mean a Review.

**Note**:
The underlying fact a user enters; one Note can generate multiple Cards. Mostly
out of scope here, listed only to keep it distinct from Card.

**Daily quota**:
The number of Reviews that must be completed for the Block to lift that Day
(default **20**), set by the user. Independent of Anki's due pile - except that the
Block also lifts once Anki has nothing left to study, so the quota can never demand
more Reviews than exist.
_Avoid_: "goal", "target", "limit".

**Block**:
The state in which the **Blocklist** is unavailable, in force until the Daily quota
is met for the day.
_Avoid_: "lock", "ban".

**Blocklist**:
The curated set of distractions the user has chosen to make unavailable while the
Block is in force. Everything not on the Blocklist stays usable.
_Avoid_: "blacklist", "denylist".

**Day**:
The window over which the Daily quota is counted and the Block resets. It is *not*
midnight - it is anchored to Anki's own configurable day-rollover hour (default 4am),
so the tool's "today" always matches Anki's "today".
_Avoid_: "calendar day", "midnight reset".

**Emergency unlock**:
The single sanctioned way to lift the Block *without* meeting the Daily quota,
gated by a deliberate delay so it stays costlier than just studying. Exists as a
safety valve against lockouts, not as a regular exit; each use is logged.
_Avoid_: "skip", "snooze", "disable".

## Relationships

- The **Block** lifts when **Reviews** completed today reach the **Daily quota**, or
  sooner if Anki has nothing left to study that **Day** (the satisfaction floor).
- A **Daily quota** is counted in **Reviews**, never in **Cards** or **Notes**.
- One **Card** can produce many **Reviews** in a single day.
- A **Block** makes the **Blocklist** unavailable; everything else stays reachable.
- The **Blocklist** may be added to at any time, but an entry may be **removed** only
  once the **Daily quota** has been met that **Day** - not when merely freed by an
  **Emergency unlock**. Removing only shapes future Days; it grants no access today.

## Example dialogue

> **Dev:** "If I fail the same Card four times in a learning session, is that one Review or four?"
> **User:** "Four. The quota counts answers, not distinct Cards - so the number Anki logs is the number that counts."

## Flagged ambiguities

- "20 cards a day" was the original ask. Resolved: the quota is **20 Reviews**, not 20 distinct Cards, because Reviews are what Anki logs cleanly and unambiguously.
- "until I finish these cards" (clear the due pile) vs "at least 20 a day" (fixed count). Resolved: **fixed quota of 20 Reviews**; the tool enforces *showing up*, not draining the backlog.
- "change the Blocklist" (from the menu bar) was split by direction: **adding** is free (it only strengthens the Block); **removing** is a weakening - a bypass - so it is allowed only after the Daily quota is met.
- "what if there aren't `quota` Reviews available in Anki?" Resolved: a **satisfaction floor** - the Day is also done once Anki has nothing left to study, so an unreachable Daily quota can never lock you out (and you can then adjust it).
