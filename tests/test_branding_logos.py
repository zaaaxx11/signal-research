"""Brand marks are a contract, not a best-effort decoration.

Every screen row must carry a usable ``logo``: a vendored asset when the cockpit
has one, an offline monogram otherwise. A missing symbol is still a symbol, so
even ``None`` must not produce a null or an empty string.
"""

from __future__ import annotations

from app.api.screens import _with_logos
from app.branding import logo_for


def test_a_vendored_logo_is_a_stable_local_asset() -> None:
    logo = logo_for("NVDA")
    assert logo.startswith("/ui/logos/NVDA")
    assert logo.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp"))


def test_an_unknown_symbol_gets_an_offline_monogram() -> None:
    logo = logo_for("NOTATICKER")
    assert logo.startswith("data:image/svg+xml;base64,")
    assert "base64," in logo
    assert logo_for(None).startswith("data:image/svg+xml;base64,")


def test_logo_decoration_is_non_mutating_and_never_null() -> None:
    original = {"rows": [{"symbol": "NVDA"}, {"symbol": "NOTATICKER"}]}
    document = _with_logos(original)

    assert original["rows"][0] == {"symbol": "NVDA"}, "the cached document must not be mutated"
    assert all(row["logo"] for row in document["rows"])
    assert document["rows"][0]["logo"].startswith("/ui/logos/NVDA")
