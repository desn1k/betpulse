"""CI guard: every implemented provider has a section in docs/DATA_SOURCES.md.

Keeps the data-sources documentation in sync with app/providers/ (spec §20).
"""

from __future__ import annotations

from pathlib import Path

from app.providers.registry import implemented_provider_names

DATA_SOURCES = Path(__file__).resolve().parents[2] / "docs" / "DATA_SOURCES.md"


def test_every_provider_is_documented() -> None:
    text = DATA_SOURCES.read_text(encoding="utf-8")
    missing = [name for name in implemented_provider_names() if name not in text]
    assert not missing, f"providers missing a docs/DATA_SOURCES.md section: {missing}"
