"""Two refusals that stopped being true: IPO date and option/short.

Both keys were refused because the *scan* cannot answer them — ``listing_date``
is null on every parity row and the scan has no optionable column at all. Both
were also measured present in the depth store, in shapes a filter can read:

* ``ipo_date`` — **4,217 rows, no nulls**, in two spellings: ``1999-11-18``
  (4,188) and ``Oct 18, 2016`` (29). Two spellings are a parsing problem, not a
  missing metric, and both parse to a date with no guessing: one is ISO, the
  other carries its own year.
* ``optionable`` / ``shortable`` — ``Yes``/``No`` on 4,197 rows (20 absent),
  ``optionable`` split 3,053/1,144 and ``shortable`` 4,170/27. A vendor string
  is a boolean with a spelling, and the control's eight tokens are exactly the
  2×2 truth table over those two cells.

So both are enabled as bucket families, and this file is the contract:

* the family covers the probe's tokens, in panel order, with a predicate each;
* the predicate answers the truth table (including "unknown matches nothing"):
  a null IPO date is not "in the last week", and an absent optionable cell is
  neither optionable nor not-optionable;
* the schema and the document agree the keys are available;
* ``POST /screens/select`` returns a plausible count from a real depth store,
  end to end — not a predicate unit test wearing an HTTP hat.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.screen import buckets, descriptive, filter_fields
from app.screen.buckets import Context

#: The depth store the route reads, wired the way the other selection tests do.
STORE_STAMP = "2026-11-01T15:06:26Z"


def _store(path: Path, symbols: dict[str, dict[str, Any]]) -> Path:
    path.write_text(
        json.dumps({"as_of": STORE_STAMP, "symbols": symbols}), encoding="utf-8"
    )
    return path


def _row(**cells: Any) -> dict[str, Any]:
    return {"symbol": "X", **cells}


def _test(token: str, row: dict[str, Any], today: date) -> bool:
    return buckets.bucket(token).predicate(row, Context(today=today))


# ---------------------------------------------------------------------------
# The families exist, in the panel's order
# ---------------------------------------------------------------------------


def test_the_option_short_family_is_the_eight_tokens_the_panel_draws() -> None:
    family = buckets.for_key("sh_opt")

    assert [item.id for item in family] == [
        "sh_opt:option",
        "sh_opt:short",
        "sh_opt:notoption",
        "sh_opt:notshort",
        "sh_opt:optionshort",
        "sh_opt:optionnotshort",
        "sh_opt:notoptionshort",
        "sh_opt:notoptionnotshort",
    ]
    assert all(item.key == "sh_opt" for item in family)
    assert all(item.available for item in family), "no token is a stub"
    assert all(item.predicate is not None and item.describe for item in family)


def test_the_ipo_family_is_the_fifteen_tokens_the_panel_draws() -> None:
    family = buckets.for_key("ipodate")

    assert [item.finviz for item in family] == [
        "today",
        "yesterday",
        "prevweek",
        "prevmonth",
        "prevquarter",
        "prevyear",
        "prev2yrs",
        "prev3yrs",
        "prev5yrs",
        "more1",
        "more5",
        "more10",
        "more15",
        "more20",
        "more25",
    ]
    assert [item.id for item in family][0] == "ipodate:today"
    assert all(item.key == "ipodate" for item in family)
    assert all(item.available for item in family)


# ---------------------------------------------------------------------------
# The truth tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("optionable", "shortable", "expected"),
    [
        ("Yes", "Yes", {"sh_opt:option", "sh_opt:short", "sh_opt:optionshort"}),
        ("Yes", "No", {"sh_opt:option", "sh_opt:notshort", "sh_opt:optionnotshort"}),
        ("No", "Yes", {"sh_opt:short", "sh_opt:notoption", "sh_opt:notoptionshort"}),
        ("No", "No", {"sh_opt:notoption", "sh_opt:notshort", "sh_opt:notoptionnotshort"}),
    ],
)
def test_the_option_short_tokens_are_the_truth_table(
    optionable: str, shortable: str, expected: set[str]
) -> None:
    row = _row(optionable=optionable, shortable=shortable)
    matched = {
        item.id for item in buckets.for_key("sh_opt") if item.predicate(row, Context(today=date(2026, 11, 1)))
    }
    assert matched == expected


def test_an_unknown_option_cell_matches_nothing() -> None:
    """Absent is not ``No``: 20 symbols in the store carry neither cell."""

    row = _row(optionable=None, shortable=None)
    matched = [
        item.id for item in buckets.for_key("sh_opt") if item.predicate(row, Context(today=date(2026, 11, 1)))
    ]
    assert matched == []


def test_a_boolean_cell_is_read_the_same_way() -> None:
    """The official source writes a flag; the depth store writes ``Yes``/``No``."""

    from_booleans = _row(optionable=True, shortable=False)
    from_strings = _row(optionable="Yes", shortable="No")
    today = date(2026, 11, 1)
    for item in buckets.for_key("sh_opt"):
        assert item.predicate(from_booleans, Context(today=today)) is item.predicate(
            from_strings, Context(today=today)
        ), item.id


#: The seven "in the last N" windows. They nest: a date three days old is inside
#: the week, the month, the quarter, the year and every multi-year window — so a
#: case's expected set is one of these prefixes plus the exact-day token.
LAST = (
    "ipodate:prevweek",
    "ipodate:prevmonth",
    "ipodate:prevquarter",
    "ipodate:prevyear",
    "ipodate:prev2yrs",
    "ipodate:prev3yrs",
    "ipodate:prev5yrs",
)


@pytest.mark.parametrize(
    ("cell", "today", "expected"),
    [
        # Three days old: inside every "last N" window, outside every "more".
        ("2026-10-30", date(2026, 11, 1), set(LAST)),
        # Today: the exact token joins the "last N" windows.
        ("2026-11-01", date(2026, 11, 1), {"ipodate:today", *LAST}),
        # Yesterday has its own token, and is still inside the week window.
        ("2026-10-31", date(2026, 11, 1), {"ipodate:yesterday", *LAST}),
        # 16.5 years old: "more than 5/10/15", not "more than 20", no "last N".
        ("2010-05-04", date(2026, 11, 1), {"ipodate:more1", "ipodate:more5", "ipodate:more10", "ipodate:more15"}),
        # 27 years old: every "more than N" that is younger than it.
        (
            "1999-11-18",
            date(2026, 11, 1),
            {
                "ipodate:more1",
                "ipodate:more5",
                "ipodate:more10",
                "ipodate:more15",
                "ipodate:more20",
                "ipodate:more25",
            },
        ),
    ],
)
def test_the_ipo_windows_answer_their_own_bounds(
    cell: str, today: date, expected: set[str]
) -> None:
    """Each window is a predicate over one date; the older ones nest by design."""

    row = _row(listing_date=cell)
    matched = {
        item.id
        for item in buckets.for_key("ipodate")
        if item.predicate(row, Context(today=today))
    }
    assert matched == expected, matched


def test_both_ipo_spellings_parse_to_the_same_date() -> None:
    """``1999-11-18`` and ``Nov 18, 1999`` are one day, and the parser says so."""

    today = date(2026, 11, 1)
    iso = _row(listing_date="1999-11-18")
    spelled = _row(listing_date="Nov 18, 1999")
    for item in buckets.for_key("ipodate"):
        assert item.predicate(iso, Context(today=today)) is item.predicate(
            spelled, Context(today=today)
        ), item.id


def test_an_epoch_stamp_is_read_as_the_documented_unit() -> None:
    """``listing_date`` is documented as epoch seconds; read it that way."""

    import calendar

    stamp = calendar.timegm((2010, 5, 4, 0, 0, 0))
    today = date(2026, 11, 1)
    from_epoch = _row(listing_date=stamp)
    from_iso = _row(listing_date="2010-05-04")
    for item in buckets.for_key("ipodate"):
        assert item.predicate(from_epoch, Context(today=today)) is item.predicate(
            from_iso, Context(today=today)
        ), item.id


def test_an_unparseable_or_null_ipo_cell_matches_nothing() -> None:
    today = date(2026, 11, 1)
    # ``19991118`` is *not* in this list: an int is the column's documented unit
    # (epoch seconds), so it parses — as 1970-08-20, which is a real date.
    for cell in (None, "", "not a date", "18/11/1999", True):
        row = _row(listing_date=cell)
        matched = [
            item.id
            for item in buckets.for_key("ipodate")
            if item.predicate(row, Context(today=today))
        ]
        assert matched == [], (cell, matched)


def test_every_ipo_predicate_names_the_cell_it_reads() -> None:
    for item in buckets.for_key("ipodate"):
        assert "listing_date" in item.describe, (item.id, item.describe)


# ---------------------------------------------------------------------------
# The schema and the document
# ---------------------------------------------------------------------------


def test_the_schema_accepts_both_keys_as_bucket_filters() -> None:
    for key in ("sh_opt", "ipodate"):
        binding = filter_fields.binding(key)
        assert binding.is_available, key
        assert binding.reason is None, key
        assert binding.bucket is True, key
        assert binding.field is None, key
        assert filter_fields.UNAVAILABLE.get(key) is None, key


def test_the_document_publishes_both_as_available_bucket_controls() -> None:
    controls = {
        control["id"]: control
        for group in descriptive.document()["groups"]
        for control in group["controls"]
    }
    for control_id, count, disposition in (
        ("sh_opt", 8, "join"),
        ("ipodate", 15, "join"),
    ):
        control = controls[control_id]
        assert control["available"] is True, control_id
        assert control["control"] == "buckets", control_id
        assert control["disposition"] == disposition, control_id
        assert control["reason"] is None, control_id
        assert len(control["options"]) == count, control_id
        assert all(option["predicate"] for option in control["options"]), control_id


# ---------------------------------------------------------------------------
# End to end: a real depth store, a real selection
# ---------------------------------------------------------------------------


async def test_a_selection_filters_by_option_and_short_from_the_depth_store(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from app.api import screens as screens_api
    from app.screen import depth_source
    from tests import _screen as S

    recent = (date.today() - timedelta(days=20)).isoformat()
    store = _store(
        tmp_path / "depth.json",
        {
            "AAPL": {"optionable": "Yes", "shortable": "Yes", "ipo_date": "1980-12-12"},
            "MSFT": {"optionable": "Yes", "shortable": "No", "ipo_date": recent},
            "NVDA": {"optionable": "No", "shortable": "Yes", "ipo_date": "Oct 18, 2016"},
            "GOOGL": {"optionable": "No", "shortable": "No"},
        },
    )
    monkeypatch.setattr(screens_api, "resolve_store_path", lambda *a, **k: store)
    monkeypatch.setattr(depth_source, "resolve_store_path", lambda *a, **k: store)

    async with S.screen_client(tmp_path, monkeypatch) as harness:

        async def count(key: str, token: str) -> int:
            response = await harness.client.post(
                "/screens/select",
                json={
                    "mode": "criteria",
                    "region": "us",
                    "criteria": {
                        "limit": 100,
                        "filters": [{"key": key, "op": "eq", "value": token}],
                    },
                },
            )
            assert response.status_code == 200, (token, response.text)
            return int(response.json()["total"])

        assert await count("sh_opt", "sh_opt:option") == 2, "AAPL and MSFT are optionable"
        assert await count("sh_opt", "sh_opt:notoption") == 2
        assert await count("sh_opt", "sh_opt:optionshort") == 1
        assert await count("sh_opt", "sh_opt:optionnotshort") == 1
        assert await count("sh_opt", "sh_opt:notoptionnotshort") == 1

        assert await count("ipodate", "ipodate:more20") == 1, "AAPL listed in 1980"
        assert await count("ipodate", "ipodate:prevmonth") == 1, "MSFT listed a month ago"
        assert await count("ipodate", "ipodate:more5") == 2, "AAPL and NVDA (2016)"


async def test_an_unknown_option_token_is_a_422_that_names_itself(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Enabled does not mean "anything goes": the vocabulary is still closed."""

    from tests import _screen as S

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post(
            "/screens/select",
            json={
                "mode": "criteria",
                "region": "us",
                "criteria": {
                    "limit": 5,
                    "filters": [{"key": "sh_opt", "op": "eq", "value": "sh_sh_opt:maybe"}],
                },
            },
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "unknown_bucket", body
    assert "sh_sh_opt:maybe" in body["errorMessage"]
    assert "total" not in body
