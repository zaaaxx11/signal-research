"""News keywords: free text with an alphabet, searched over our own corpus.

The control is a ``text`` box, so the two failure modes are a token that is not a
keyword (a quote, a colon, 41 characters) and a keyword that quietly means
something else (``supply chain`` as OR instead of AND). Both are pinned here,
plus the corpus itself: what it holds, what it matches and how it is counted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, descriptive, news_index
from app.screen.selection import SelectionError, parse_selection
from tests import _screen as S


def corpus(items: list[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    return {
        "as_of": "2026-11-01",
        "captured_at": "2026-11-01T20:00:00+00:00",
        "source": {"service": "app.news.service", "symbols": len({s for s, _ in items})},
        "items": [
            {"symbol": symbol, "title": title, "published": None, "source": "test"}
            for symbol, title in items
        ],
        **extra,
    }


def use_corpus(tmp_path: Path, monkeypatch: Any, document: dict[str, Any]) -> None:
    path = tmp_path / "news_corpus.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(news_index.PATH_ENV, str(path))
    news_index.clear()


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    news_index.clear()
    yield
    news_index.clear()


def body(key: str, value: str) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [{"key": key, "op": "eq", "value": value}], "limit": 10},
    }


# ---------------------------------------------------------------------------
# The alphabet
# ---------------------------------------------------------------------------


def test_the_control_publishes_the_pattern_it_enforces() -> None:
    control = next(
        control
        for group in descriptive.document()["groups"]
        if group["id"] == "news"
        for control in group["controls"]
        if control["id"] == "news_keywords"
    )
    assert control["control"] == "text"
    assert control["pattern"] == news_index.PATTERN
    assert control["tier"] == "premium"
    assert control["available"] is True
    assert control["note"] and "case-insensitively" in control["note"]


def test_a_keyword_outside_the_alphabet_is_422() -> None:
    for bad in ("a;drop table", "c++", "semi/conductor", "", "x" * 41):
        with pytest.raises(SelectionError) as caught:
            parse_selection(body("news_keywords", bad))
        assert caught.value.code == "invalid_news_keyword", bad
        assert any(
            hint in str(caught.value)
            for hint in ("letters, digits, spaces and hyphens", "maximum", "cannot be empty")
        ), bad


def test_a_colon_is_a_namespace_not_a_keyword() -> None:
    """``ai:chips`` is a *control* prefix, so it is refused as one, not parsed."""

    with pytest.raises(SelectionError) as caught:
        parse_selection(body("news_keywords", "ai:chips"))
    assert caught.value.code == "unknown_bucket"
    assert "namespace" in str(caught.value) or "belongs to control" in str(caught.value)


def test_a_keyword_inside_the_alphabet_canonicalises_to_lower_case() -> None:
    request = parse_selection(body("news_keywords", "  Supply-Chain  "), plan="premium")
    assert request.filters[0].value == "news_kw:supply chain"


def test_the_public_pattern_and_the_validator_are_one_rule() -> None:
    assert news_index.KEYWORD_RE.fullmatch("Semiconductor-2") is not None
    assert news_index.KEYWORD_RE.fullmatch("x" * news_index.MAX_LENGTH) is not None
    assert news_index.KEYWORD_RE.fullmatch("x" * (news_index.MAX_LENGTH + 1)) is None


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def test_a_keyword_matches_a_word_case_insensitively(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_corpus(
        tmp_path,
        monkeypatch,
        corpus([("AAPL", "Apple's semiconductor orders rise"), ("MSFT", "Cloud growth slows")]),
    )
    assert sorted(news_index.symbols_for("SEMICONDUCTOR")) == ["AAPL"]
    assert sorted(news_index.symbols_for("semiconductor")) == ["AAPL"]


def test_matching_is_whole_word_not_substring(tmp_path: Path, monkeypatch: Any) -> None:
    use_corpus(tmp_path, monkeypatch, corpus([("AAPL", "Semiconductors rally hard")]))
    assert news_index.symbols_for("semiconductor") == frozenset()
    assert news_index.symbols_for("semiconductors") == frozenset({"AAPL"})


def test_a_multi_word_query_is_an_and(tmp_path: Path, monkeypatch: Any) -> None:
    use_corpus(
        tmp_path,
        monkeypatch,
        corpus(
            [
                ("FAST", "Supply chain pressure eases for distributors"),
                ("AAPL", "Supply growth meets new chip demand"),
            ]
        ),
    )
    assert news_index.symbols_for("supply chain") == frozenset({"FAST"})
    assert news_index.symbols_for("supply-chain") == frozenset({"FAST"})
    assert news_index.symbols_for("supply") == frozenset({"FAST", "AAPL"})


def test_a_word_the_corpus_never_uses_matches_nobody(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_corpus(tmp_path, monkeypatch, corpus([("AAPL", "Apple's quarter beats")]))
    assert news_index.symbols_for("aardvark") == frozenset()


def test_the_bucket_reads_the_index(tmp_path: Path, monkeypatch: Any) -> None:
    use_corpus(
        tmp_path,
        monkeypatch,
        corpus([("AAPL", "Apple's semiconductor orders rise"), ("MSFT", "Cloud growth slows")]),
    )
    found = buckets.bucket("news_kw:semiconductor")
    context = buckets.Context.at("2026-11-01")
    assert found.key == "news_keywords"
    assert found.predicate({"symbol": "AAPL"}, context) is True
    assert found.predicate({"symbol": "MSFT"}, context) is False
    assert found.predicate({"symbol": None}, context) is False


def test_the_corpus_counts_are_measured_not_described(tmp_path: Path, monkeypatch: Any) -> None:
    use_corpus(
        tmp_path,
        monkeypatch,
        corpus(
            [
                ("AAPL", "Apple's semiconductor orders rise"),
                ("AAPL", "Apple's semiconductor orders rise"),  # duplicate title
                ("MSFT", "Cloud growth slows"),
            ]
        ),
    )
    coverage = news_index.coverage()
    assert coverage["available"] is True
    assert coverage["headlines"] == 3
    assert coverage["symbols"] == 2
    assert coverage["words"] > 0
    assert coverage["as_of"] == "2026-11-01"


def test_a_missing_corpus_refuses_the_bucket_with_the_reason(
    tmp_path: Path, monkeypatch: Any
) -> None:
    news_index.clear()
    monkeypatch.setenv(news_index.PATH_ENV, str(tmp_path / "absent.json"))
    with pytest.raises(buckets.UnavailableBucket) as caught:
        buckets.bucket("news_kw:ai")
    assert caught.value.code == "unavailable_bucket"
    assert "news_corpus.json" in str(caught.value)


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_news_keywords_is_premium_then_answers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    use_corpus(
        tmp_path,
        monkeypatch,
        corpus([("AAPL", "Apple's semiconductor orders rise"), ("MSFT", "Cloud growth slows")]),
    )
    rows = [
        {"symbol": "AAPL", "name": "Apple", "price": 200.0, "market_cap_usd": 3e12},
        {"symbol": "MSFT", "name": "Microsoft", "price": 400.0, "market_cap_usd": 3e12},
    ]

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", lambda _rows, **_: list(rows))
        denied = await harness.client.post(
            "/screens/select", json=body("news_keywords", "semiconductor")
        )
        allowed = await harness.client.post(
            "/screens/select",
            json=body("news_keywords", "semiconductor"),
            headers={"X-Plan": "premium"},
        )

    assert denied.status_code == 403
    assert denied.json()["error"] == "premium_required"
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()
    assert payload["total"] == 1
    assert payload["rows"][0]["symbol"] == "AAPL"
    assert payload["filters"] == [
        {"key": "news_keywords", "field": None, "op": "eq", "value": "news_kw:semiconductor"}
    ]


async def test_a_bad_keyword_is_422_even_with_a_premium_plan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=body("news_keywords", "ai;drop"),
            headers={"X-Plan": "premium"},
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_news_keyword"
