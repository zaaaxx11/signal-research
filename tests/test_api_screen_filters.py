"""``GET /screens/filters`` and the ``idx`` wiring, over the ASGI app (T10).

Two promises, both HTTP-level:

* the **document** is served from memory, revalidates on its own digest (``304``
  with no body) and reports availability as a *runtime* fact — remove the
  membership snapshot and ``idx`` is disabled with the reason, not empty;
* ``POST /screens/select`` **enriches its universe with index membership**
  before it filters, so ``{"key": "idx", "op": "eq", "value": "sp500"}`` answers
  the S&P 500 rather than every row. Without that wiring the bucket reads a cell
  no route ever attached and the honest answer is zero rows — which is why the
  count is asserted here and not only in the bucket unit tests.

The ETag half is the stale-row bug this task exists to prevent: the membership
snapshot is a new input to the answer, so it is a new line in
:func:`app.api.screens._data_revision` (``v2`` → ``v3``, ``members=``). A warm
client pays exactly one ``200`` after the upgrade; a changed snapshot pays one
``200`` and never a stale ``304``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.api.sectors import tally_sectors
from app.screen import index_membership
from app.screen.universe import PARITY_COLUMNS, build_universe
from tests import _market as M
from tests import _screen as S

BAD_TOKEN = [
    # key, value, the code, and the token the message must name
    ("idx", "sp900", "unknown_bucket", "sp900"),
    ("cap", "price:u5", "unknown_bucket", "price:u5"),
    ("theme", "ai", "unavailable_filter_key", "theme"),
]


def select_body(filters: list[dict[str, Any]], **criteria: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"limit": 5}
    block.update(criteria)
    return {"mode": "criteria", "region": "us", "criteria": {**block, "filters": filters}}


def members_universe(members: tuple[str, ...]) -> Any:
    """A ``build_universe`` stand-in that answers exactly ``members``.

    The route under test is the wiring — scan → universe → membership
    enrichment → filter — and the one input the suite cannot invent is a
    503-name universe, because the fake scanner holds 27 rows by design. So the
    *rows* are supplied and everything downstream of them is real.
    """

    def build(rows: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbol,
                "name": f"{symbol} Inc",
                "price": 100.0,
                "change_pct": 1.0,
                "market_cap_usd": 1e9,
                "volume": 1_000_000.0,
            }
            for symbol in members
        ]

    return build


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


async def test_get_screens_filters_serves_the_five_tabs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.get("/screens/filters")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()

    assert body["schema_version"] == "screener-3"
    assert [(group["id"], len(group["controls"])) for group in body["groups"]] == [
        ("descriptive", 25),
        ("fundamental", 37),
        ("technical", 20),
        ("news", 2),
        ("etf", 1),
    ]
    assert body["membership"]["counts"]["sp500"] > 480
    assert body["membership"]["as_of"], "a membership count without a stamp is a claim"

    # Every control states its availability, and every option its predicate.
    for group in body["groups"]:
        for control in group["controls"]:
            assert isinstance(control["available"], bool)
            if not control["available"]:
                assert control["reason"], (group["id"], control["id"])
            for option in control["options"]:
                if option["available"]:
                    assert option["predicate"], (control["id"], option["id"])
                else:
                    assert option["reason"], (control["id"], option["id"])

    group = body["groups"][0]
    idx = next(control for control in group["controls"] if control["id"] == "idx")
    assert idx["available"] is True
    assert [option["predicate"] for option in idx["options"]] == [
        "'sp500' in index_members",
        "'ndx' in index_members",
        "'dji' in index_members",
        "'rut' in index_members",
    ]


async def test_the_sector_control_publishes_the_live_taxonomy_with_its_counts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``sec`` is enabled by real numbers: 21 labels, 21 predicates, 21 counts.

    The labels are not typed here: they are what
    :func:`app.api.sectors.tally_sectors` derives from the same universe the
    ``/market/groups`` sector table counts (parity columns, no cap floor, every
    stock subtype), so the menu and the table cannot publish two vocabularies.
    A count is a row count from that population, and the predicate is the value
    ``sec`` compiles to — ``sector eq <label>``, label byte for byte.
    """

    rows = M.universe()
    async with M.market_client(tmp_path, monkeypatch, rows=rows) as harness:
        response = await harness.get("/screens/filters")

    assert response.status_code == 200, response.text
    controls = {
        control["id"]: control
        for control in response.json()["groups"][0]["controls"]
    }
    sector = controls["sec"]
    assert sector["available"] is True, "the Sector control must not be disabled"
    assert sector["options_source"] == "/market/sectors", "kept for compatibility"

    built = build_universe(
        [{"symbol": row.symbol, **row.cells()} for row in rows],
        region="us",
        min_market_cap=0,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
        asset_class="stock",
    )
    reader = tally_sectors(built)
    expected = {tally.label: tally.count for tally in reader.values()}
    assert len(expected) == 21, "the live distribution is the provider's 21 sectors"

    published = sector["options"]
    assert len(published) == 21
    assert [option["label"] for option in published] == sorted(
        expected, key=str.casefold
    ), "the menu's labels are the sector reader's labels"
    assert {option["label"]: option["count"] for option in published} == expected
    for option in published:
        assert option["predicate"] == f"sector eq {option['label']}", (
            f"{option['label']!r} does not compile to a sec predicate"
        )
        assert isinstance(option["count"], int) and option["count"] > 0
    assert sum(option["count"] for option in published) == 5_576


async def test_the_document_revalidates_on_its_own_digest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.get("/screens/filters")
        etag = first.headers["etag"]
        second = await harness.client.get("/screens/filters", headers={"If-None-Match": etag})
        third = await harness.client.get("/screens/filters")

    assert etag.startswith('"flt-') and etag.endswith('"'), etag
    # The tag names the representation: ``"flt-<32 hex>"`` for the identity
    # bytes and ``"flt-<32 hex>-gzip"`` for the compressed ones (httpx asks for
    # gzip by default, so this fetch gets the gzip tag).
    body = etag[len('"flt-') : -1]
    assert body.endswith("-gzip") or len(body) == 32, etag
    digest = body[: -len("-gzip")] if body.endswith("-gzip") else body
    assert len(digest) == 32, "the prefix plus a 128-bit digest"
    assert second.status_code == 304
    assert second.content == b"", "a 304 carries no body"
    assert second.headers["etag"] == etag
    assert "must-revalidate" in second.headers["cache-control"]
    assert third.headers["etag"] == etag, "the document is deterministic"


async def test_availability_is_a_runtime_fact(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        warm = (await harness.client.get("/screens/filters")).headers["etag"]
        monkeypatch.setattr(
            index_membership, "load", lambda path=None: index_membership.Snapshot()
        )
        monkeypatch.setattr(index_membership, "available", lambda: False)
        response = await harness.client.get("/screens/filters")

    assert response.status_code == 200, response.text
    body = response.json()
    idx = next(
        control for control in body["groups"][0]["controls"] if control["id"] == "idx"
    )
    assert idx["available"] is False
    assert "index-membership snapshot" in idx["reason"]
    assert len(idx["options"]) == 4, "a disabled control keeps its options"
    assert all(option["predicate"] is None for option in idx["options"])
    assert body["membership"]["available"] is False
    assert body["membership"]["counts"] == {}
    assert response.headers["etag"] != warm, (
        "installing or removing the snapshot is a body change, so the tag moves"
    )


# ---------------------------------------------------------------------------
# idx over HTTP
# ---------------------------------------------------------------------------


async def test_idx_sp500_answers_the_members_over_http(
    tmp_path: Path, monkeypatch: Any
) -> None:
    members = index_membership.members("sp500")
    assert len(members) > 400, "the vendored snapshot is the population"

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", members_universe(members))
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "idx", "op": "eq", "value": "sp500"}], limit=100),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(members), (
        "every S&P 500 member is a row; the route must enrich index_members"
    )
    assert body["total"] > 400
    assert body["returned"] == 100
    assert body["filters"] == [
        {"key": "idx", "field": None, "op": "eq", "value": "idx:sp500"}
    ]


async def test_a_smaller_index_answers_its_own_members(
    tmp_path: Path, monkeypatch: Any
) -> None:
    members = index_membership.members("dji")
    assert len(members) == 30

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(
            screens_api, "build_universe", members_universe(index_membership.members("sp500"))
        )
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "idx", "op": "eq", "value": "dji"}]),
        )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 30


async def test_a_bucket_token_filters_the_page_over_http(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The wire shape stays ``{key, op: eq, value: <token>}`` end to end."""

    rows = [
        {"symbol": "CHEAP", "name": "Cheap", "price": 4.5, "market_cap_usd": 3e9},
        {"symbol": "PRICEY", "name": "Pricey", "price": 55.0, "market_cap_usd": 3e9},
    ]

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", lambda _rows, **_: list(rows))
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "sh_price", "op": "eq", "value": "price:u5"}]),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [row["symbol"] for row in body["rows"]] == ["CHEAP"]
    assert all(row["price"] < 5 for row in body["rows"])
    assert body["filters"] == [
        {"key": "sh_price", "field": None, "op": "eq", "value": "price:u5"}
    ]


async def test_a_bucket_whose_source_is_missing_is_422_unavailable_bucket(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No snapshot: ``idx`` is refused by name, never answered with 0 rows."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        monkeypatch.setattr(
            index_membership, "load", lambda path=None: index_membership.Snapshot()
        )
        monkeypatch.setattr(index_membership, "available", lambda: False)
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "idx", "op": "eq", "value": "sp500"}]),
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unavailable_bucket"
    assert "index-membership snapshot" in body["errorMessage"]
    assert "total" not in body, "a source that is missing is not an empty market"


async def test_a_bad_token_is_422_with_a_specific_code(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        for key, value, code, named in BAD_TOKEN:
            response = await harness.client.post(
                "/screens/select", json=select_body([{"key": key, "op": "eq", "value": value}])
            )
            assert response.status_code == 422, (key, value, response.text)
            body = response.json()
            assert set(body) == {"error", "errorMessage"}
            assert body["error"] == code, (key, value, body)
            assert "total" not in body, "a refusal is never an empty page"
            assert named in body["errorMessage"], (key, value, body)


# ---------------------------------------------------------------------------
# The new groups, over HTTP
# ---------------------------------------------------------------------------

#: A five-row universe whose cells make every example filter answer a known
#: population. The route under test is the wiring — bucket id → predicate →
#: `total` — so the rows are supplied and everything downstream is real.
BUCKET_ROWS: tuple[dict[str, Any], ...] = (
    {"symbol": "CHEAP", "name": "Cheap", "price": 10.0, "pe_ttm": 12.0, "rsi_14": 75.0, "ret_1w": 3.0, "market_cap_usd": 1e9},
    {"symbol": "MID", "name": "Mid", "price": 20.0, "pe_ttm": 14.9, "rsi_14": 69.9, "ret_1w": 0.5, "market_cap_usd": 2e9},
    {"symbol": "RICH", "name": "Rich", "price": 30.0, "pe_ttm": 60.0, "rsi_14": 50.0, "ret_1w": -2.0, "market_cap_usd": 3e9},
    {"symbol": "EDGE", "name": "Edge", "price": 40.0, "pe_ttm": 15.0, "rsi_14": 70.0, "ret_1w": 0.0, "market_cap_usd": 4e9},
    {"symbol": "NONE", "name": "None", "price": 50.0, "pe_ttm": None, "rsi_14": None, "ret_1w": None, "market_cap_usd": 5e9},
)


@pytest.mark.parametrize(
    ("key", "value", "expected", "canonical"),
    [
        ("fa_pe", "pe:u15", ["MID", "CHEAP"], "pe:u15"),
        ("ta_rsi", "rsi:overbought", ["EDGE", "CHEAP"], "rsi:ob70"),
        ("ta_perf", "1wup", ["MID", "CHEAP"], "1wup"),
    ],
)
async def test_a_representative_filter_answers_over_http(
    tmp_path: Path,
    monkeypatch: Any,
    key: str,
    value: str,
    expected: list[str],
    canonical: str,
) -> None:
    """Three groups, one wire shape: ``{key, op: eq, value: <token>}``."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(
            screens_api, "build_universe", lambda _rows, **_: [dict(row) for row in BUCKET_ROWS]
        )
        response = await harness.client.post(
            "/screens/select", json=select_body([{"key": key, "op": "eq", "value": value}])
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["symbol"] for row in body["rows"]] == expected
    assert body["total"] == len(expected)
    echoed = body["filters"][0]
    if key in ("ta_perf", "ta_perf2", "ta_volatility"):
        # A choice key compiles the token; the envelope echoes the predicate.
        assert (echoed["field"], echoed["op"], echoed["value"]) == ("ret_1w", "gt", 0.0)
    else:
        assert echoed["value"] == canonical, (
            "the envelope echoes the canonical spelling, so a synonym is not a second bucket"
        )


async def test_a_symbol_list_filter_answers_only_those_rows(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``{"key": "symbol", "op": "in", "value": [...]}`` over the real route.

    The wire value is a list, which is the one filter shape a ticker input
    writes; the answer is those rows and nothing else, and the envelope echoes
    the canonical (upper-cased, dot-spelled) tickers the request became.
    """

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(
            screens_api, "build_universe", lambda _rows, **_: [dict(row) for row in BUCKET_ROWS]
        )
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "symbol", "op": "in", "value": ["cheap", "RICH"]}]),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["symbol"] for row in body["rows"]} == {"CHEAP", "RICH"}
    assert body["total"] == 2
    assert body["filters"][0]["value"] == ["CHEAP", "RICH"], (
        "the envelope echoes the canonical tickers, not the caller's spelling"
    )


async def test_an_unavailable_bucket_is_422_over_http(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "fa_divgrowth", "op": "eq", "value": "divgrowth:cy9"}]),
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unavailable_bucket"
    assert "no series" in body["errorMessage"]
    assert "total" not in body


async def test_a_misspelled_token_is_422_unknown_bucket(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=select_body([{"key": "fa_pe", "op": "eq", "value": "pe:u999"}]),
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unknown_bucket"
    assert "pe:u999" in body["errorMessage"]
    assert "total" not in body


# ---------------------------------------------------------------------------
# The order and the signal, over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sort", ["pe_ttm", "-pe_ttm", "market_cap", "gainers"])
async def test_sort_accepts_the_order_by_names(
    tmp_path: Path, monkeypatch: Any, sort: str
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=select_body([], sort=sort))

    assert response.status_code == 200, response.text
    assert response.json()["sort"] == sort


async def test_an_unknown_sort_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select", json=select_body([], sort="sectr")
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unknown_sort"
    assert "pe_ttm" in body["errorMessage"]


async def test_perf_4w_orders_by_the_one_month_return_over_http(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The live 422: the grid's "Perf Month" header sends ``-perf_4w``.

    The row spells that cell ``ret_1m``; the route has to translate the catalogue
    spelling instead of refusing it, and the page has to arrive ordered by the
    translated column — not merely 200 with the default order.
    """

    rows = [
        {"symbol": "MID", "name": "Mid", "price": 10.0, "market_cap_usd": 1e9, "ret_1m": 3.0},
        {"symbol": "TOP", "name": "Top", "price": 10.0, "market_cap_usd": 1e9, "ret_1m": 12.0},
        {"symbol": "BOTTOM", "name": "Bottom", "price": 10.0, "market_cap_usd": 1e9, "ret_1m": -4.0},
        {"symbol": "NONE", "name": "None", "price": 10.0, "market_cap_usd": 1e9, "ret_1m": None},
    ]

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(
            screens_api, "build_universe", lambda _rows, **_: [dict(row) for row in rows]
        )
        response = await harness.client.post(
            "/screens/select", json=select_body([], sort="-perf_4w")
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sort"] == "-perf_4w", "the request echoes the spelling the grid sent"
    assert [row["symbol"] for row in body["rows"]] == [
        "BOTTOM",
        "MID",
        "TOP",
        "NONE",
    ], "ascending one-month return, missing last"


async def test_every_default_screener_column_sorts_both_ways_over_http(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every column the screener opens with, pressed in both directions.

    The keys come from the client's own ``defaultColumns.ts`` — the file that
    sends them — so a column added there without a rank field fails here instead
    of shipping a header that answers 422. ``no`` is the one client-only column
    and must stay refused, with a message that says why.
    """

    from app.screen.rank import CLIENT_ONLY_SORTS

    columns = S.default_screener_columns()
    requestable = [key for key in columns if key not in CLIENT_ONLY_SORTS]
    assert len(requestable) >= 13, f"the default set is the whole table: {columns}"

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        for key in requestable:
            for token in (key, f"-{key}"):
                response = await harness.client.post(
                    "/screens/select", json=select_body([], sort=token)
                )
                assert response.status_code == 200, (token, response.text)
                assert response.json()["sort"] == token

        for token in ("no", "-no"):
            refused = await harness.client.post(
                "/screens/select", json=select_body([], sort=token)
            )
            assert refused.status_code == 422, refused.text
            body = refused.json()
            assert body["error"] == "unknown_sort"
            assert "client-only" in body["errorMessage"], body["errorMessage"]


async def test_a_signal_orders_its_population(tmp_path: Path, monkeypatch: Any) -> None:
    rows = [
        {"symbol": "SLOW", "name": "Slow", "price": 10.0, "change_pct": 1.0, "market_cap_usd": 1e9},
        {"symbol": "FAST", "name": "Fast", "price": 10.0, "change_pct": 12.0, "market_cap_usd": 1e9},
        {"symbol": "RED", "name": "Red", "price": 10.0, "change_pct": -3.0, "market_cap_usd": 1e9},
        {"symbol": "FLAT", "name": "Flat", "price": 10.0, "change_pct": 0.0, "market_cap_usd": 1e9},
    ]

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(screens_api, "build_universe", lambda _rows, **_: list(rows))
        response = await harness.client.post(
            "/screens/select",
            json={"mode": "criteria", "region": "us", "signal": "top-gainers", "criteria": {"limit": 5}},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sort"] == "change_pct"
    assert [row["symbol"] for row in body["rows"]] == ["FAST", "SLOW"], (
        "the gainers population, highest change first; 0% and -3% are not gainers"
    )
    assert body["total"] == 2


async def test_an_unavailable_signal_is_422(tmp_path: Path, monkeypatch: Any) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={"mode": "criteria", "region": "us", "signal": "upgrades", "criteria": {}},
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unavailable_signal"
    assert "upgrades" in body["errorMessage"]


# ---------------------------------------------------------------------------
# The revision
# ---------------------------------------------------------------------------


def test_the_revision_is_v3_and_names_the_membership() -> None:
    from app.api.screens import _data_revision

    line = _data_revision(
        snapshot_id=None,
        snapshot_as_of=None,
        depth="depth-1",
        scan=7,
        fundamentals="3:2026-11-01",
        facts="2:2026-11-01",
        brand="brand-1",
        members="2026-11-01:503,101,30,1881",
    )

    assert line.startswith("v3;"), line
    assert ";members=2026-11-01:503,101,30,1881" in line
    assert ";scan=7" in line


def test_the_tag_moves_because_the_revision_moved() -> None:
    """The bump's exact claim: the same question under ``v2`` and ``v3`` differs.

    A warm client holds a ``v2`` tag; the membership snapshot joined the
    revision, so that tag no longer certifies anything and the next request is a
    ``200``. The reverse — a ``v3`` request answered with the old tag — would be
    a stale ``304`` carrying rows filtered by a list the process no longer has.
    """

    from app.api.screens import _data_revision, _etag
    from app.screen.selection import parse_selection

    request = parse_selection(
        {"mode": "criteria", "region": "us", "criteria": {"limit": 5}}
    )
    current = _data_revision(
        snapshot_id=None,
        snapshot_as_of=None,
        depth="depth-1",
        scan=1,
        fundamentals="3:2026-11-01",
        facts="2:2026-11-01",
        brand="brand-1",
        members="2026-11-01:503,101,30,1881",
    )
    previous = current.replace(
        ";members=2026-11-01:503,101,30,1881", "", 1
    ).replace("v3;", "v2;", 1)

    assert previous.startswith("v2;") and "members=" not in previous
    assert _etag(revision=current, request=request) != _etag(
        revision=previous, request=request
    ), "the revision is an input of the tag, so a new input is a new tag"


async def test_the_tag_moves_when_the_membership_snapshot_moves(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Same rows, same question, a newer snapshot: a new tag, never a stale 304."""

    body = select_body([{"key": "idx", "op": "eq", "value": "sp500"}])
    original = index_membership.load()

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        from app.api import screens as screens_api

        monkeypatch.setattr(
            screens_api, "build_universe", members_universe(index_membership.members("sp500"))
        )
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text

        # A newer stamp, the same lists: the rows do not change, the data does.
        moved = index_membership.Snapshot(
            as_of="2026-10-31", source=original.source, indices=original.indices
        )
        monkeypatch.setattr(index_membership, "load", lambda path=None: moved)
        second = await harness.client.post("/screens/select", json=body)
        replay = await harness.client.post(
            "/screens/select", json=body, headers={"If-None-Match": first.headers["etag"]}
        )
        third = await harness.client.post(
            "/screens/select", json=body, headers={"If-None-Match": second.headers["etag"]}
        )

    assert first.headers["etag"] != second.headers["etag"], (
        "the membership stamp is an input of the answer, so it is an input of the tag"
    )
    assert second.json()["total"] == first.json()["total"], "the rows did not move"
    assert replay.status_code == 200, "the old tag must not certify the new snapshot"
    assert third.status_code == 304, "the current tag still revalidates"
    assert third.content == b""
