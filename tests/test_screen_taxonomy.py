"""GET /screens/taxonomy — the static theme / sub-theme vocabulary."""

from __future__ import annotations

from app.screen import theme_taxonomy


def test_taxonomy_tree_counts() -> None:
    doc = theme_taxonomy.document()
    assert doc["available"] is False
    assert len(doc["themes"]) == 40
    assert sum(len(t["subthemes"]) for t in doc["themes"]) == 268
    assert all(t["available"] is False for t in doc["themes"])
    assert all(
        s["available"] is False
        for t in doc["themes"]
        for s in t["subthemes"]
    )


def test_taxonomy_every_subtheme_has_parent() -> None:
    doc = theme_taxonomy.document()
    for t in doc["themes"]:
        assert t["subthemes"], f"theme {t['id']} has no sub-themes"
        for s in t["subthemes"]:
            assert s["id"] and s["label"]


def test_taxonomy_reason_matches_refusal() -> None:
    from app.screen.filter_fields import UNAVAILABLE

    doc = theme_taxonomy.document()
    assert doc["reason"] == UNAVAILABLE["theme"]
    assert all(t["reason"] == UNAVAILABLE["theme"] for t in doc["themes"])
    assert all(
        s["reason"] == UNAVAILABLE["theme"]
        for t in doc["themes"]
        for s in t["subthemes"]
    )


def test_taxonomy_tree_is_frozen() -> None:
    with __import__("pytest").raises(Exception):
        theme_taxonomy.TREE["themes"].append({"id": "x", "label": "x", "subthemes": []})
