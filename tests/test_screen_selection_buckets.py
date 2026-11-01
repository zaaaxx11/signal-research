"""Selection: a bucket token is a criterion with no field, and never a DSL value.

The contract (plan T6):

* ``{"key": "sh_price", "op": "eq", "value": "price:u5"}`` parses into
  ``Criterion(field=None, key="sh_price", value="price:u5")`` — the wire shape is
  unchanged, the bucket lives in ``value``;
* :func:`app.screen.selection._predicates` **drops** it, so
  ``app.screen.filters`` never sees a token and cannot mistake it for a number;
* the executor applies it through :mod:`app.screen.buckets` over the same rows,
  after the DSL has run — buckets filter, they do not widen;
* an unknown token is a ``422 unknown_bucket`` naming the token, an unavailable
  one is ``422 unavailable_bucket`` naming the missing source, and a token on the
  wrong key is refused because ids are namespaced;
* the envelope echoes the token and a null field, so two buckets are two
  questions — and therefore two ETags.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.screen import buckets, index_membership, selection as selection_module
from app.screen.filter_fields import binding, mapped_keys, unavailable_keys
from app.screen.selection import (
    SelectionError,
    parse_selection,
    request_digest,
    select_rows,
)
from tests import _screen as S

AS_OF = "2026-11-02T02:30:00.000000+00:00"

CHEAP = {"symbol": "CHEAP", "name": "Cheap Co", "price": 4.5, "market_cap_usd": 3e9}
PRICEY = {"symbol": "PRICEY", "name": "Pricey Co", "price": 55.0, "market_cap_usd": 3e9}


def rows() -> list[dict[str, Any]]:
    return [dict(CHEAP), dict(PRICEY)]


def request(filters: list[dict[str, Any]], **criteria: Any) -> Any:
    return parse_selection(
        {
            "mode": "criteria",
            "region": "us",
            "criteria": {"filters": filters, **criteria},
        }
    )


def run(filters: list[dict[str, Any]], **criteria: Any) -> Any:
    return select_rows(
        rows(), request(filters, **criteria), region="us", as_of=AS_OF
    )


def test_a_bucket_token_never_reaches_the_dsl(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = request([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    criterion = parsed.filters[0]
    assert criterion.field is None
    assert criterion.key == "sh_price"
    assert criterion.value == "price:u5"
    assert selection_module._predicates(parsed) == (), (
        "a bucket has no field, so the DSL must not receive a dict for it"
    )

    seen: list[Any] = []
    original = selection_module.apply_filters

    def spy(rows_: Any, filters_: Any = None, *, region: str | None = None) -> Any:
        seen.append(filters_)
        return original(rows_, filters_, region=region)

    monkeypatch.setattr(selection_module, "apply_filters", spy)
    selection = run([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    assert seen == [()], "apply_filters is called with the empty predicate list"
    assert [row["symbol"] for row in selection.rows] == ["CHEAP"]


def test_a_bucket_really_filters_the_page() -> None:
    selection = run([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    assert selection.total == 1
    assert [row["symbol"] for row in selection.rows] == ["CHEAP"]
    assert selection.universe_total == 2


def test_a_token_is_case_insensitive() -> None:
    upper = request([{"key": "idx", "op": "eq", "value": "SP500"}])
    lower = request([{"key": "idx", "op": "eq", "value": "sp500"}])
    assert upper.filters[0].value == lower.filters[0].value == "idx:sp500"
    assert request_digest(upper) == request_digest(lower)


def test_a_bare_token_is_resolved_inside_its_own_namespace() -> None:
    parsed = request([{"key": "idx", "op": "eq", "value": "sp500"}])
    assert parsed.filters[0].value == "idx:sp500"
    parsed = request([{"key": "cap", "op": "eq", "value": "mega"}])
    assert parsed.filters[0].value == "cap:mega"
    assert parsed.filters[0].field is None


def test_an_index_bucket_selects_members_and_nothing_else() -> None:
    members = [
        {"symbol": "AAPL", "name": "Apple", "price": 200.0, "index_members": ["sp500", "ndx"]},
        {"symbol": "MSFT", "name": "Microsoft", "price": 400.0, "index_members": []},
    ]
    selection = select_rows(
        members,
        request([{"key": "idx", "op": "eq", "value": "sp500"}]),
        region="us",
        as_of=AS_OF,
    )
    assert [row["symbol"] for row in selection.rows] == ["AAPL"]
    assert selection.total == 1


def test_a_dsl_criterion_and_a_bucket_and_together() -> None:
    """Two compilers, one AND: neither may replace the other."""

    selection = run(
        [
            {"field": "market_cap_usd", "op": "gte", "value": 1},
            {"key": "sh_price", "op": "eq", "value": "price:u5"},
        ]
    )
    assert [row["symbol"] for row in selection.rows] == ["CHEAP"]
    assert selection.total == 1
    # Flip the DSL half and the bucket half still applies.
    selection = run(
        [
            {"field": "market_cap_usd", "op": "gte", "value": 1},
            {"key": "sh_price", "op": "eq", "value": "price:o50"},
        ]
    )
    assert [row["symbol"] for row in selection.rows] == ["PRICEY"]


def test_unknown_token_is_422_unknown_bucket() -> None:
    with pytest.raises(SelectionError) as caught:
        request([{"key": "idx", "op": "eq", "value": "sp900"}])
    assert caught.value.code == "unknown_bucket"
    assert "sp900" in str(caught.value)
    assert "idx:sp500" in str(caught.value)


def test_a_token_on_a_wrong_key_is_refused() -> None:
    """`u5` names a price band; `cap:u5` names nothing, and ids are namespaced."""

    with pytest.raises(SelectionError) as caught:
        request([{"key": "cap", "op": "eq", "value": "u5"}])
    assert caught.value.code == "unknown_bucket"
    assert "cap:mega" in str(caught.value)

    with pytest.raises(SelectionError) as caught:
        request([{"key": "cap", "op": "eq", "value": "price:u5"}])
    assert caught.value.code == "unknown_bucket"
    assert "price:u5" in str(caught.value)


def test_a_bucket_takes_no_second_comparison() -> None:
    with pytest.raises(SelectionError) as caught:
        request([{"key": "sh_price", "op": "lt", "value": "price:u5"}])
    assert caught.value.code == "invalid_filter"
    assert "eq" in str(caught.value)


def test_a_numeric_criterion_on_a_bucket_key_still_works() -> None:
    """`price` keeps its DSL form: the bucket path only claims tokens."""

    parsed = request([{"key": "sh_price", "op": "lt", "value": 5}])
    criterion = parsed.filters[0]
    assert criterion.field == "price"
    assert criterion.op == "lt"
    assert criterion.value == 5.0
    selection = run([{"key": "sh_price", "op": "in", "value": ["4.5", "55"]}])
    assert selection.total == 2


def test_two_different_buckets_are_two_etags() -> None:
    under = request([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    over = request([{"key": "sh_price", "op": "eq", "value": "price:o5"}])
    assert request_digest(under) != request_digest(over)
    assert request_digest(under) == request_digest(
        request([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    )


def test_the_envelope_echoes_the_token() -> None:
    selection = run([{"key": "sh_price", "op": "eq", "value": "price:u5"}])
    document = selection.as_document()
    assert document["filters"] == [
        {"key": "sh_price", "field": None, "op": "eq", "value": "price:u5"}
    ]
    assert json.loads(json.dumps(document))["filters"][0]["field"] is None


def test_idx_is_available_with_a_bucket_and_no_column() -> None:
    bound = binding("idx")
    assert bound.is_available is True
    assert bound.field is None
    assert bound.bucket is True
    assert bound.reason is None
    assert "idx" in mapped_keys()
    assert "idx" not in unavailable_keys()
    assert bound.as_dict()["bucket"] is True
    assert bound.as_dict()["field"] is None


def test_every_bucket_key_publishes_the_flag() -> None:
    for key in buckets.KEYS:
        assert binding(key).bucket is True, key
    # The flag does not leak onto keys with no vocabulary of their own.
    for key in ("exch", "sh_curvol", "ta_perf"):
        assert binding(key).bucket is False, key


def test_an_unavailable_control_keeps_its_refusal_not_a_bucket_path() -> None:
    """`theme` has no predicate family, so it stays a 422 that names the metric."""

    for key, token in (("theme", "artificialintelligence"), ("sh_opt", "option")):
        with pytest.raises(SelectionError) as caught:
            request([{"key": key, "op": "eq", "value": token}])
        assert caught.value.code == "unavailable_filter_key"
        assert key in str(caught.value)


def test_an_unavailable_index_snapshot_is_422_not_an_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        index_membership, "load", lambda path=None: index_membership.Snapshot()
    )
    with pytest.raises(SelectionError) as caught:
        request([{"key": "idx", "op": "eq", "value": "sp500"}])
    assert caught.value.code == "unavailable_bucket"
    assert "snapshot" in str(caught.value)


# ---------------------------------------------------------------------------
# Over HTTP: the route maps a bucket failure to its own 422 code
# ---------------------------------------------------------------------------


async def test_a_bucket_token_over_http_filters_the_page(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {
                    "filters": [{"key": "cap", "op": "eq", "value": "mega"}],
                    "limit": 20,
                },
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filters"] == [
        {"key": "cap", "field": None, "op": "eq", "value": "cap:mega"}
    ]
    assert body["total"] > 0
    assert body["returned"] == len(body["rows"])
    for row in body["rows"]:
        assert row["market_cap_usd"] >= 200_000_000_000, row["symbol"]


async def test_an_unknown_bucket_over_http_is_a_422_envelope(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {"filters": [{"key": "idx", "op": "eq", "value": "sp900"}]},
            },
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"error", "errorMessage"}, body
    assert body["error"] == "unknown_bucket"
    assert "sp900" in body["errorMessage"]
