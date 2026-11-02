"""The source panel's theme taxonomy: 40 themes, 268 sub-themes, one tree.

Finviz offers two classification pickers — **Theme** (40 values) and
**Sub-theme** (268) — and this deployment could never answer either: no layer
carries a theme column, so the refusal in
:data:`app.screen.filter_fields.UNAVAILABLE` names the metric a request would
need and ``POST /screens/select`` refuses the key instead of returning an
unfiltered page (see :mod:`app.screen.filter_fields`).

What this module publishes is the **vocabulary**, not a filter. The distinction
is the whole design:

* the options are **static** — the probe's own tokens and labels, read from the
  vendored extract (:func:`app.screen.descriptive.vendored`) at first use. A
  theme is a label a reader recognises, not a cell a row carries, so nothing
  here scans and nothing here counts;
* a census over the universe would answer **zero for every theme**, because no
  row has the column. That is not an honest empty answer — it is the shape of a
  lie ("nobody is in quantum computing"), which is the failure mode
  :data:`app.screen.filter_fields.UNAVAILABLE` exists to refuse. So this route
  publishes no ``count`` at all: an absent count is a vocabulary entry, never a
  measured zero;
* the control stays **unavailable** until a classification source exists, and
  :func:`document` says so on every option with the very sentence
  :data:`app.screen.filter_fields.UNAVAILABLE` gives ``theme`` — one owner per
  fact, so the document and the 422 cannot describe different gaps.

The tree
--------
The vendored probe ships the two vocabularies flat: 40 theme labels and 268
sub-theme labels of the shape ``"AI - Ads, Search & Recommendations"``. The
hierarchy is the probe's own abbreviation convention: each sub-theme's label is
prefixed with a short form of its theme's name, and the mapping below is the
probe's own grouping, not an invention. The derivation runs once, at import,
and **raises** if any sub-theme resolves to no theme or to two: an unmapped
sub-theme would be a taxonomy entry whose parent silence is a data bug, and a
two-parent one would make the picker's second level ambiguous.

That is also why the sub-theme's own ``token`` keeps its full label spelling:
``agriculturealtprotein`` is the value a panel sends, and the tree names which
theme it belongs to — the grouping never rewrites a label.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from app.screen import descriptive
from app.screen.filter_fields import UNAVAILABLE

#: The two catalogue keys this module publishes. Closed: a third would be a
#: second taxonomy nobody probed.
THEME_KEYS: tuple[str, ...] = ("theme", "subtheme")

#: The probe's own abbreviation: sub-theme prefix (the part of its label before
#: ``" - "``) → theme token. Derived from the vendored labels, never invented:
#: every one of the 40 themes has one short form and every one of the 268
#: sub-themes carries it. :func:`_build_tree` fails at import if a sub-theme's
#: prefix is missing here, so a stale probe cannot silently drop a sub-theme.
_PREFIX_TO_THEME: Mapping[str, str] = MappingProxyType(
    {
        "AI": "artificialintelligence",
        "Agriculture": "agriculturefoodtech",
        "Automation": "industrialautomation",
        "Autonomous": "autonomoussystems",
        "Big Data": "bigdata",
        "Biometrics": "biometrics",
        "Blockchain": "cryptoblockchain",
        "Cloud": "cloudcomputing",
        "Comm Agri": "commoditiesagriculture",
        "Comm Energy": "commoditiesenergy",
        "Comm Metals": "commoditiesmetals",
        "Consumer": "consumergoods",
        "Cybersecurity": "cybersecurity",
        "Defense": "defenseaerospace",
        "E-commerce": "ecommerce",
        "EVs": "electricvehicles",
        "Education": "educationtechnology",
        "Energy Base": "energytraditional",
        "Energy Clean": "energyrenewable",
        "Entertainment": "digitalentertainment",
        "Environmental": "environmentalsustainability",
        "FinTech": "fintech",
        "Hardware": "hardware",
        "Healthcare": "healthcarebiotech",
        "IoT": "internetofthings",
        "Longevity": "agingpopulationlongevity",
        "NanoTech": "nanotechnology",
        "Nutrition": "healthyfoodnutrition",
        "Quantum": "quantumcomputing",
        "Real Estate": "realestatereits",
        "Robotics": "robotics",
        "Semis": "semiconductors",
        "Smart Home": "smarthome",
        "Social": "socialmedia",
        "Software": "software",
        "Space": "spacetech",
        "Telecom": "telecommunications",
        "Transportation": "transportationlogistics",
        "V/A Reality": "virtualaugmentedreality",
        "Wearables": "wearables",
    }
)


def _build_tree() -> dict[str, Any]:
    """The taxonomy tree, derived once from the vendored probe.

    Raises ``RuntimeError`` at import when the probe cannot produce exactly one
    parent per sub-theme: a vocabulary whose tree is incomplete is a picker that
    would drop 268 options on the floor, and one whose parent is ambiguous would
    show the same sub-theme twice.
    """

    controls = descriptive.vendored()
    themes = controls.get("theme", {}).get("options", [])
    subthemes = controls.get("subtheme", {}).get("options", [])
    if len(themes) != 40:
        raise RuntimeError(
            f"the theme vocabulary is 40 values, got {len(themes)}: the vendored "
            "probe changed shape and the picker would be stale"
        )
    if len(subthemes) != 268:
        raise RuntimeError(
            f"the sub-theme vocabulary is 268 values, got {len(subthemes)}: the "
            "vendored probe changed shape and the picker would be stale"
        )

    themes_node: list[dict[str, Any]] = []
    for theme in themes:
        token, label = str(theme["token"]), str(theme["label"])
        themes_node.append({"id": token, "label": label, "subthemes": []})

    index = {theme["id"]: theme for theme in themes_node}
    for sub in subthemes:
        token, label = str(sub["token"]), str(sub["label"])
        prefix = label.split(" - ")[0].strip()
        parent = _PREFIX_TO_THEME.get(prefix)
        if parent is None:
            raise RuntimeError(
                f"sub-theme {label!r} has no theme parent: the tree would publish "
                "an option no picker can show, so the taxonomy is refused instead"
            )
        if parent not in index:
            raise RuntimeError(
                f"sub-theme {label!r} maps to unknown theme {parent!r}: the "
                "taxonomy is stale and the picker would be broken"
            )
        index[parent]["subthemes"].append({"id": token, "label": label})

    # Every theme keeps at least one sub-theme, or the picker's second level
    # would be empty for a theme the probe says exists.
    empty = [theme["id"] for theme in themes_node if not theme["subthemes"]]
    if empty:
        raise RuntimeError(
            f"themes with no sub-themes: {empty} — the probe changed shape"
        )

    # Freeze the tree so a route can never hand out a mutable copy a caller
    # could edit into the next response.
    frozen_themes = tuple(
        {
            "id": theme["id"],
            "label": theme["label"],
            "subthemes": tuple(theme["subthemes"]),
        }
        for theme in themes_node
    )
    return {"themes": frozen_themes}


#: The derived tree, built once. Frozen: a route must never hand out a mutable
#: copy a caller could edit into the next response.
TREE: Mapping[str, Any] = MappingProxyType(_build_tree())

#: The sentence every option carries while no classification source exists —
#: read from the one table the 422 reads, so the document and the refusal can
#: never describe two different gaps.
REASON: str = UNAVAILABLE["theme"]

#: Where the vocabulary came from, published so a client can date it.
SOURCE: str = (
    "static taxonomy: the 40 theme and 268 sub-theme tokens are the source "
    "panel's own vocabulary, read from the vendored probe — no census, no "
    "counts, because no layer carries a theme column (see "
    "app.screen.filter_fields.UNAVAILABLE)"
)


def document() -> dict[str, Any]:
    """The taxonomy tree with the reason it cannot yet filter.

    One body: ``themes`` is the tree in panel order, and every node carries
    ``available: False`` plus :data:`REASON`, so a picker built from this answer
    draws the second level and still cannot promise a filter — the same contract
    a disabled control publishes inline in ``GET /screens/filters``.
    """

    themes = []
    for theme in TREE["themes"]:
        themes.append(
            {
                "id": theme["id"],
                "label": theme["label"],
                "available": False,
                "reason": REASON,
                "subthemes": [
                    {
                        "id": sub["id"],
                        "label": sub["label"],
                        "available": False,
                        "reason": REASON,
                    }
                    for sub in theme["subthemes"]
                ],
            }
        )
    return {
        "source": SOURCE,
        "available": False,
        "reason": REASON,
        "themes": themes,
    }


__all__ = ["REASON", "SOURCE", "THEME_KEYS", "TREE", "document"]
