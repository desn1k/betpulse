"""Temporal windows over a chronologically ordered dataset.

Windows are cut on **kickoff instants**, never inside one: every fixture of a
same-kickoff batch (:mod:`app.ml.chronology`) lands in the same window, so no
window can learn from a match played at the same moment as one it is scored
on. Windows are contiguous, disjoint and in time order — each one strictly
later than the previous.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import datetime

import numpy as np


def temporal_windows(kickoffs: Sequence[datetime], cuts: Sequence[float]) -> list[np.ndarray]:
    """Split row indices into ``len(cuts) + 1`` time windows.

    ``kickoffs`` must be in non-decreasing order (rows as produced by the
    chronological feature table). ``cuts`` are increasing fractions in (0, 1)
    of the distinct kickoff instants: ``(0.6, 0.8)`` gives the first 60 % of
    instants, the next 20 %, and the last 20 %. Returns one index array per
    window (possibly empty when there are too few instants).
    """
    if any(b < a for a, b in zip(kickoffs, kickoffs[1:], strict=False)):
        raise ValueError("kickoffs must be in chronological order")
    if any(not 0.0 < c < 1.0 for c in cuts) or list(cuts) != sorted(set(cuts)):
        raise ValueError("cuts must be increasing fractions in (0, 1)")

    instants = sorted(set(kickoffs))
    boundaries = [instants[min(int(len(instants) * c), len(instants) - 1)] for c in cuts]
    # Window k holds rows with boundaries[k-1] <= kickoff < boundaries[k].
    window_of = np.array([bisect_right(boundaries, k) for k in kickoffs], dtype=int)
    return [np.flatnonzero(window_of == k) for k in range(len(cuts) + 1)]


def assert_ordered_disjoint(kickoffs: Sequence[datetime], windows: Sequence[np.ndarray]) -> None:
    """Guard used by the training path: windows are disjoint and every row of a
    later window kicks off strictly after every row of an earlier one."""
    seen: set[int] = set()
    previous_last: datetime | None = None
    for window in windows:
        rows = set(int(i) for i in window)
        if rows & seen:
            raise AssertionError("temporal windows overlap")
        seen |= rows
        if len(window) == 0:
            continue
        first = min(kickoffs[i] for i in window)
        if previous_last is not None and not first > previous_last:
            raise AssertionError("a later window starts before an earlier one ends")
        previous_last = max(kickoffs[i] for i in window)
