"""AnkiConnect client - the detection half (ADR-0001).

We only need one fact: how many Reviews have been logged today. AnkiConnect's
`getNumCardsReviewedToday` counts rows in the revlog (individual answer events,
i.e. our Review unit) respecting Anki's configured day cutoff - verified against
the add-on source, despite the misleading "Cards" in the action name.

`reviews_today()` raises AnkiUnavailable whenever the count cannot be confirmed
(Anki closed, add-on broken, timeout). Callers treat that as "not done" and keep
the Block on - fail closed (ADR-0003).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AnkiUnavailable(Exception):
    """Raised when the Review count cannot be confirmed for any reason."""


class AnkiClient:
    def __init__(self, url: str, timeout: float = 3.0):
        self.url = url
        self.timeout = timeout

    def _invoke(self, action: str, **params):
        payload = json.dumps(
            {"action": action, "version": 6, "params": params}
        ).encode()
        req = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            raise AnkiUnavailable(str(e)) from e
        if not isinstance(body, dict) or "result" not in body:
            raise AnkiUnavailable(f"unexpected AnkiConnect response: {body!r}")
        if body.get("error") is not None:
            raise AnkiUnavailable(f"AnkiConnect error: {body['error']}")
        return body["result"]

    def reviews_today(self) -> int:
        """Reviews logged so far in Anki's current day. Raises AnkiUnavailable."""
        result = self._invoke("getNumCardsReviewedToday")
        try:
            return int(result)
        except (TypeError, ValueError) as e:
            raise AnkiUnavailable(f"non-numeric review count: {result!r}") from e

    def nothing_left_today(self) -> bool:
        """True when Anki has nothing left to study today - the satisfaction floor
        (ADR-0006). Sums new + learning + review across all decks via getDeckStats
        (which respects Anki's daily limits). Only zero-vs-nonzero is meaningful:
        parent/child decks may over-count, but the sum is 0 exactly when every deck
        is empty. Raises AnkiUnavailable."""
        names = self._invoke("deckNames")
        stats = self._invoke("getDeckStats", decks=names)
        total = 0
        for s in (stats or {}).values():
            total += s.get("new_count", 0) + s.get("learn_count", 0) + s.get("review_count", 0)
        return total == 0
