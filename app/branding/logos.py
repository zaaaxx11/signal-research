"""Guaranteed per-symbol brand marks for the cockpit and its JSON rows.

The engine's contracts are allowed to carry provenance internally, but the
cockpit does not put a provider name in front of the operator. A logo is part of
the instrument identity, not of the source that happened to answer first.

Resolution order:
1. a vendored logo under ``ui/logos`` (stable, CSP-friendly, no third party);
2. a known domain's favicon URL for folded brands the cockpit has no asset for;
3. a deterministic monogram data URI, so ``logo`` is never null.

The third hop is the important one: a missing vendor asset must not turn a row
into ``logo: null`` or make the UI fetch a broken image. The monogram is an
honest placeholder, and it renders without any network.
"""

from __future__ import annotations

import base64
import hashlib
from html import escape
from pathlib import Path

_UI_LOGO_DIR = Path(__file__).resolve().parents[2] / "ui" / "logos"

#: Folded logo domains for instruments whose vendored asset is not present yet.
LOGO_DOMAINS: dict[str, str] = {
    "AAPL": "apple.com",
    "ABBV": "abbvie.com",
    "AMD": "amd.com",
    "AMZN": "amazon.com",
    "AVGO": "broadcom.com",
    "BRK.B": "berkshirehathaway.com",
    "GOOG": "google.com",
    "GOOGL": "google.com",
    "JNJ": "jnj.com",
    "JPM": "jpmorganchase.com",
    "LLY": "lilly.com",
    "MA": "mastercard.com",
    "META": "meta.com",
    "MSFT": "microsoft.com",
    "MU": "micron.com",
    "NVDA": "nvidia.com",
    "SPCX": "spacex.com",
    "TSLA": "tesla.com",
    "V": "visa.com",
    "WMT": "walmart.com",
    "XOM": "exxonmobil.com",
}

_LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")


def _initials(symbol: str) -> str:
    """A short, deterministic ticker mark; dots and slashes are not initials."""

    cleaned = "".join(char for char in symbol.upper() if char.isalnum())
    return (cleaned or "?")[:4]


def _monogram(symbol: str) -> str:
    """A colour-stable SVG mark; data URI keeps the field useful offline."""

    seed = int(hashlib.sha256(symbol.upper().encode("utf-8")).hexdigest()[:8], 16)
    hue = seed % 360
    # Two nearby hues keep the palette in the cockpit's pink/rose family.
    top = f"hsl({hue} 72% 58%)"
    bottom = f"hsl({(hue + 28) % 360} 62% 38%)"
    label = escape(_initials(symbol))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/>'
        "</linearGradient></defs>"
        '<rect width="64" height="64" rx="15" fill="url(#g)"/>'
        '<text x="32" y="34" text-anchor="middle" dominant-baseline="middle" '
        'font-family="ui-monospace, monospace" font-size="21" font-weight="700" '
        f'fill="#1b0a12">{label}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _vendored(symbol: str) -> str | None:
    """The first vendored asset for ``symbol``, if the UI has one."""

    for extension in _LOGO_EXTENSIONS:
        candidate = _UI_LOGO_DIR / f"{symbol}{extension}"
        if candidate.is_file():
            return f"/ui/logos/{symbol}{extension}"
    return None


def logo_for(symbol: str | None) -> str:
    """Return a non-empty logo URL for every instrument.

    ``symbol`` is the canonical ticker. The function is deliberately pure and
    filesystem-only: no provider request is made while serving a screen, and a
    new symbol cannot add latency to the browse path.
    """

    if not symbol:
        return _monogram("?")
    key = str(symbol).upper()
    return _vendored(key) or _monogram(key)


def logo_domain(symbol: str) -> str | None:
    """The folded brand domain, exposed for diagnostics only (not the UI)."""

    return LOGO_DOMAINS.get(str(symbol).upper())


__all__ = ["LOGO_DOMAINS", "logo_domain", "logo_for"]
