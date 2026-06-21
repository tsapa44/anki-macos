"""Persistent daemon state.

In a real install this file is root-owned (the daemon writes it; users can read but
not edit without sudo), so the "quota met today" flag cannot be forged in a lazy
moment. The daemon is the only writer of `satisfied_day` and `emergency_day`; it
only sets them after *observing* the condition, never on request.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields


@dataclass
class State:
    # Day (see CONTEXT.md) for which the quota was confirmed met by the daemon.
    satisfied_day: str | None = None
    # Day for which an Emergency unlock has freed the user.
    emergency_day: str | None = None
    # Pending Emergency unlock: epoch when it releases, and when it was requested.
    emergency_release_at: float | None = None
    emergency_requested_at: float | None = None
    # Epochs of completed Emergency unlocks - the accountability log (ADR-0003).
    unlock_log: list[float] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "State":
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str) -> None:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(self), f, indent=2)
            os.replace(tmp, path)  # atomic
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
