"""The ten native scan metrics wired as screener columns.

The scanner answers ``EMA13``/``SMA13``/``Mom``/``Perf.W`` for every row; those
cells used to stop at the provider boundary because no catalogue key named them.
This suite pins the wiring end to end:

* one registry (:mod:`app.screen.derived_columns`) names the ten public keys,
  their row field, unit and label — and it is **additive**: the frozen 116-key
  catalogue is not edited;
* every provider column the registry needs is in ``PARITY_FIELDS``, so the
  parity scan asks for it and ``build_universe`` copies it onto the row;
* ``POST /screens/select`` accepts the keys in ``columns`` and answers numbers
  for AAPL over the real ASGI app, while an unknown key is still refused;
* the 40-column cap still holds with the ten extra keys;
* the TypeScript mirror the picker renders names exactly the same ten keys.

Honest exclusions live in the module docstring, not in a fake column: there is
no provider MTD field (``Perf.1M`` is a trailing month, not month-to-date), and
greeks/expiry/DTM are per-symbol option metrics, not bulk scan columns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.screen import columns as column_map
from app.screen import derived_columns as computed
from app.screen.selection import COLUMN_LIMIT, SelectionError, parse_selection
from app.screen.universe import (
    CARRIED_FIELDS,
    PARITY_COLUMNS,
    PARITY_FIELDS,
    build_universe,
)
from tests import _screen as S

ROOT = Path(__file__).resolve().parents[1]

#: Public key → the provider column the scan must request. ``perf_wtd`` is the
#: one key whose provider field already existed (``Perf.W`` → ``ret_1w``).
PROVIDER_FIELD: dict[str, str] = {
    "ema_13": "EMA13",
    "ema_21": "EMA21",
    "ema_50": "EMA50",
    "ema_100": "EMA100",
    "ema_200": "EMA200",
    "sma_13": "SMA13",
    "sma_21": "SMA21",
    "sma_100": "SMA100",
    "momentum": "Mom",
    "perf_wtd": "Perf.W",
}

#: Values the ASGI responder sends for AAPL, keyed by provider column.
PROVIDER_VALUES: dict[str, float] = {
    "EMA13": 343.8211,
    "EMA21": 341.9768,
    "EMA50": 334.5159,
    "EMA100": 322.6794,
    "EMA200": 303.5753,
    "SMA13": 344.4827,
    "SMA21": 342.0821,
    "SMA100": 321.9515,
    "Mom": -0.88,
    "Perf.W": -0.5408,
}

#: What every row needs to survive the universe filter and dedupe.
BASE: dict[str, Any] = {
    "name": "AAPL",
    "description": "Apple Inc.",
    "exchange": "NASDAQ",
    "country": "United States",
    "sector": "Technology",
    "type": "stock",
    "subtype": "common",
    "currency": "USD",
    "close": 342.06,
    "change": 0.5,
    "volume": 1_000_000.0,
    "Value.Traded": 100_000_000.0,
    "market_cap_basic": 3_400_000_000_000.0,
}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_the_registry_names_exactly_the_ten_wired_keys() -> None:
    assert tuple(computed.COMPUTED_COLUMNS) == (
        "ema_13",
        "ema_21",
        "ema_50",
        "ema_100",
        "ema_200",
        "sma_13",
        "sma_21",
        "sma_100",
        "momentum",
        "perf_wtd",
    )
    assert set(PROVIDER_FIELD) == set(computed.COMPUTED_COLUMNS)


def test_every_entry_declares_a_real_row_field_a_unit_and_a_label() -> None:
    for key, entry in computed.COMPUTED_COLUMNS.items():
        assert entry["field"] in CARRIED_FIELDS, (
            f"{key} points at {entry['field']!r}, which no row carries"
        )
        assert entry["unit"] in {"price", "percent"}, key
        assert entry["label"].strip(), key
    assert computed.COMPUTED_COLUMNS["ema_13"]["unit"] == "price"
    assert computed.COMPUTED_COLUMNS["perf_wtd"]["unit"] == "percent"


def test_the_momentum_label_states_the_transform_and_the_window() -> None:
    """``Mom`` is a price difference over 10 sessions, not a percent.

    Verified live on 2026-11-02: AAPL ``Mom`` = -0.88 and close − close[10]
    = 342.06 − 342.94 = -0.88. Calling it "Momentum" alone would let a reader
    assume a percent; the label says what it is.
    """

    label = computed.COMPUTED_COLUMNS["momentum"]["label"].lower()
    assert "price change" in label
    assert "10" in label
    wtd = computed.COMPUTED_COLUMNS["perf_wtd"]["label"].lower()
    assert "week" in wtd and "date" in wtd


def test_the_public_keys_are_additive_to_the_frozen_catalogue() -> None:
    assert len(column_map.CATALOGUE_KEY_SET) == 116
    assert set(computed.COMPUTED_COLUMNS).isdisjoint(column_map.CATALOGUE_KEY_SET), (
        "these are extra keys; editing the 116-column catalogue is not the fix"
    )


# ---------------------------------------------------------------------------
# Scan request + row normaliser
# ---------------------------------------------------------------------------


def test_every_provider_column_is_requested_by_the_parity_scan() -> None:
    for key, provider in PROVIDER_FIELD.items():
        assert provider in PARITY_COLUMNS, f"{provider} is not in the scan request"
        assert PARITY_FIELDS[provider] == computed.COMPUTED_COLUMNS[key]["field"]


def test_build_universe_copies_all_ten_cells_onto_the_row() -> None:
    row = dict(S.fixture_row("us", "AAPL"))
    row.update(PROVIDER_VALUES)
    row["Perf.W"] = PROVIDER_VALUES["Perf.W"]

    universe = build_universe(
        [row],
        region="us",
        min_market_cap=None,
        columns=PARITY_COLUMNS,
        allow_any_subtype=True,
    )
    assert len(universe) == 1
    aapl = universe[0]
    for key, entry in computed.COMPUTED_COLUMNS.items():
        value = aapl[entry["field"]]
        assert isinstance(value, float), f"{key}: {value!r}"
    assert aapl["ema_13"] == pytest.approx(343.8211)
    assert aapl["momentum_10d"] == pytest.approx(-0.88)
    assert aapl["ret_1w"] == pytest.approx(-0.5408)


def test_a_missing_cell_is_null_not_zero() -> None:
    value = computed.value_for({"ema_13": None}, "ema_13")
    assert value is None
    assert computed.value_for({}, "momentum") is None
    assert computed.value_for({"ema_13": float("nan")}, "ema_13") is None
    assert computed.value_for({"ema_13": True}, "ema_13") is None
    assert computed.value_for({"ema_13": 0.0}, "ema_13") == 0.0


# ---------------------------------------------------------------------------
# POST /screens/select over ASGI
# ---------------------------------------------------------------------------


def _aapl_web() -> tuple[Any, list[list[str]]]:
    """A scanner responder that answers every requested column for AAPL."""

    seen: list[list[str]] = []
    web = S.MockWeb()

    def responder(request: Any) -> dict[str, Any]:
        body = json.loads(request.content)
        columns = list(body["columns"])
        seen.append(columns)
        data = [BASE.get(column, PROVIDER_VALUES.get(column)) for column in columns]
        return {"totalCount": 1, "data": [{"s": "NASDAQ:AAPL", "d": data}]}

    web.json("tradingview", responder)
    return web, seen


def _selection_payload(columns: list[str]) -> dict[str, Any]:
    return {
        "mode": "criteria",
        "region": "us",
        "criteria": {"filters": [], "limit": 5},
        "columns": columns,
    }


async def test_each_computed_key_returns_a_number_for_aapl_over_asgi(
    tmp_path: Path, monkeypatch: Any
) -> None:
    web, seen = _aapl_web()
    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        response = await harness.client.post(
            "/screens/select", json=_selection_payload(list(computed.COMPUTED_COLUMNS))
        )

    assert response.status_code == 200, response.text
    document = response.json()
    assert document["returned"] == 1
    row = document["rows"][0]
    assert row["symbol"] == "AAPL"
    assert set(row) == {"symbol", "as_of", *computed.COMPUTED_COLUMNS}
    for key in computed.COMPUTED_COLUMNS:
        value = row[key]
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{key} came back as {value!r}, not a number"
        )
    assert row["ema_13"] == pytest.approx(343.8211)
    assert row["sma_100"] == pytest.approx(321.9515)
    assert row["momentum"] == pytest.approx(-0.88)
    assert row["perf_wtd"] == pytest.approx(-0.5408)

    # The scan really asked the provider for the nine new columns; ``Perf.W``
    # was already on the parity request.
    requested = set(seen[0])
    for provider in PROVIDER_FIELD.values():
        assert provider in requested, f"{provider} never reached the scanner"


async def test_an_unknown_column_is_still_refused_before_the_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    web, _seen = _aapl_web()
    async with S.screen_client(tmp_path, monkeypatch, web=web) as harness:
        response = await harness.client.post(
            "/screens/select",
            json=_selection_payload(["ema_13", "definitely_not_a_column"]),
        )
        requests = S.provider_requests(harness.web)

    assert response.status_code == 422, response.text
    body = response.json()
    assert "definitely_not_a_column" in body["errorMessage"]
    assert requests == 0, "an unknown column must be refused before any scan"


def test_the_forty_column_cap_still_holds_with_the_computed_keys() -> None:
    pool = [key for key in sorted(column_map.ROW_COLUMNS) if key not in computed.COMPUTED_COLUMNS]
    filler = pool[: COLUMN_LIMIT - len(computed.COMPUTED_COLUMNS)]
    accepted = [*computed.COMPUTED_COLUMNS, *filler]
    assert len(accepted) == COLUMN_LIMIT

    request = parse_selection(_selection_payload(accepted))
    assert len(request.columns) == COLUMN_LIMIT

    with pytest.raises(SelectionError) as caught:
        parse_selection(_selection_payload([*accepted, pool[len(filler)]]))
    assert f"maximum is {COLUMN_LIMIT}" in str(caught.value)


# ---------------------------------------------------------------------------
# The client mirror
# ---------------------------------------------------------------------------


def test_the_typescript_mirror_names_the_same_ten_keys() -> None:
    source = (ROOT / "web" / "src" / "grid" / "computedColumns.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"export const COMPUTED_COLUMN_KEYS = \[(.*?)\] as const", source, re.S
    )
    assert match is not None, "the picker's key list is no longer a literal"
    keys = [
        token.strip().strip('"')
        for token in match.group(1).split(",")
        if token.strip()
    ]
    assert keys == list(computed.COMPUTED_COLUMNS)
