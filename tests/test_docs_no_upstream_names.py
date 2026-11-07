"""The docs describe a source by its role, never by its name.

`docs/blueprint.md` is the sanitized copy of the operator's pipeline note: what
each feed contributes, not who it is ("rahasia dapur" — the reader sees the
product, never the supply chain). The served UI has its own guard
(`web/src/shell/SiteFooter.test.tsx`); this is the same rule for `docs/`.

Scope is deliberately `docs/` only. The README predates the rule and names its
sources throughout; sanitizing it is a separate decision, not something this
guard should force by failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"

#: Substrings that may not appear in a document. Compared lowercased, so this is
#: a plain substring test. ``sec`` alone is not in the list on purpose: it is a
#: substring of ordinary words (second, section, security) and would fail on a
#: sentence that reveals nothing. ``edgar`` and ``sec.gov`` carry the meaning.
FORBIDDEN = (
    "yahoo",
    "coingecko",
    "finnhub",
    "edgar",
    "stooq",
    "finviz",
    "tradingview",
    "nasdaq",
    "bloomberg",
    "reuters",
    "marketbeat",
    "defillama",
    "cnbc",
    "cboe",
    "finra",
    "sec.gov",
    "america/scan",
)


def test_the_blueprint_ships() -> None:
    """The sanitized copy is what history carries; the raw note stays local."""

    assert (DOCS / "blueprint.md").is_file()


@pytest.mark.parametrize("path", sorted(DOCS.rglob("*.md")), ids=lambda item: item.name)
def test_a_document_names_no_upstream(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    hits = [name for name in FORBIDDEN if name in text]
    assert hits == [], f"{path.relative_to(DOCS.parent)} names {hits}"
