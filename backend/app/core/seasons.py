"""Canonical season labels.

One spelling per season, whatever a source sends:

* a league whose season spans two calendar years (``leagues.season_start_month``
  set, e.g. 8 = August for the top-5 leagues, 7 for the RPL) uses ``YYYY-YYYY``
  with consecutive years, e.g. ``2026-2027``;
* a calendar-year league (``season_start_month`` NULL) uses ``YYYY``.

Without one canonical label the same match filed by two sources ("2026" from a
live API, "2026-2027" from a historical file) would be two fixtures.
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"^\s*(\d{4})\s*[-/]\s*(\d{2}|\d{4})\s*$")
_SHORT = re.compile(r"^\s*(\d{2})\s*[-/]\s*(\d{2})\s*$")
_SINGLE = re.compile(r"^\s*(\d{4})\s*$")


class SeasonFormatError(ValueError):
    """A season label that cannot be read unambiguously."""


def _expand(short_year: int, start_year: int) -> int:
    """Second year of a split season written with two digits ('2026/27')."""
    candidate = start_year - start_year % 100 + short_year
    return candidate if candidate > start_year else candidate + 100


def canonical_season(raw: str, *, split_year: bool) -> str:
    """Return the canonical label for ``raw``.

    Accepted spellings: ``2026``, ``2026-2027``, ``2026/2027``, ``2026/27``,
    ``26/27``. For a split-year league a single year is the season's *start*
    year (``"2026"`` → ``"2026-2027"``); a calendar-year league keeps
    ``"2026"``. Anything else — including non-consecutive years — raises
    :class:`SeasonFormatError` rather than guessing.
    """
    if match := _SINGLE.match(raw):
        start = int(match.group(1))
        return f"{start}-{start + 1}" if split_year else str(start)

    if match := _SPLIT.match(raw):
        start = int(match.group(1))
        end_raw = match.group(2)
        end = int(end_raw) if len(end_raw) == 4 else _expand(int(end_raw), start)
    elif match := _SHORT.match(raw):
        start = 2000 + int(match.group(1))
        end = _expand(int(match.group(2)), start)
    else:
        raise SeasonFormatError(f"unreadable season label {raw!r}")

    if end != start + 1:
        raise SeasonFormatError(f"season {raw!r} does not span consecutive years")
    if not split_year:
        raise SeasonFormatError(
            f"season {raw!r} spans two years but the league plays calendar-year seasons"
        )
    return f"{start}-{end}"


def is_canonical(season: str, *, split_year: bool) -> bool:
    try:
        return canonical_season(season, split_year=split_year) == season
    except SeasonFormatError:
        return False
