"""Shared helpers for the Fase-6 wiring RED suite (formats + Yahoo capabilities).

Not a test module (leading underscore), so pytest never collects it. The
contracts pinned here are expanded in ``briefs/market-engine/20-wiring-red.md``.

Two independent things live in this file, and they are deliberately independent
of the code under test:

**The export oracles.** ``artifact_row_count`` reads an artifact with the
library of its *format* (``csv``, ``openpyxl``, ``pyarrow``), never with
``app.export.writer``. A writer that miscounts its own rows cannot satisfy a
test that counts them with someone else's parser.

**The Yahoo oracles.** ``eod_oracle``, ``fundamentals_oracle`` and
``options_oracle`` recompute the expected cells from the raw fixture JSON using
only the standard library. They never import ``app.providers.adapters.yahoo``,
so a formula that drifts inside the adapter cannot redefine the expectation.

EOD definitions pinned by :func:`eod_oracle` (the adapter must match them
exactly — they are the spec, not a suggestion):

* **Bars.** One bar per timestamp whose ``open/high/low/close`` are all
  numbers, with ``low <= min(open, close) <= max(open, close) <= high`` and
  ``close > 0`` (V2: a bar that violates the envelope is dropped, and
  ``bars_count`` follows). A null ``volume`` counts as ``0``.
* **Window.** Every indicator/return is computed over the **whole series from
  its first bar**, not over a truncated tail. Wilder smoothing is path
  dependent, so "the last 100 bars" would be a different number every time the
  range changes.
* ``ma_N`` — simple mean of the last ``N`` closes; ``None`` when the series is
  shorter than ``N``.
* ``ema_N`` — seeded with the SMA of the first ``N`` closes, then
  ``alpha = 2 / (N + 1)`` over the rest.
* ``rsi_14`` — Wilder: seed = mean of the first 14 gains/losses (changes
  ``close[i] - close[i-1]``), then ``avg = (avg * 13 + current) / 14``;
  ``100 - 100 / (1 + avg_gain / avg_loss)``, and ``100.0`` when
  ``avg_loss == 0``.
* ``atr_14`` — Wilder over true range
  ``max(high - low, |high - prev_close|, |low - prev_close|)``, seeded with the
  mean of the first 14 ranges.
* ``volatility_20d`` — population standard deviation of the last 20 daily log
  returns, annualised by ``sqrt(252)``, expressed in **percent**.
* ``avg_volume_20d`` — mean of the last 20 volumes.
* **Returns are percent and computed, never quoted.** ``ret_1d`` is the
  previous bar; ``ret_1w`` anchors 7 calendar days back; ``ret_1m/3m/6m/1y/3y/5y``
  anchor by calendar month arithmetic (``-1/-3/-6/-12/-36/-60`` months);
  ``ret_ytd`` anchors on the last bar of the previous calendar year. Each anchor
  is the last bar at or before the target date. When the target precedes the
  first bar, the first bar is used **only** if it is within 10 calendar days of
  the target (a weekend/holiday gap, not a shorter history); otherwise the cell
  is ``None``.

Yahoo capability definitions pinned by :func:`fundamentals_oracle` and
:func:`options_oracle`:

* ``quoteSummary`` modules ``price``, ``summaryDetail``,
  ``defaultKeyStatistics`` and ``financialData`` feed the 22 fundamentals /
  statements columns; ``dividend_yield``, ``payout_ratio`` and ``roe`` are
  stored in **percent points** (V6), ``debt_to_equity`` as the provider reports
  it, and ``market_cap_usd`` equals ``market_cap`` only when the quote currency
  is USD (no FX rate is invented).
* The options block is the **ATM call**: the expiry nearest 30 DTE from the
  chain's own ``quote.regularMarketTime`` (never the wall clock), and the call
  whose strike is closest to the chain's ``quote.regularMarketPrice``. IV is a
  **fraction** (V7); the Greeks are computed by the adapter (Yahoo does not
  publish them) and must be finite with the V8 signs.
"""

from __future__ import annotations

import csv
import importlib
import json
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

from tests._support import COLUMNS, api_client, enrich_payload

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures"

#: The live AAPL captures. The chart is a real ``/v8/finance/chart`` response
#: (5y, daily); ``quoteSummary`` and ``options`` are recorded to the documented
#: public shape because Yahoo now answers 401 "Invalid Crumb" without a warmed
#: cookie jar (01-providers §Yahoo note 2).
CHART_5Y_PATH = FIXTURE_DIR / "yahoo_aapl_chart_5y.json"
QUOTE_SUMMARY_PATH = FIXTURE_DIR / "yahoo_aapl_quote_summary.json"
OPTIONS_PATH = FIXTURE_DIR / "yahoo_aapl_options.json"

#: The four data files one run must produce (``json`` stays a by-request format).
FOUR_FORMATS: tuple[str, ...] = ("csv", "xlsx", "jsonl", "parquet")

#: Media type per format, from the format's own registration — not from
#: ``app.api.requests.MEDIA_TYPES``, so a wrong constant cannot define itself.
MEDIA_TYPES: dict[str, str] = {
    "csv": "text/csv",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
}

#: The 21 EOD/performance cells the EOD capability must fill from one chart body.
EOD_COLUMNS: tuple[str, ...] = (
    "bars_count",
    "first_bar_date",
    "last_bar_date",
    "ma_20",
    "ma_50",
    "ma_200",
    "ema_12",
    "ema_26",
    "rsi_14",
    "atr_14",
    "volatility_20d",
    "avg_volume_20d",
    "ret_1d",
    "ret_1w",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_ytd",
    "ret_1y",
    "ret_3y",
    "ret_5y",
)

#: The 22 fundamentals/statements cells the FUNDAMENTALS capability must fill.
FUNDAMENTAL_COLUMNS: tuple[str, ...] = (
    "market_cap",
    "market_cap_usd",
    "shares_outstanding",
    "float_shares",
    "pe_ttm",
    "pe_forward",
    "peg",
    "pb",
    "ps",
    "ev_ebitda",
    "eps_ttm",
    "eps_forward",
    "dividend_yield",
    "payout_ratio",
    "revenue_ttm",
    "ebitda_ttm",
    "net_income_ttm",
    "fcf_ttm",
    "total_debt",
    "cash_and_equivalents",
    "roe",
    "debt_to_equity",
)

#: The 10 options cells the OPTIONS capability must fill from one ATM contract.
OPTION_COLUMNS: tuple[str, ...] = (
    "opt_expiry",
    "opt_dte",
    "opt_strike_atm",
    "opt_iv_atm",
    "opt_delta_atm",
    "opt_gamma_atm",
    "opt_theta_atm",
    "opt_vega_atm",
    "opt_open_interest_atm",
    "opt_volume_atm",
)


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load(module_name: str) -> Any:
    """Import a module, turning a missing one into a RED ``NotImplementedError``."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Fase 6 wiring RED): {exc}"
        ) from exc


def _require(module: Any, *names: str) -> Any:
    missing = [name for name in names if not hasattr(module, name)]
    if missing:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module.__name__} is missing " + ", ".join(missing) + " (Fase 6 wiring RED)"
        )
    if len(names) == 1:
        return getattr(module, names[0])
    return tuple(getattr(module, name) for name in names)


def writer_module() -> Any:
    return _load("app.export.writer")


def formats() -> tuple[str, ...]:
    """Every format the writer layer knows (``FORMATS``)."""

    return tuple(_require(writer_module(), "FORMATS"))


def implemented_formats() -> tuple[str, ...]:
    """The formats the API advertises because the runner writes them today."""

    return tuple(_require(writer_module(), "IMPLEMENTED_FORMATS"))


def manifest_module() -> Any:
    module = _load("app.export.manifest")
    _require(module, "read_manifest", "verify_manifest")
    return module


def runner_module() -> Any:
    module = _load("app.worker.runner")
    _require(module, "process_job", "_fetch_live")
    return module


def yahoo_module() -> Any:
    module = _load("app.providers.adapters.yahoo")
    _require(module, "YahooAdapter")
    return module


def verify_manifest(path: str | Path) -> Any:
    return manifest_module().verify_manifest(Path(path))


def read_manifest(path: str | Path) -> dict[str, Any]:
    return dict(manifest_module().read_manifest(Path(path)))


def check_field(check: Any, name: str) -> Any:
    """Read ``ok``/``problems``/``files_checked`` from a manifest check."""

    if isinstance(check, Mapping):
        if name in check:
            return check[name]
    elif hasattr(check, name):
        return getattr(check, name)
    raise NotImplementedError(f"verify_manifest() must expose {name!r} (Fase 6 wiring RED)")


# ---------------------------------------------------------------------------
# Reading artifacts with the format's own reader
# ---------------------------------------------------------------------------


def artifact_row_count(fmt: str, path: str | Path) -> int:
    """Count the data rows of an artifact, independently of the writer."""

    target = Path(path)
    if fmt == "csv":
        with open(target, newline="", encoding="utf-8") as handle:
            return max(0, sum(1 for _line in csv.reader(handle)) - 1)
    if fmt == "jsonl":
        return sum(
            1 for line in target.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    if fmt == "json":
        payload = json.loads(target.read_text(encoding="utf-8"))
        return len(payload) if isinstance(payload, list) else 0
    if fmt == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(filename=str(target), read_only=True, data_only=True)
        try:
            sheet = workbook["data"]
            return max(0, int(sheet.max_row or 1) - 1)
        finally:
            workbook.close()
    if fmt == "parquet":
        import pyarrow.parquet as pq

        return int(pq.read_table(str(target)).num_rows)
    raise AssertionError(f"no independent reader for format {fmt!r}")


def artifact_header(fmt: str, path: str | Path) -> list[str]:
    """The column header of an artifact, read with the format's own library."""

    target = Path(path)
    if fmt == "csv":
        with open(target, newline="", encoding="utf-8") as handle:
            return next(csv.reader(handle))
    if fmt == "jsonl":
        first = target.read_text(encoding="utf-8").splitlines()[0]
        return list(json.loads(first))
    if fmt == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(filename=str(target), read_only=True, data_only=True)
        try:
            sheet = workbook["data"]
            return [str(cell.value) for cell in next(sheet.iter_rows(max_row=1))]
        finally:
            workbook.close()
    if fmt == "parquet":
        import pyarrow.parquet as pq

        return [str(name) for name in pq.read_table(str(target)).column_names]
    raise AssertionError(f"no independent reader for format {fmt!r}")


# ---------------------------------------------------------------------------
# API harness
# ---------------------------------------------------------------------------


@asynccontextmanager
async def api(tmp_path: Path, monkeypatch: Any) -> AsyncIterator[tuple[Any, Path]]:
    """App on the fake provider plus the export directory it writes into."""

    async with api_client(tmp_path, monkeypatch) as client:
        yield client, Path(tmp_path) / "exports"


def export_dir(tmp_path: Path) -> Path:
    return Path(tmp_path) / "exports"


def submit_payload(**overrides: Any) -> dict[str, Any]:
    """A valid inline ``POST /markets/enrich`` body (fake provider, no network)."""

    body = enrich_payload(**{"async": False})
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Yahoo fixtures
# ---------------------------------------------------------------------------


def _fixture(path: Path) -> dict[str, Any]:
    if not path.exists():  # pragma: no cover - the fixtures ship with the suite
        raise NotImplementedError(f"missing test fixture {path} (Fase 6 wiring RED)")
    return json.loads(path.read_text(encoding="utf-8"))


def chart_5y() -> dict[str, Any]:
    return _fixture(CHART_5Y_PATH)


def quote_summary() -> dict[str, Any]:
    return _fixture(QUOTE_SUMMARY_PATH)


def options_chain() -> dict[str, Any]:
    return _fixture(OPTIONS_PATH)


def raw_body(payload: Mapping[str, Any], capability: str, *, status: int = 200) -> dict[str, Any]:
    """A ``RawResponse`` as ``HttpAdapter.fetch`` builds one."""

    return {
        "status": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload).encode("utf-8"),
        "url": f"https://query1.finance.yahoo.com/{capability}",
        "capability": capability,
    }


def observations(raw: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``{field: value}`` from an adapter's observation list (num, then text)."""

    out: dict[str, Any] = {}
    for item in raw:
        name = str(item.get("field") or "")
        if not name:
            continue
        value = item.get("value_num")
        if value is None:
            value = item.get("value_text")
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# EOD oracle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    """One surviving daily bar: the unit every EOD number is computed from."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def bars(fixture: Mapping[str, Any]) -> list[Bar]:
    """The bars the EOD parser must keep (V2 applied, nulls dropped)."""

    result = (fixture.get("chart") or {}).get("result")
    assert isinstance(result, list) and result, "chart fixture has no result[0]"
    entry = result[0]
    stamps = entry.get("timestamp") or []
    quote = ((entry.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    kept: list[Bar] = []
    for index, stamp in enumerate(stamps):
        try:
            o, h, l, c = (opens[index], highs[index], lows[index], closes[index])
        except IndexError:  # pragma: no cover - a truncated fixture is a fixture bug
            continue
        if any(value is None for value in (o, h, l, c)):
            continue
        if not (l <= min(o, c) and max(o, c) <= h) or c <= 0:
            continue
        volume = volumes[index] if index < len(volumes) else None
        kept.append(
            Bar(
                day=datetime.fromtimestamp(int(stamp), UTC).date(),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(volume) if volume is not None else 0.0,
            )
        )
    return kept


def _sma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _ema(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    ema = sum(values[:window]) / window
    for value in values[window:]:
        ema = value * alpha + ema * (1.0 - alpha)
    return ema


def _rsi(closes: Sequence[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    changes = [cur - prev for prev, cur in zip(closes[:-1], closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(series: Sequence[Bar], window: int = 14) -> float | None:
    if len(series) < window + 1:
        return None
    ranges = [
        max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        for prev, cur in zip(series[:-1], series[1:])
    ]
    atr = sum(ranges[:window]) / window
    for value in ranges[window:]:
        atr = (atr * (window - 1) + value) / window
    return atr


def _volatility(closes: Sequence[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    returns = [
        math.log(cur / prev)
        for prev, cur in zip(closes[-window - 1 : -1], closes[-window:])
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance) * math.sqrt(252.0) * 100.0


def shift_months(moment: date, months: int) -> date:
    """Calendar month arithmetic, clamped to the target month's last day."""

    import calendar

    year = moment.year + (moment.month - 1 + months) // 12
    month = (moment.month - 1 + months) % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _anchor(series: Sequence[Bar], target: date, *, tolerance_days: int = 10) -> Bar | None:
    """Last bar at or before ``target``; the first bar only across a small gap."""

    earlier = [bar for bar in series if bar.day <= target]
    if earlier:
        return earlier[-1]
    first = series[0]
    if (first.day - target).days <= tolerance_days:
        return first
    return None


def _return_pct(series: Sequence[Bar], target: date) -> float | None:
    anchor = _anchor(series, target)
    if anchor is None or anchor.close == 0:
        return None
    return (series[-1].close / anchor.close - 1.0) * 100.0


def eod_oracle(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Every EOD/performance cell, recomputed from the fixture's own bars."""

    series = bars(fixture)
    assert len(series) >= 200, f"the 5y fixture must carry >= 200 bars, got {len(series)}"
    closes = [bar.close for bar in series]
    volumes = [bar.volume for bar in series]
    last = series[-1].day

    ytd_anchor = [bar for bar in series if bar.day.year < last.year]
    expected: dict[str, Any] = {
        "bars_count": len(series),
        "first_bar_date": series[0].day.isoformat(),
        "last_bar_date": last.isoformat(),
        "ma_20": _sma(closes, 20),
        "ma_50": _sma(closes, 50),
        "ma_200": _sma(closes, 200),
        "ema_12": _ema(closes, 12),
        "ema_26": _ema(closes, 26),
        "rsi_14": _rsi(closes),
        "atr_14": _atr(series),
        "volatility_20d": _volatility(closes),
        "avg_volume_20d": sum(volumes[-20:]) / 20.0,
        "ret_1d": (closes[-1] / closes[-2] - 1.0) * 100.0,
        "ret_1w": _return_pct(series, last - timedelta(days=7)),
        "ret_1m": _return_pct(series, shift_months(last, -1)),
        "ret_3m": _return_pct(series, shift_months(last, -3)),
        "ret_6m": _return_pct(series, shift_months(last, -6)),
        "ret_ytd": (
            (closes[-1] / ytd_anchor[-1].close - 1.0) * 100.0 if ytd_anchor else None
        ),
        "ret_1y": _return_pct(series, shift_months(last, -12)),
        "ret_3y": _return_pct(series, shift_months(last, -36)),
        "ret_5y": _return_pct(series, shift_months(last, -60)),
    }
    assert set(expected) == set(EOD_COLUMNS)
    return expected


def corrupt_bar(fixture: Mapping[str, Any], index: int = 5) -> dict[str, Any]:
    """A copy of the chart whose bar ``index`` violates V2 (``high < close``)."""

    copy = json.loads(json.dumps(fixture))
    quote = copy["chart"]["result"][0]["indicators"]["quote"][0]
    close = quote["close"][index]
    quote["high"][index] = float(close) - 1.0
    return copy


# ---------------------------------------------------------------------------
# Fundamentals oracle
# ---------------------------------------------------------------------------

#: ``column -> (module, key, transform)``. The transform is spelled out rather
#: than named by a flag, so the expected arithmetic is visible here.
FUNDAMENTAL_SOURCES: dict[str, tuple[str, str, str]] = {
    "market_cap": ("price", "marketCap", "number"),
    "market_cap_usd": ("price", "marketCap", "number"),
    "shares_outstanding": ("defaultKeyStatistics", "sharesOutstanding", "number"),
    "float_shares": ("defaultKeyStatistics", "floatShares", "number"),
    "pe_ttm": ("summaryDetail", "trailingPE", "number"),
    "pe_forward": ("summaryDetail", "forwardPE", "number"),
    "peg": ("defaultKeyStatistics", "pegRatio", "number"),
    "pb": ("defaultKeyStatistics", "priceToBook", "number"),
    "ps": ("summaryDetail", "priceToSalesTrailing12Months", "number"),
    "ev_ebitda": ("defaultKeyStatistics", "enterpriseToEbitda", "number"),
    "eps_ttm": ("defaultKeyStatistics", "trailingEps", "number"),
    "eps_forward": ("defaultKeyStatistics", "forwardEps", "number"),
    "dividend_yield": ("summaryDetail", "dividendYield", "percent"),
    "payout_ratio": ("summaryDetail", "payoutRatio", "percent"),
    "revenue_ttm": ("financialData", "totalRevenue", "number"),
    "ebitda_ttm": ("financialData", "ebitda", "number"),
    "net_income_ttm": ("financialData", "netIncomeToCommon", "number"),
    "fcf_ttm": ("financialData", "freeCashflow", "number"),
    "total_debt": ("financialData", "totalDebt", "number"),
    "cash_and_equivalents": ("financialData", "totalCash", "number"),
    "roe": ("financialData", "returnOnEquity", "percent"),
    "debt_to_equity": ("financialData", "debtToEquity", "number"),
}


def fundamentals_oracle(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Every fundamentals/statements cell, read straight out of ``quoteSummary``."""

    result = (fixture.get("quoteSummary") or {}).get("result")
    assert isinstance(result, list) and result, "quoteSummary fixture has no result[0]"
    modules = result[0]
    currency = str((modules.get("price") or {}).get("currency") or "")

    expected: dict[str, Any] = {}
    for column, (module, key, transform) in FUNDAMENTAL_SOURCES.items():
        raw = (modules.get(module) or {}).get(key)
        assert raw is not None, f"fixture is missing {module}.{key} for {column}"
        value = float(raw)
        if transform == "percent":
            value *= 100.0
        expected[column] = value
    if currency != "USD":
        # No FX rung is wired into the adapter; inventing a rate would be worse
        # than an empty cell (02 §3.7 V11: never convert silently).
        expected["market_cap_usd"] = None
    assert set(expected) == set(FUNDAMENTAL_COLUMNS)
    return expected


# ---------------------------------------------------------------------------
# Options oracle
# ---------------------------------------------------------------------------


def options_oracle(
    fixture: Mapping[str, Any], *, dte_target: int = 30
) -> dict[str, Any]:
    """The cells an ATM call at ~30 DTE must produce, from the chain itself."""

    result = (fixture.get("optionChain") or {}).get("result")
    assert isinstance(result, list) and result, "options fixture has no result[0]"
    entry = result[0]
    quote = entry.get("quote") or {}
    spot = float(quote["regularMarketPrice"])
    today = datetime.fromtimestamp(int(quote["regularMarketTime"]), UTC).date()

    expiries = [int(value) for value in (entry.get("expirationDates") or [])]
    assert expiries, "options fixture has no expirationDates"
    chosen = min(
        expiries,
        key=lambda value: abs(
            (datetime.fromtimestamp(value, UTC).date() - today).days - dte_target
        ),
    )
    chain = entry["options"][0]
    assert int(chain["expirationDate"]) == chosen, "fixture chain block is not the chosen expiry"
    calls = [item for item in chain.get("calls") or [] if item.get("strike") is not None]
    assert calls, "options fixture has no calls"
    atm = min(calls, key=lambda item: abs(float(item["strike"]) - spot))

    return {
        "opt_expiry": datetime.fromtimestamp(chosen, UTC).date().isoformat(),
        "opt_dte": (datetime.fromtimestamp(chosen, UTC).date() - today).days,
        "opt_strike_atm": float(atm["strike"]),
        "opt_iv_atm": float(atm["impliedVolatility"]),
        "opt_open_interest_atm": int(atm.get("openInterest") or 0),
        "opt_volume_atm": int(atm.get("volume") or 0),
        "expiration": chosen,
        "spot": spot,
    }


# ---------------------------------------------------------------------------
# Live-path doubles
# ---------------------------------------------------------------------------


def observation(
    instrument_id: str,
    field: str,
    value: Any,
    *,
    unit: str = "count",
    as_of: str = "2026-10-21T20:00:00+00:00",
) -> dict[str, Any]:
    """One observation document (``app.contracts.Observation`` shape)."""

    return {
        "instrument_id": instrument_id,
        "field": field,
        "value_num": None if isinstance(value, str) else float(value),
        "value_text": value if isinstance(value, str) else None,
        "unit": unit,
        "source": "yahoo",
        "source_id": None,
        "as_of": as_of,
        "fetched_at": as_of,
        "confidence": 1.0,
        "verdict": "ok",
        "trust": 3,
        "raw_sha256": None,
        "job_id": None,
    }


#: What each capability answers in the ``_fetch_live`` test. Values are
#: arbitrary-but-distinct so a merged row is attributable to its capability.
CAPABILITY_CELLS: dict[str, tuple[tuple[str, Any, str], ...]] = {
    "quote": (
        ("last_price", 348.22, "price:USD"),
        ("prev_close", 345.00, "price:USD"),
        ("quote_as_of", "2026-10-21T20:00:00+00:00", "timestamp"),
    ),
    "eod_bars": (
        ("bars_count", 1254, "count"),
        ("ma_20", 341.75, "price:USD"),
        ("ma_200", 289.40, "price:USD"),
        ("rsi_14", 61.2, "pct"),
    ),
    "fundamentals": (
        ("market_cap", 5.1675848e12, "price:USD"),
        ("pe_ttm", 32.51, "ratio"),
        ("roe", 171.6, "pct"),
    ),
    "options": (
        ("opt_expiry", "2026-11-20", "date"),
        ("opt_dte", 29, "count"),
        ("opt_strike_atm", 347.5, "price:USD"),
        ("opt_iv_atm", 0.2643, "ratio"),
    ),
}


class CapabilityLadder:
    """``Ladder.fetch`` stand-in: one scripted observation set per capability."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, capability: Any, instrument: Any, fields: set[str]) -> Any:
        from app.providers.base import Verdict
        from app.providers.ladder import LadderResult

        name = str(getattr(capability, "value", capability))
        self.calls.append(name)
        cells = CAPABILITY_CELLS.get(name, ())
        documents = [
            observation(instrument.instrument_id, field, value, unit=unit)
            for field, value, unit in cells
        ]
        return LadderResult(
            observations=documents,
            rung="yahoo",
            verdict=Verdict(ok=True, kind="ok"),
            attempts=[
                {
                    "rung": "L1",
                    "provider": "yahoo",
                    "capability": name,
                    "verdict": "ok",
                    "attempts": 1,
                    "requests": 1,
                    "bytes": 1024,
                }
            ],
        )


def ladder_factory(ladder: CapabilityLadder) -> Any:
    """A ``Runtime.ladder``-shaped factory the runner can call."""

    def factory(**kwargs: Any) -> CapabilityLadder:
        ladder.kwargs = kwargs  # type: ignore[attr-defined]
        return ladder

    return factory


def column_oracle_header() -> list[str]:
    """The frozen v1 header, from the spec — not from ``app.contracts``."""

    return list(COLUMNS)


__all__ = [
    "CAPABILITY_CELLS",
    "CHART_5Y_PATH",
    "COLUMNS",
    "CapabilityLadder",
    "EOD_COLUMNS",
    "FOUR_FORMATS",
    "FUNDAMENTAL_COLUMNS",
    "FUNDAMENTAL_SOURCES",
    "MEDIA_TYPES",
    "OPTION_COLUMNS",
    "OPTIONS_PATH",
    "QUOTE_SUMMARY_PATH",
    "api",
    "artifact_header",
    "artifact_row_count",
    "bars",
    "chart_5y",
    "check_field",
    "column_oracle_header",
    "corrupt_bar",
    "eod_oracle",
    "export_dir",
    "formats",
    "fundamentals_oracle",
    "implemented_formats",
    "ladder_factory",
    "manifest_module",
    "observation",
    "observations",
    "options_chain",
    "options_oracle",
    "quote_summary",
    "raw_body",
    "read_manifest",
    "runner_module",
    "shift_months",
    "submit_payload",
    "verify_manifest",
    "writer_module",
    "yahoo_module",
]
