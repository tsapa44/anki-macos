# Use AnkiConnect to detect review progress

We read the day's Review count by polling the AnkiConnect add-on's local HTTP API
(`getNumCardsReviewedToday`, which counts `revlog` rows respecting Anki's day
cutoff) rather than reading Anki's `collection.anki2` SQLite database directly.

## Considered Options

- **Read the SQLite DB directly.** Works even when Anki is closed, but fragile: the
  file is locked / WAL-journaled while Anki writes and syncs, the day-rollover math
  must be re-implemented, and the schema drifts between Anki versions.
- **A custom Anki add-on.** Most control, but the most code to build and maintain.

## Consequences

- The tool depends on the user installing the AnkiConnect add-on.
- Progress is only readable while Anki is running. This costs nothing in practice -
  you cannot make Review progress with Anki closed - but it means that when Anki is
  closed the tool must rely on a cached "quota met today" flag rather than a live
  read, and treat "cannot confirm" as "not yet done" (stay blocked).
