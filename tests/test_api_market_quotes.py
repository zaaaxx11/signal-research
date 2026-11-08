"""Contract for ``GET /market/quotes`` — the batch lookup a watchlist calls.

What is pinned here, in the order the route computes it:

* **the document** — ``{as_of, rows, missing}``, one row ``{symbol, name, price,
  change_pct, currency, class}``;
* **two sources, one call** — a US equity is read from the selector's scan rows
  (one pass, memoised per scan generation) and a crypto/futures/forex symbol
  from the same path ``/market/alt`` uses, so one call can answer ``AAPL`` and
  ``BTC-USD`` together;
* **no fan-out for equities** — a stocks-only request makes zero transport
  calls, and a mixed one asks the transport for the alt symbols only
  (**adversarial (d)**);
* **the caller's order** — symbols are de-duplicated and upper-cased, ``rows``
  follow the request, and ``missing`` is the unanswered symbols **in input
  order**, never invented;
* **the refusals** — more than 50 symbols, an empty list, and a list where no
  token can be a ticker are all a ``422 invalid_symbols`` naming the cap;
* **the cache and the validator** — memoised per symbols and scan generation,
  weak ``W/"qts-…"``, ``Cache-Control: no-cache``, a ``304`` decided before any
  fetch, and a ``HEAD`` with the ``GET``'s length and none of its bytes.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from tests import _alt as A
from tests import _market as M

_ROW_KEYS = frozenset({"symbol", "name", "price", "change_pct", "currency", "class"})


def _rows(count: int = 4) -> list[M.Row]:
    """A small US population: AAPL, MSFT, NVDA, AMZN with distinct moves."""

    names = ["AAPL", "MSFT", "NVDA", "AMZN"]
    return [
        M.Row(symbol=names[index], cap=3_000_000_000_000.0 - index * 1_000_000_000.0,
              change=1.5 + index)
        for index in range(min(count, len(names)))
    ]


def _bitcoin() -> dict[str, Any]:
    return A.chart_body("BTC-USD", closes=(77_318.72,), previous_close=77_202.40)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


async def test_a_stock_and_a_crypto_symbol_both_resolve_in_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        response = await harness.get("/market/quotes", symbols="AAPL,BTC-USD")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"as_of", "rows", "missing"}
    assert body["missing"] == []
    assert body["as_of"].endswith("+00:00")
    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "BTC-USD"]
    stock, crypto = body["rows"]
    assert set(stock) == _ROW_KEYS and set(crypto) == _ROW_KEYS
    assert stock["class"] == "stock"
    assert stock["price"] == pytest.approx(10.0)
    assert stock["change_pct"] == pytest.approx(1.5)
    assert stock["name"] == "AAPL Corporation"
    assert stock["currency"] == "USD"
    assert crypto["class"] == "crypto"
    assert crypto["price"] == pytest.approx(77_318.72)
    assert crypto["change_pct"] == pytest.approx(
        round((77_318.72 / 77_202.40 - 1.0) * 100.0, 4)
    )
    assert crypto["name"] == "Bitcoin"
    assert all(math.isfinite(row["change_pct"]) for row in body["rows"])


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("ES=F", "futures"),
        ("6E=F", "futures"),
        ("EURUSD=X", "forex"),
        ("ETH-USD", "crypto"),
    ],
)
async def test_the_suffix_names_the_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symbol: str, expected: str
) -> None:
    body = A.chart_body(symbol, closes=(100.0,), previous_close=99.0)
    async with A.alt_client(
        tmp_path, monkeypatch, rows=[], transport=A.FakeTransport(bodies={symbol: body})
    ) as harness:
        answer = (await harness.get("/market/quotes", symbols=symbol)).json()

    assert answer["rows"][0]["class"] == expected


async def test_an_unknown_symbol_is_missing_not_invented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        body = (
            await harness.get("/market/quotes", symbols="ZZZZ,BTC-USD,QQQQ")
        ).json()

    assert [row["symbol"] for row in body["rows"]] == ["BTC-USD"]
    assert body["missing"] == ["ZZZZ", "QQQQ"]


async def test_symbols_are_upper_cased_and_de_duplicated_in_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        body = (
            await harness.get(
                "/market/quotes", symbols="aapl, BTC-USD ,zzzz,AAPL,zzzz,msft"
            )
        ).json()

    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "BTC-USD", "MSFT"]
    assert body["missing"] == ["ZZZZ"]
    # One read for the one alt symbol, however many times it was named.
    assert transport.asked == ["BTC-USD"]


async def test_a_stock_the_scanner_could_not_price_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [A.BareRow("AAPL", price=None), A.BareRow("MSFT", price=10.0)]
    async with A.alt_client(
        tmp_path, monkeypatch, rows=rows, transport=A.FakeTransport()
    ) as harness:
        body = (await harness.get("/market/quotes", symbols="AAPL,MSFT")).json()

    assert [row["symbol"] for row in body["rows"]] == ["MSFT"]
    assert body["missing"] == ["AAPL"]


async def test_an_alt_symbol_outside_the_catalogue_takes_its_name_from_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PEPE-USD is in the crypto catalogue (122 coins), so its name comes from the
    # curated list, not the provider. This contract is about a symbol the
    # catalogue does *not* carry: WOJAK-USD is outside it, and the -USD suffix is
    # what still classes it as crypto.
    body = A.chart_body(
        "WOJAK-USD", closes=(0.0000012,), previous_close=0.0000011,
        meta={"shortName": "Wojak USD"},
    )
    async with A.alt_client(
        tmp_path,
        monkeypatch,
        rows=[],
        transport=A.FakeTransport(bodies={"WOJAK-USD": body}),
    ) as harness:
        answer = (await harness.get("/market/quotes", symbols="WOJAK-USD")).json()

    assert answer["rows"][0]["name"] == "Wojak USD"
    assert answer["rows"][0]["class"] == "crypto"


# ---------------------------------------------------------------------------
# Where each symbol is resolved (adversarial (d))
# ---------------------------------------------------------------------------


async def test_equities_are_read_from_the_scan_and_cost_no_transport_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport()
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        response = await harness.get("/market/quotes", symbols="AAPL,MSFT,NVDA")
        scans = harness.web.count("tradingview")
        cached = await harness.get("/market/quotes", symbols="AAPL,MSFT,NVDA")

    assert response.status_code == 200
    assert [row["symbol"] for row in response.json()["rows"]] == ["AAPL", "MSFT", "NVDA"]
    assert transport.batches == [], "a stock must not be fetched over HTTP"
    assert scans == 1, "one scan for the population, not one per symbol"
    assert cached.headers["x-quotes-cache"] == "hit"
    assert harness.web.count("tradingview") == 1


async def test_a_mixed_call_asks_the_transport_for_the_alt_symbols_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        body = (
            await harness.get("/market/quotes", symbols="AAPL,BTC-USD,MSFT")
        ).json()

    assert transport.batches == [("BTC-USD",)]
    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "BTC-USD", "MSFT"]


# ---------------------------------------------------------------------------
# The cache and the scan generation
# ---------------------------------------------------------------------------


async def test_the_memo_is_keyed_by_the_symbols_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        first = await harness.get("/market/quotes", symbols="AAPL,BTC-USD")
        second = await harness.get("/market/quotes", symbols="AAPL,BTC-USD")
        other = await harness.get("/market/quotes", symbols="BTC-USD")

    assert first.headers["x-quotes-cache"] == "miss"
    assert second.headers["x-quotes-cache"] == "hit"
    assert other.headers["x-quotes-cache"] == "miss"
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert transport.asked == ["BTC-USD"]  # the symbol cache, not a second read


async def test_a_new_scan_generation_is_a_new_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The memo holds one document per (symbols, generation), so a scan moves it."""

    clock = M.Clock(start=1_000.0)
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), clock=clock, scan_ttl=5.0
    ) as harness:
        first = await harness.get("/market/quotes", symbols="AAPL")
        clock.advance(6.0)
        second = await harness.get("/market/quotes", symbols="AAPL")
        scans = harness.web.count("tradingview")

    assert first.headers["x-quotes-cache"] == "miss"
    assert second.headers["x-quotes-cache"] == "miss"
    assert second.headers["etag"] != first.headers["etag"]
    assert scans == 2


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _fifty_one() -> str:
    return ",".join(f"S{index:04d}" for index in range(51))


async def test_fifty_one_symbols_is_refused_by_name_and_costs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport()
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        response = await harness.get("/market/quotes", symbols=_fifty_one())

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "invalid_symbols"
    assert "50" in body["errorMessage"]
    assert transport.batches == []
    assert harness.web.count("tradingview") == 0


async def test_fifty_symbols_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symbols = ",".join(f"S{index:04d}" for index in range(50))
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=A.FakeTransport()
    ) as harness:
        response = await harness.get("/market/quotes", symbols=symbols)

    assert response.status_code == 200, response.text
    assert len(response.json()["missing"]) == 50


@pytest.mark.parametrize("asked", ["", "   ", ",,,", "!!,@@", "@@@"])
async def test_an_empty_or_unusable_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asked: str
) -> None:
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=A.FakeTransport()
    ) as harness:
        response = await harness.get("/market/quotes", symbols=asked)

    assert response.status_code == 422, (asked, response.text)
    body = response.json()
    assert body["error"] == "invalid_symbols"
    assert "AAPL" in body["errorMessage"]  # the sentence shows a legal example


async def test_an_unusable_token_beside_a_good_one_is_missing_and_never_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MSFT!`` and ``A A=F`` are not tickers, so they are not looked up.

    A malformed token that nevertheless ends in ``=F`` must not reach the
    transport: the suffix check runs on validated tokens only.
    """

    transport = A.FakeTransport()
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        body = (
            await harness.get("/market/quotes", symbols="AAPL,A A=F,MSFT!")
        ).json()

    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]
    assert body["missing"] == ["A A=F", "MSFT!"]
    assert transport.batches == []


# ---------------------------------------------------------------------------
# Validators: ETag, 304, HEAD
# ---------------------------------------------------------------------------


async def test_the_tag_is_weak_and_the_cache_control_is_no_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=A.FakeTransport()
    ) as harness:
        response = await harness.get("/market/quotes", symbols="AAPL")

    assert response.headers["etag"].startswith('W/"qts-')
    assert response.headers["cache-control"] == "no-cache"


async def test_a_matching_tag_is_a_304_with_no_fetch_and_no_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(bodies={"BTC-USD": _bitcoin()})
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=transport
    ) as harness:
        first = await harness.get("/market/quotes", symbols="AAPL,BTC-USD")
        transport.raise_on_fetch = True
        revalidated = await harness.client.get(
            "/market/quotes",
            params={"symbols": "AAPL,BTC-USD"},
            headers={"if-none-match": first.headers["etag"]},
        )
        scans = harness.web.count("tradingview")

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert revalidated.headers["x-quotes-cache"] == "hit"
    assert scans == 1
    assert transport.asked == ["BTC-USD"]


async def test_head_answers_the_get_headers_and_none_of_the_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=A.FakeTransport()
    ) as harness:
        got = await harness.get("/market/quotes", symbols="AAPL,MSFT")
        head = await harness.client.head("/market/quotes", params={"symbols": "AAPL,MSFT"})
        revalidated = await harness.client.head(
            "/market/quotes",
            params={"symbols": "AAPL,MSFT"},
            headers={"if-none-match": got.headers["etag"]},
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(got.content))
    assert head.headers["etag"] == got.headers["etag"]
    assert revalidated.status_code == 304


async def test_the_tag_moves_when_the_symbol_list_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with A.alt_client(
        tmp_path, monkeypatch, rows=_rows(), transport=A.FakeTransport()
    ) as harness:
        one = await harness.get("/market/quotes", symbols="AAPL")
        two = await harness.get("/market/quotes", symbols="AAPL,MSFT")

    assert one.headers["etag"] != two.headers["etag"]
