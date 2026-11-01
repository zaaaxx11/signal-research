"""Contract for ``GET /market/alt`` — the nav's crypto, futures and forex tables.

What is pinned here, in the order the route computes it:

* **the document** — ``{as_of, class, universe_total, measured, per, method,
  rows}``, one row ``{symbol, name, price, change_pct, volume, currency}``;
* **the catalogue is data** — 40 crypto majors, 25 front-month futures, 20 FX
  pairs, curated in :mod:`app.api.alt`, so ``universe_total`` is the size of a
  list rather than a number a scan happens to return;
* **one batched read per class** — 40 symbols arrive in one transport call, and
  a warm request makes none (**adversarial (c)**: a class-keyed cache, so two
  classes cannot share a slot);
* **a symbol that fails or returns no bar is counted and absent** — never a row
  with a zero price, and an empty ``timestamp`` array is exactly that case
  (**adversarial (b)**);
* **the move is against the session before** — the last non-zero close over
  ``chartPreviousClose``, which on a closed market is the last session's close
  against the one before it, not against a week-old print (**adversarial (a)**);
* **the 60 s cache** — the second request inside the window makes zero transport
  calls, proved with a counting transport;
* **the validator** — weak ``W/"alt-…"``, ``Cache-Control: no-cache``, a ``304``
  decided from the inputs before any fetch, and a ``HEAD`` that carries the
  ``GET``'s length and none of its bytes;
* **``per`` is clamped**, and an unknown class is a ``422 unknown_class`` whose
  sentence names the three legal classes;
* **the budgets** — cold under 4 s, warm under 5 ms, ``304`` under 2 ms (the two
  tight ones measured as deltas against the app's cheapest document, so a busy
  box is not a regression).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import pytest

from tests import _alt as A

_DOCUMENT_KEYS = frozenset(
    {"as_of", "class", "universe_total", "measured", "per", "method", "rows"}
)
_ROW_KEYS = frozenset({"symbol", "name", "price", "change_pct", "volume", "currency"})


def _catalogue(name: str) -> tuple[Any, ...]:
    from app.api.alt import CATALOGUE

    return CATALOGUE[name]


# ---------------------------------------------------------------------------
# The catalogue is data
# ---------------------------------------------------------------------------


def test_the_catalogue_has_the_documented_size_and_no_duplicates() -> None:
    from app.api.alt import CLASSES, CRYPTO, FOREX, FUTURES

    assert CLASSES == ("crypto", "futures", "forex")
    assert (len(CRYPTO), len(FUTURES), len(FOREX)) == (40, 25, 20)
    for catalogue in (CRYPTO, FUTURES, FOREX):
        symbols = [instrument.symbol for instrument in catalogue]
        assert len(set(symbols)) == len(symbols)
        assert all(instrument.name for instrument in catalogue)
    assert CRYPTO[0].symbol == "BTC-USD"
    assert {"ES=F", "NQ=F", "YM=F", "RTY=F", "CL=F", "GC=F", "ZN=F", "6E=F"} <= {
        instrument.symbol for instrument in FUTURES
    }
    assert len([instrument for instrument in FOREX if instrument.symbol.endswith("=X")]) == 20


# ---------------------------------------------------------------------------
# The document, per class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["crypto", "futures", "forex"])
async def test_each_class_publishes_a_row_per_symbol_with_a_price_and_a_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    catalogue = _catalogue(name)
    transport = A.FakeTransport(bodies=A.priced(catalogue))
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        response = await harness.get("/market/alt", **{"class": name})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _DOCUMENT_KEYS, sorted(body)
    assert body["class"] == name
    assert body["per"] == 50
    assert body["universe_total"] == len(catalogue)
    assert body["measured"] == len(catalogue)
    assert len(body["rows"]) == len(catalogue)
    assert isinstance(body["method"], str) and "close" in body["method"]
    assert body["as_of"].endswith("+00:00")
    assert [row["symbol"] for row in body["rows"]] == [i.symbol for i in catalogue]
    for index, row in enumerate(body["rows"]):
        assert set(row) == _ROW_KEYS, sorted(row)
        assert isinstance(row["price"], float) and row["price"] > 0
        assert math.isfinite(row["change_pct"])
        assert row["change_pct"] == pytest.approx(index * 1.0, abs=1e-6)
        assert row["name"]
        assert row["currency"]
        assert isinstance(row["volume"], float) and row["volume"] >= 0
    # One batched read for the whole class, not forty.
    assert len(transport.batches) == 1
    assert set(transport.batches[0]) == {instrument.symbol for instrument in catalogue}


async def test_the_move_is_price_over_the_previous_session_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with A.alt_client(
        tmp_path,
        monkeypatch,
        transport=A.FakeTransport(
            bodies={"BTC-USD": A.chart_body("BTC-USD", closes=(110.0,), previous_close=100.0)}
        ),
    ) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    row = body["rows"][0]
    assert row["change_pct"] == pytest.approx(10.0)
    assert (row["symbol"], row["name"]) == ("BTC-USD", "Bitcoin")


async def test_the_volume_is_the_sum_of_the_session_s_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = A.chart_body(
        "BTC-USD", closes=(100.0, 101.0, 102.0), volumes=(10.0, 20.0, None), previous_close=100.0
    )
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies={"BTC-USD": body})
    ) as harness:
        answer = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    assert answer["rows"][0]["volume"] == pytest.approx(30.0)


async def test_the_currency_is_the_provider_s_and_falls_back_to_the_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = {
        "EURUSD=X": A.chart_body("EURUSD=X", currency="EUR", previous_close=100.0),
        "GBPUSD=X": A.chart_body("GBPUSD=X", currency=None, previous_close=100.0),
    }
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=bodies)
    ) as harness:
        body = (await harness.get("/market/alt", **{"class": "forex"})).json()

    currencies = {row["symbol"]: row["currency"] for row in body["rows"]}
    assert currencies == {"EURUSD=X": "EUR", "GBPUSD=X": "USD"}


# ---------------------------------------------------------------------------
# Failing symbols and empty series (adversarial (b))
# ---------------------------------------------------------------------------


async def test_a_failing_symbol_is_missing_but_still_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fetch and an empty series are two shapes of "no measurement"."""

    from app.api.alt import CRYPTO

    bodies: dict[str, Any] = A.priced(CRYPTO)
    bodies["ETH-USD"] = None  # the provider refused this symbol
    bodies["SOL-USD"] = A.empty_body("SOL-USD")  # a 200 with no timestamps at all
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=bodies)
    ) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    symbols = [row["symbol"] for row in body["rows"]]
    assert "ETH-USD" not in symbols and "SOL-USD" not in symbols
    assert body["universe_total"] == len(CRYPTO)
    assert body["measured"] == len(CRYPTO) - 2
    assert len(body["rows"]) == body["measured"]
    assert all(row["price"] > 0 for row in body["rows"])
    assert all(math.isfinite(row["change_pct"]) for row in body["rows"])


async def test_a_symbol_with_no_previous_close_is_not_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A price without a reference cannot state a move, so it is not published."""

    bodies = {
        "BTC-USD": A.chart_body("BTC-USD", previous_close=None),
        "ETH-USD": A.chart_body("ETH-USD", previous_close=0.0),
    }
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=bodies)
    ) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    assert body["rows"] == []
    assert body["measured"] == 0
    assert body["universe_total"] == len(_catalogue("crypto"))


async def test_an_empty_answer_is_asked_once_more_and_the_symbol_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint intermittently answers a live symbol with no bars at all.

    One recovery read of the empty subset turns a transient hole into a row; the
    second answer is the price, so the table does not lose a symbol to one flaky
    response.
    """

    from app.api.alt import CRYPTO

    bodies = A.priced(CRYPTO)
    flaky = "USDC-USD"

    class Flaky(A.FakeTransport):
        async def payloads(self, symbols: Any) -> dict[str, Any]:
            self.batches.append(tuple(symbols))
            first = len(self.batches) == 1
            return {
                symbol: (A.empty_body(symbol) if first and symbol == flaky else self.bodies.get(symbol))
                for symbol in symbols
            }

    transport = Flaky(bodies=bodies)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    assert body["measured"] == len(CRYPTO)
    assert flaky in [row["symbol"] for row in body["rows"]]
    assert len(transport.batches) == 2
    assert transport.batches[1] == (flaky,)


async def test_a_refused_symbol_is_not_asked_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``None`` answer is the provider saying it has no such symbol: one ask."""

    from app.api.alt import CRYPTO

    bodies = A.priced(CRYPTO)
    bodies["USDC-USD"] = None
    transport = A.FakeTransport(bodies=bodies)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    assert body["measured"] == len(CRYPTO) - 1
    assert len(transport.batches) == 1
    assert transport.count() == len(CRYPTO)


# ---------------------------------------------------------------------------
# The closed market (adversarial (a))
# ---------------------------------------------------------------------------


async def test_a_closed_market_answers_with_its_last_session_and_its_prior_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saturday's futures table shows Friday's close against Thursday's close.

    ``regularMarketPrice`` is ``0`` here, as a closed market can send it: the
    price must come from the last non-zero bar, and the reference must be the
    session before — never a week-old print.
    """

    body = A.chart_body(
        "ES=F",
        closes=(7_700.0, 7_640.0, 7_614.0),
        previous_close=7_754.25,
        meta={"regularMarketPrice": 0.0},
        start=A.BASE_T - 6 * 86_400,
        step=86_400,
    )
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies={"ES=F": body})
    ) as harness:
        answer = (await harness.get("/market/alt", **{"class": "futures"})).json()

    row = answer["rows"][0]
    assert row["price"] == 7_614.0
    assert row["change_pct"] == pytest.approx(round((7_614.0 / 7_754.25 - 1.0) * 100.0, 4))
    assert row["change_pct"] < 0


async def test_the_last_usable_close_wins_over_a_null_or_zero_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forming bar is null, and a closed print is zero: skip, don't publish."""

    body = A.chart_body(
        "BTC-USD", closes=(100.0, 0.0, 101.5, None, 0.0), previous_close=100.0
    )
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies={"BTC-USD": body})
    ) as harness:
        answer = (await harness.get("/market/alt", **{"class": "crypto"})).json()

    assert answer["rows"][0]["price"] == 101.5


# ---------------------------------------------------------------------------
# The 60 s cache and its key
# ---------------------------------------------------------------------------


async def test_the_second_request_inside_sixty_seconds_makes_zero_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO

    transport = A.FakeTransport(bodies=A.priced(CRYPTO))
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        first = await harness.get("/market/alt", **{"class": "crypto"})
        second = await harness.get("/market/alt", **{"class": "crypto"})

    assert first.status_code == second.status_code == 200
    assert first.headers["x-alt-cache"] == "miss"
    assert second.headers["x-alt-cache"] == "hit"
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert transport.count() == len(CRYPTO)


async def test_the_cache_expires_after_sixty_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO

    clock = A.M.Clock(start=1_000.0)
    transport = A.FakeTransport(bodies=A.priced(CRYPTO))
    async with A.alt_client(
        tmp_path, monkeypatch, transport=transport, clock=clock
    ) as harness:
        first = await harness.get("/market/alt", **{"class": "crypto"})
        clock.advance(59.0)
        still = await harness.get("/market/alt", **{"class": "crypto"})
        clock.advance(2.0)
        expired = await harness.get("/market/alt", **{"class": "crypto"})

    assert [first.headers["x-alt-cache"], still.headers["x-alt-cache"]] == ["miss", "hit"]
    assert expired.headers["x-alt-cache"] == "miss"
    assert transport.count() == 2 * len(CRYPTO)
    assert expired.headers["etag"] != first.headers["etag"]


async def test_the_class_cache_does_not_leak_between_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO, FUTURES

    bodies = {**A.priced(CRYPTO, base=100.0), **A.priced(FUTURES, base=5_000.0)}
    transport = A.FakeTransport(bodies=bodies)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        crypto = (await harness.get("/market/alt", **{"class": "crypto"})).json()
        futures = (await harness.get("/market/alt", **{"class": "futures"})).json()
        again = (await harness.get("/market/alt", **{"class": "crypto"}))

    assert {row["symbol"] for row in crypto["rows"]} == {i.symbol for i in CRYPTO}
    assert {row["symbol"] for row in futures["rows"]} == {i.symbol for i in FUTURES}
    assert crypto["rows"][0]["price"] == pytest.approx(100.0)
    assert futures["rows"][0]["price"] == pytest.approx(5_000.0)
    assert again.headers["x-alt-cache"] == "hit"
    assert again.json()["rows"][0]["price"] == pytest.approx(100.0)
    assert transport.count() == len(CRYPTO) + len(FUTURES)


async def test_two_classes_requested_concurrently_do_not_share_one_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (c): both cold, both in flight, each in its own slot."""

    import asyncio

    from app.api.alt import CRYPTO, FOREX

    bodies = {**A.priced(CRYPTO, base=100.0), **A.priced(FOREX, base=1.0, step=0.01)}
    transport = A.FakeTransport(bodies=bodies, delay_s=0.01)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        crypto, forex = await asyncio.gather(
            harness.get("/market/alt", **{"class": "crypto"}),
            harness.get("/market/alt", **{"class": "forex"}),
        )
        crypto_again = await harness.get("/market/alt", **{"class": "crypto"})
        forex_again = await harness.get("/market/alt", **{"class": "forex"})

    crypto_body, forex_body = crypto.json(), forex.json()
    assert {row["symbol"] for row in crypto_body["rows"]} == {i.symbol for i in CRYPTO}
    assert {row["symbol"] for row in forex_body["rows"]} == {i.symbol for i in FOREX}
    assert crypto_body["rows"][0]["price"] == pytest.approx(100.0)
    assert forex_body["rows"][0]["price"] == pytest.approx(1.0)
    assert crypto_again.headers["x-alt-cache"] == "hit"
    assert forex_again.headers["x-alt-cache"] == "hit"
    assert {row["symbol"] for row in crypto_again.json()["rows"]} == {
        i.symbol for i in CRYPTO
    }


# ---------------------------------------------------------------------------
# per, the class parameter, and their refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("asked", "expected"), [("0", 1), ("-5", 1), ("3", 3), ("999", 200)])
async def test_per_is_clamped_and_echoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asked: str, expected: int
) -> None:
    from app.api.alt import CRYPTO, PER_MAX

    assert PER_MAX == 200
    transport = A.FakeTransport(bodies=A.priced(CRYPTO))
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        body = (await harness.get("/market/alt", **{"class": "crypto", "per": asked})).json()

    assert body["per"] == expected
    assert len(body["rows"]) == min(expected, len(CRYPTO))
    assert body["measured"] == len(body["rows"])
    assert body["universe_total"] == len(CRYPTO)


async def test_an_unknown_class_is_refused_by_name_and_costs_no_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport()
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        response = await harness.get("/market/alt", **{"class": "commodities"})

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unknown_class"
    assert "commodities" in body["errorMessage"]
    for legal in ("crypto", "futures", "forex"):
        assert legal in body["errorMessage"]
    assert transport.batches == []


async def test_an_empty_class_parameter_is_refused_not_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with A.alt_client(tmp_path, monkeypatch, transport=A.FakeTransport()) as harness:
        response = await harness.get("/market/alt", **{"class": ""})

    assert response.status_code == 422
    assert response.json()["error"] == "unknown_class"


# ---------------------------------------------------------------------------
# Validators: ETag, 304, HEAD
# ---------------------------------------------------------------------------


async def test_the_tag_is_weak_and_the_cache_control_is_no_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO

    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=A.priced(CRYPTO))
    ) as harness:
        response = await harness.get("/market/alt", **{"class": "crypto"})

    assert response.headers["etag"].startswith('W/"alt-')
    assert response.headers["cache-control"] == "no-cache"


async def test_a_matching_tag_is_a_304_with_no_fetch_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO

    transport = A.FakeTransport(bodies=A.priced(CRYPTO))
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        first = await harness.get("/market/alt", **{"class": "crypto"})
        transport.raise_on_fetch = True
        revalidated = await harness.client.get(
            "/market/alt",
            params={"class": "crypto"},
            headers={"if-none-match": first.headers["etag"]},
        )
        # A cold process still answers the tag from its inputs, so a restart
        # does not force a 200.
        from app.api.alt import AltState

        harness.app.state.alt = AltState(clock=A.M.Clock(start=1_000.0), transport=transport)
        cold = await harness.client.get(
            "/market/alt",
            params={"class": "crypto"},
            headers={"if-none-match": first.headers["etag"]},
        )

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert revalidated.headers["x-alt-cache"] == "hit"
    assert cold.status_code == 304
    assert cold.headers["x-alt-cache"] == "miss"
    assert transport.batches == [tuple(i.symbol for i in CRYPTO)]


async def test_head_answers_the_get_headers_and_none_of_the_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO

    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=A.priced(CRYPTO))
    ) as harness:
        got = await harness.get("/market/alt", **{"class": "crypto"})
        head = await harness.client.head("/market/alt", params={"class": "crypto"})
        revalidated = await harness.client.head(
            "/market/alt",
            params={"class": "crypto"},
            headers={"if-none-match": got.headers["etag"]},
        )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(got.content))
    assert head.headers["etag"] == got.headers["etag"]
    assert head.headers["cache-control"] == "no-cache"
    assert revalidated.status_code == 304


async def test_the_tag_moves_with_the_class_and_the_page_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.alt import CRYPTO, FUTURES

    bodies = {**A.priced(CRYPTO), **A.priced(FUTURES)}
    async with A.alt_client(
        tmp_path, monkeypatch, transport=A.FakeTransport(bodies=bodies)
    ) as harness:
        crypto = await harness.get("/market/alt", **{"class": "crypto"})
        futures = await harness.get("/market/alt", **{"class": "futures"})
        short = await harness.get("/market/alt", **{"class": "crypto", "per": "5"})

    assert len({crypto.headers["etag"], futures.headers["etag"], short.headers["etag"]}) == 3


# ---------------------------------------------------------------------------
# An outage is a 503, not an empty table
# ---------------------------------------------------------------------------


async def test_a_transport_outage_is_a_503_in_the_house_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = A.FakeTransport(raise_on_fetch=True)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        response = await harness.get("/market/alt", **{"class": "crypto"})

    assert response.status_code == 503, response.text
    assert response.json()["error"] == "alt_unavailable"


# ---------------------------------------------------------------------------
# The timing goals
# ---------------------------------------------------------------------------


async def _fastest(
    client: Any,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    runs: int = 15,
) -> tuple[Any, float]:
    """The answer to ``path`` and the fastest of ``runs`` round trips, in ms."""

    best = float("inf")
    response = None
    for _ in range(runs):
        mark = time.perf_counter()
        response = await client.get(path, params=params, headers=headers)
        best = min(best, (time.perf_counter() - mark) * 1_000.0)
    return response, best


async def test_cold_warm_and_304_stay_inside_the_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold under 4 s; warm under 5 ms and 304 under 2 ms above the app's floor.

    The tight budgets are measured as deltas against ``/market/calendar`` — one
    ``stat``, one memo lookup, one response — timed in the same loop, because an
    absolute millisecond count on a shared box measures the box.
    """

    from app.api.alt import CRYPTO

    transport = A.FakeTransport(bodies=A.priced(CRYPTO), delay_s=0.02)
    async with A.alt_client(tmp_path, monkeypatch, transport=transport) as harness:
        started = time.perf_counter()
        first = await harness.get("/market/alt", **{"class": "crypto"})
        cold_ms = (time.perf_counter() - started) * 1_000.0
        tag = first.headers["etag"]

        floor, floor_ms = await _fastest(harness.client, "/market/calendar")
        floor_tag = floor.headers["etag"]
        _, floor_304_ms = await _fastest(
            harness.client, "/market/calendar", headers={"if-none-match": floor_tag}
        )

        warm, warm_ms = await _fastest(
            harness.client, "/market/alt", params={"class": "crypto"}
        )
        answer, cheap_ms = await _fastest(
            harness.client,
            "/market/alt",
            params={"class": "crypto"},
            headers={"if-none-match": tag},
        )

    assert first.status_code == 200 and first.headers["x-alt-cache"] == "miss"
    assert warm.headers["x-alt-cache"] == "hit"
    assert answer.status_code == 304
    assert cold_ms < 4_000.0, f"cold {cold_ms:.1f} ms"
    assert warm_ms - floor_ms < 5.0, f"warm {warm_ms:.2f} ms over a {floor_ms:.2f} ms floor"
    assert cheap_ms - floor_304_ms < 2.0, (
        f"304 {cheap_ms:.2f} ms over a {floor_304_ms:.2f} ms floor"
    )
