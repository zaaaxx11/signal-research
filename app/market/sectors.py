"""Broad sector labels from SEC's SIC industry text.

SEC publishes ``sicDescription`` (for example ``Electronic Computers``), not a
GICS sector. This module is an explicit, testable translation layer: first match
wins, and an unknown industry becomes ``Other`` rather than an invented label.
It is deliberately not a claim that SIC and GICS are the same taxonomy.
"""

from __future__ import annotations

from typing import Iterable

#: Order matters: "real estate investment trust" must hit Real Estate before
#: the generic finance rules, and "communications equipment" must hit
#: Technology before Communication Services.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Healthcare",
        (
            "pharmaceutical",
            "medical",
            "hospital",
            "health",
            "biolog",
            "surgical",
            "diagnostic",
            "dental",
            "nursing",
            "orthopedic",
            "drug",
            "laboratory analytical",
            "ophthalmic",
            "doctors",
        ),
    ),
    (
        "Real Estate",
        ("real estate", "reit"),
    ),
    (
        "Financials",
        (
            "bank",
            "insurance",
            "financial",
            "finance services",
            "credit",
            "investment",
            "securities",
            "savings",
            "mortgage",
            "holding compan",
            "blank checks",
            "broker",
            "security & commodity",
        ),
    ),
    (
        "Technology",
        (
            "computer",
            "semiconductor",
            "software",
            "electronic",
            "data processing",
            "information technology",
            "communications equipment",
            "measuring",
            "printed circuit",
            "search, detection",
        ),
    ),
    (
        "Communication Services",
        (
            "telephone",
            "telecommunication",
            "broadcasting",
            "radio",
            "television",
            "cable",
            "publishing",
            "advertising",
            "communications services",
            "motion picture",
            "video tape",
        ),
    ),
    (
        "Energy",
        ("oil", "gas", "petroleum", "energy", "coal", "drilling"),
    ),
    (
        "Materials",
        (
            "chemical",
            "steel",
            "metal",
            "mining",
            "paper",
            "forest",
            "cement",
            "glass",
            "plastics",
            "rubber",
            "gold",
            "silver",
            "ores",
        ),
    ),
    (
        "Utilities",
        ("electric", "utility", "water", "sanitary"),
    ),
    (
        "Consumer Staples",
        (
            "food",
            "beverage",
            "tobacco",
            "household",
            "soap",
            "toilet",
            "grocery",
            "agricultural production",
        ),
    ),
    (
        "Consumer Discretionary",
        (
            "retail",
            "apparel",
            "automobile",
            "motor vehicle",
            "hotel",
            "restaurant",
            "entertainment",
            "amusement",
            "sporting",
            "furniture",
            "furnishg",
            "homebuilding",
            "operative builders",
            "textile",
            "cutlery",
            "personal services",
            "educational services",
        ),
    ),
    (
        "Industrials",
        (
            "air",
            "railroad",
            "truck",
            "transport",
            "construction",
            "contractor",
            "machinery",
            "industrial",
            "aerospace",
            "defense",
            "ship",
            "engineering",
            "waste",
            "refuse",
            "equipment rental",
            "help supply",
            "business services",
            "management consulting",
            "pumps",
            "engines",
            "guided missiles",
            "ordnance",
        ),
    ),
)


def classify_sector(industry: str | None) -> str | None:
    """Map one SEC industry description to a broad sector, or ``Other``."""

    text = " ".join(str(industry or "").lower().split())
    if not text:
        return None
    for sector, needles in _RULES:
        if any(needle in text for needle in needles):
            return sector
    return "Other"


def classify_sectors(industries: Iterable[str | None]) -> list[str | None]:
    return [classify_sector(industry) for industry in industries]


__all__ = ["classify_sector", "classify_sectors"]
