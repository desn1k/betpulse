"""Chronological processing order shared by every rating / feature pass.

A pass walks finished fixtures in time order, predicting each one from the
state built so far and then folding its result into that state. Two rules keep
that honest (no information from the future, and reproducible):

* fixtures are ordered by ``(kickoff_at, id)`` — the id is a stable tie-break,
  so the order never depends on how the database happened to return rows;
* fixtures that kick off at the same instant form one **batch**: every fixture
  in a batch is predicted from the state *before* the batch, and only then do
  the batch's results update it. Otherwise one match's result would leak into
  the prediction of another match played at the same time.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from itertools import groupby
from typing import Protocol


class _Timed(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def kickoff_at(self) -> datetime: ...


def chronological[T: _Timed](fixtures: Iterable[T]) -> list[T]:
    """Fixtures sorted by ``(kickoff_at, id)``."""
    return sorted(fixtures, key=lambda fx: (fx.kickoff_at, fx.id))


def chronological_batches[T: _Timed](fixtures: Iterable[T]) -> Iterator[Sequence[T]]:
    """Yield groups of fixtures sharing a kickoff instant, in time order."""
    for _, batch in groupby(chronological(fixtures), key=lambda fx: fx.kickoff_at):
        yield list(batch)
