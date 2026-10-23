"""The Finviz filter catalogue and the ``field op value`` filter DSL.

Two decisions shape this module, and the rest follows from them.

**The catalogue is vendored, not invented.** ``tests/fixtures/finviz_filters.json``
is a probe of Finviz's own filter panel: 82 keys with their label, tab, UI control
and option vocabulary. :class:`FilterSpec` copies that metadata verbatim, so the
parity gate is checked against the artefact the probe produced instead of a
hand-written list of filters that "look about right". The catalogue answers one
question — does Ztockz accept the same filter keys as Finviz — and it does not
claim that the metric behind every key is already sourced.

**Only a sourced metric gets a field.** ``FilterSpec.field`` names the canonical
row column a key filters, and it is ``None`` for the keys no row carries.
**Thirty-six keys map today**: the seven light columns (exchange, sector,
country, market cap, price, change %, current volume) plus twenty-nine parity
metrics that :data:`app.screen.universe.PARITY_FIELDS` copies from the bulk scan
— P/E, PEG, P/B, P/FCF, EV/EBITDA, ROE/ROA/ROIC, the current, quick and
debt/equity ratios, the three margins, share count and float, average/relative
volume, target price, the next earnings date, industry, dividend yield, RSI, the
three SMAs, beta, gap and ATR.

The rule for the twenty-nine is **one quantity, one column, and the provider's
window or unit in the name**. ``ta_beta`` therefore resolves to ``beta_1y`` (the
provider's one-year window; Finviz's beta is five-year monthly) and ``sh_avgvol``
to ``avg_volume_30d``; a field that claimed ``beta`` or ``avg_volume`` would
assert a methodology the row does not have. ``ta_averagetruerange`` resolves to
``atr_14`` and ``ta_gap`` to ``gap_pct`` (signed percent: ``-0.57`` is a 0.57%
gap down), while ``fa_div`` resolves to ``dividend_yield``, which is the
provider's percent (``0.47`` = 0.47%), not a fraction. A key is *not* mapped
when no column measures it, when one key spans several columns (``ta_perf``
covers ten windows, ``ta_volatility`` two), when the provider's unit differs
from Finviz's in a way a name cannot state (``ta_highlow52w`` is a percent
distance and the row carries absolute ``high_52w``/``low_52w``; ``Recommend.All``
is a -1..1 score against Finviz's 1..5, and inverting it would mean inventing a
derived column), or when the provider's column answers ``null`` for **every**
row — the live probe returned no values at all for ``price_earnings_forward``,
``price_sales_ttm``, ``short_float`` and ``ipo_date``, and a filter on a column
that is always null could only return an empty screen. Mapping those would let a
caller read a different question's answer, or an empty one, as parity; the
forty-six refusals in :mod:`app.screen.filter_fields` name the missing metric
instead.

**The DSL is field/op/value, and unknown never passes.** ``compile_filter``
rejects an unknown field, an unknown operator and an operator that cannot work
on the field's kind (``gt`` on a sector, ``contains`` on a price) before a single
row is read. ``apply_filters`` then ANDs the predicates and keeps input order.

A row whose value is null is dropped by **every** operator, ``ne`` included: a
missing P/E is not evidence that the P/E differs from 10, and a screen that leaks
unknown rows into ``PE != 10`` is wrong in the expensive direction. For the same
reason a non-finite value (NaN/inf) is treated as unknown, not as a number.

Text rules are split on purpose: ``eq``/``ne``/``in`` compare exactly (an
exchange code is an identifier), ``contains`` searches case-insensitively (it is
the operator a human types). Values inside ``in`` and ``between`` are tuples of
numbers or strings, never a bare scalar.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

#: The five tabs Finviz groups its filters under. An entry outside this set means
#: the probe grew a tab the app does not model yet.
FILTER_TABS: tuple[str, ...] = ("descriptive", "fundamental", "technical", "news", "etf")

#: The four control types in the probe. The spelling is copied because the UI
#: layer will switch on it; a typo would render a range as a modal.
UI_TYPES: frozenset[str] = frozenset(
    {"floatRangeSelect", "modalSelect", "select", "tickersInput"}
)

#: Canonical row field → comparison kind. Every field here is carried by a real
#: row: the thirteen light columns, plus the parity metrics a bulk scan copies
#: from the provider (:data:`app.screen.universe.PARITY_FIELDS`). A filter may not
#: name a metric that no row carries — that is what
#: :mod:`app.screen.filter_fields` refuses instead of ignoring.
FIELDS: Mapping[str, str] = MappingProxyType(
    {
        # -- light row
        "symbol": "text",
        "name": "text",
        "exchange": "text",
        "country": "text",
        "sector": "text",
        # The provider's industry string ("Semiconductors"), copied verbatim by
        # the parity scan. It is the vendor's classification, not Finviz's
        # option tokens; `contains` is case-insensitive, `eq` is exact.
        "industry": "text",
        "currency": "text",
        "price": "number",
        "change_pct": "number",
        "volume": "number",
        "turnover": "number",
        "turnover_usd": "number",
        "market_cap_usd": "number",
        "aum_usd": "number",
        "is_primary": "bool",
        # -- valuation multiples (provider column in parentheses)
        "pe_ttm": "number",  # price_earnings_ttm
        "pe_forward": "number",  # price_earnings_forward: null on every live row
        "peg": "number",  # price_earnings_growth_ttm
        "ps": "number",  # price_sales_ttm: null on every live row
        "pb": "number",  # price_book_fq
        "pfcf": "number",  # price_free_cash_flow_ttm
        "ev_ebitda": "number",  # enterprise_value_ebitda_ttm
        # -- dividends (percent, as the provider reports it: 0.47 = 0.47%)
        "dividend_yield": "number",  # dividends_yield, percent not a fraction
        # -- profitability and leverage
        "roe": "number",  # return_on_equity
        "roa": "number",  # return_on_assets
        "roic": "number",  # return_on_invested_capital
        "current_ratio": "number",
        "quick_ratio": "number",
        "debt_to_equity": "number",
        "gross_margin": "number",
        "oper_margin": "number",  # operating_margin
        "profit_margin": "number",  # net_margin
        # -- forward estimates (levels in currency, not growth rates)
        "eps_forecast_next_fy": "number",
        "eps_forecast_next_fq": "number",
        "revenue_forecast_next_fy": "number",
        # -- share structure and trading depth
        "shares_outstanding": "number",
        "float_shares": "number",
        "short_float": "number",  # null on every live row
        "avg_volume_10d": "number",
        "avg_volume_30d": "number",
        "rel_volume_10d": "number",
        # -- analyst and event data
        # TradingView's own -1..1 mean rating (not Finviz's 1..5 score), so no
        # Finviz key maps to it: a Finviz-scale value would match nothing.
        "recommendation": "number",  # Recommend.All, +1 = strong buy, -1 = sell
        "target_price": "number",
        "earnings_date": "number",  # provider epoch seconds, often null
        "listing_date": "number",  # ipo_date, epoch seconds, null on every live row
        # -- technicals
        "sma_20": "number",  # the average price itself, not the distance to it
        "sma_50": "number",
        "sma_200": "number",
        "rsi_14": "number",
        "beta_1y": "number",  # one-year window; Finviz's beta is five-year
        "high_52w": "number",
        "low_52w": "number",
        # Percent, signed: -0.57 = opened 0.57% below the previous close.
        "gap_pct": "number",  # gap
        # Average true range (14 sessions) in price units, not a percent.
        "atr_14": "number",  # ATR
        "volatility_d": "number",
        "volatility_w": "number",
        "volatility_m": "number",
        "ret_1w": "number",
        "ret_1m": "number",
        "ret_3m": "number",
        "ret_6m": "number",
        "ret_ytd": "number",
        "ret_1y": "number",
        "ret_5y": "number",
        "ret_10y": "number",
    }
)

#: Operators that compare numbers, and the one that searches text. ``eq``/``ne``
#: and ``in`` work on every kind; these require the kind to match, so a filter
#: that could never match a row fails at compile time instead of returning [].
NUMERIC_OPS: frozenset[str] = frozenset({"gt", "gte", "lt", "lte", "between"})
TEXT_OPS: frozenset[str] = frozenset({"contains"})
OPERATORS: frozenset[str] = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "in", "between", "contains"}
)
_SYMBOLS: Mapping[str, str] = MappingProxyType(
    {"=": "eq", "==": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
)

#: ``market_cap_usd >= 100000000``, ``sector = Technology``, ``price between 5..10``,
#: ``exchange in (XNAS, XNYS)``. The named form needs a word boundary so ``in``
#: cannot match the first two letters of a value.
_EXPRESSION = re.compile(
    r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?P<symbol><=|>=|==|!=|=|<|>)"
    r"|(?P<name>contains|between|gte|lte|eq|ne|gt|lt|in)\b)\s*"
    r"(?P<value>.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """One Finviz filter key, exactly as the probe found it.

    ``field`` is the canonical row column the key filters, or ``None`` when the
    light universe does not carry that metric yet. ``options`` keeps Finviz's
    vocabulary verbatim (``u5``, ``largeover``, ...): turning ``u5`` into ``< 5``
    is a metric decision that belongs with the metric, not with the catalogue.
    """

    key: str
    label: str
    tab: str
    ui_type: str
    field: str | None
    options: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        """Can a filter on this key be compiled today?"""
        return self.field is not None


@dataclass(frozen=True, slots=True)
class Filter:
    """One compiled predicate: ``row[field] <op> value``."""

    field: str
    op: str
    value: Any

    def as_dict(self) -> dict[str, Any]:
        """The wire form, so a client can round-trip what it parsed."""

        return {"field": self.field, "op": self.op, "value": self.value}


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def _to_number(value: Any) -> float | None:
    """A finite float, or ``None`` when the value is not a usable number.

    Booleans are rejected on purpose (``True`` is an ``int`` in Python) and
    NaN/inf are rejected because a provider can send them and they compare
    false in every direction, which would turn an unknown value into a silent
    mismatch.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _unquote(value: Any) -> Any:
    """Strip one pair of matching quotes from a scalar, leaving anything else."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _items(field: str, raw: Any) -> list[Any]:
    """The members of an ``in`` value: a sequence, or ``(a, b)`` / ``a,b``."""

    if isinstance(raw, str):
        text = raw.strip()
        if len(text) >= 2 and text[0] in "([" and text[-1] in ")]":
            text = text[1:-1]
        parts: list[Any] = [part.strip() for part in text.split(",")] if text.strip() else []
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        raise ValueError(f"{field} 'in' expects a list of values, got {raw!r}")
    if not parts:
        raise ValueError(f"{field} 'in' expects at least one value")
    return [_unquote(part) for part in parts]


def _bounds(field: str, raw: Any) -> tuple[float, float]:
    """The inclusive ``(lo, hi)`` of a ``between`` filter."""

    if isinstance(raw, str):
        parts: Any = re.split(r"\s*(?:\.\.|,|:)\s*", raw.strip().strip("[]()"))
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        raise ValueError(f"{field} 'between' expects two numbers, got {raw!r}")
    if len(parts) != 2:
        raise ValueError(f"{field} 'between' expects exactly two numbers, got {raw!r}")
    low, high = (_to_number(part) for part in parts)
    if low is None or high is None:
        raise ValueError(f"{field} 'between' expects numbers, got {raw!r}")
    if low > high:
        raise ValueError(f"{field} 'between' bounds are reversed: {low} > {high}")
    return low, high


def _boolean(field: str, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        token = raw.strip().casefold()
        if token in {"true", "yes"}:
            return True
        if token in {"false", "no"}:
            return False
    raise ValueError(f"{field} expects a boolean, got {raw!r}")


def _operator(op: Any) -> str:
    """Normalise an operator token; unknown tokens are returned for the caller."""

    if not isinstance(op, str):
        raise ValueError(f"unknown filter operator {op!r}")
    token = op.strip().casefold()
    return _SYMBOLS.get(token, token)


def _value(field: str, kind: str, op: str, raw: Any) -> Any:
    """Coerce ``raw`` to the type ``kind``/``op`` requires, or raise ``ValueError``."""

    if kind == "number":
        if op == "between":
            return _bounds(field, raw)
        if op == "in":
            numbers = [_to_number(item) for item in _items(field, raw)]
            if any(number is None for number in numbers):
                raise ValueError(f"{field} 'in' expects numbers, got {raw!r}")
            return tuple(numbers)
        number = _to_number(raw)
        if number is None:
            raise ValueError(f"{field} expects a number, got {raw!r}")
        return number

    if kind == "bool":
        if op == "in":
            return tuple(_boolean(field, item) for item in _items(field, raw))
        return _boolean(field, raw)

    if op == "contains":
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{field} 'contains' expects a non-empty string, got {raw!r}")
        return raw.strip()
    if op == "in":
        items = _items(field, raw)
        if not all(isinstance(item, str) for item in items):
            raise ValueError(f"{field} 'in' expects strings, got {raw!r}")
        return tuple(items)
    if not isinstance(raw, str):
        raise ValueError(f"{field} expects a string, got {raw!r}")
    return raw


# ---------------------------------------------------------------------------
# Compile / parse
# ---------------------------------------------------------------------------


def _expression(text: str) -> tuple[Any, Any, Any]:
    """Split ``"field op value"`` into its three raw parts."""

    match = _EXPRESSION.match(text)
    if match is None:
        raise ValueError(
            f"cannot parse filter expression {text!r}; expected 'field op value', "
            "e.g. 'market_cap_usd gte 100000000'"
        )
    if not match.group("value").strip():
        raise ValueError(f"filter expression {text!r} has no value")
    return (
        match.group("field"),
        match.group("symbol") or match.group("name"),
        _unquote(match.group("value")),
    )


def _unpack(spec: Any) -> tuple[Any, Any, Any]:
    """Accept every form a filter may arrive in and return its three parts."""

    if isinstance(spec, Filter):
        return spec.field, spec.op, spec.value
    if isinstance(spec, str):
        return _expression(spec)
    if isinstance(spec, Mapping):
        missing = [name for name in ("field", "op", "value") if name not in spec]
        if missing:
            raise ValueError(f"a filter mapping needs {', '.join(missing)}: {dict(spec)!r}")
        return spec["field"], spec["op"], spec["value"]
    if isinstance(spec, (tuple, list)):
        if len(spec) != 3:
            raise ValueError(f"a filter triple is (field, op, value), got {len(spec)} items")
        return spec[0], spec[1], spec[2]
    raise ValueError(f"cannot compile a filter from {type(spec).__name__}: {spec!r}")


def compile_filter(spec: Filter | Mapping[str, Any] | Sequence[Any] | str) -> Filter:
    """Validate one filter and return it in canonical form.

    Raises ``ValueError`` for an unknown field, an unknown operator, a value of
    the wrong type, or an operator that cannot apply to the field's kind.
    """

    field, op, raw = _unpack(spec)
    if not isinstance(field, str) or field not in FIELDS:
        raise ValueError(
            f"unknown filter field {field!r}; available fields: {', '.join(FIELDS)}"
        )
    operator = _operator(op)
    if operator not in OPERATORS:
        raise ValueError(
            f"unknown filter operator {op!r}; available operators: "
            f"{', '.join(sorted(OPERATORS))}"
        )
    kind = FIELDS[field]
    if operator in NUMERIC_OPS and kind != "number":
        raise ValueError(
            f"operator {operator!r} needs a numeric field; {field!r} is {kind}"
        )
    if operator in TEXT_OPS and kind != "text":
        raise ValueError(f"operator {operator!r} needs a text field; {field!r} is {kind}")
    return Filter(field=field, op=operator, value=_value(field, kind, operator, raw))


def _is_triple(raw: Sequence[Any]) -> bool:
    return (
        len(raw) == 3
        and isinstance(raw[0], str)
        and isinstance(raw[1], str)
        and _operator(raw[1]) in OPERATORS
    )


def parse_filters(raw: Any = None) -> tuple[Filter, ...]:
    """Parse a filter document into compiled filters.

    Accepts ``None``, one expression string, a ``;``/newline separated string, a
    mapping, a ``Filter``, a ``(field, op, value)`` triple, or any iterable of
    those. The result is a tuple so a caller cannot mutate a parsed document.
    """

    if raw is None:
        return ()
    if isinstance(raw, str):
        expressions = [part.strip() for part in re.split(r"[;\n]", raw) if part.strip()]
        return tuple(compile_filter(expression) for expression in expressions)
    if isinstance(raw, (Filter, Mapping)):
        return (compile_filter(raw),)
    if isinstance(raw, (tuple, list)) and _is_triple(raw):
        return (compile_filter(raw),)
    if isinstance(raw, Iterable):
        return tuple(compile_filter(item) for item in raw)
    raise ValueError(f"cannot parse filters from {type(raw).__name__}: {raw!r}")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _matches(row: Mapping[str, Any], item: Filter) -> bool:
    """Does one row satisfy one filter? Unknown values never do."""

    value = row.get(item.field)
    if value is None:
        return False
    kind = FIELDS[item.field]
    expected = item.value
    op = item.op

    if kind == "number":
        number = _to_number(value)
        if number is None:
            return False
        if op == "eq":
            return number == expected
        if op == "ne":
            return number != expected
        if op == "gt":
            return number > expected
        if op == "gte":
            return number >= expected
        if op == "lt":
            return number < expected
        if op == "lte":
            return number <= expected
        if op == "between":
            return expected[0] <= number <= expected[1]
        if op == "in":
            return number in expected
        return False

    if kind == "bool":
        if not isinstance(value, bool):
            return False
        if op == "eq":
            return value is expected
        if op == "ne":
            return value is not expected
        if op == "in":
            return value in expected
        return False

    text = value if isinstance(value, str) else str(value)
    if op == "eq":
        return text == expected
    if op == "ne":
        return text != expected
    if op == "in":
        return text in expected
    if op == "contains":
        return expected.casefold() in text.casefold()
    return False


def apply_filters(
    rows: Iterable[dict[str, Any]], filters: Any = None
) -> list[dict[str, Any]]:
    """Return the rows that satisfy every filter, in input order.

    Rows are passed through untouched — same objects, no copy, no sort — so a
    caller can chain this with the ranker or the exporter without a second
    normalisation step. ``filters=None`` (or ``[]``) is the identity.
    """

    compiled = parse_filters(filters)
    if not compiled:
        return list(rows)
    return [
        row for row in rows if all(_matches(row, item) for item in compiled)
    ]


# ---------------------------------------------------------------------------
# The catalogue (82 keys, probe order, metadata verbatim)
# ---------------------------------------------------------------------------

#: Finviz key → spec, in the order the probe returned them (the panel's own
#: order, which is what a UI should follow). ``FILTER_KEYS`` is the same
#: sequence as a tuple for callers that only need the vocabulary.
_CATALOG_SPECS: tuple[FilterSpec, ...] = (
    FilterSpec(
        "exch",
        "Exchange",
        "descriptive",
        "modalSelect",
        "exchange",
        ("", "amex", "cboe", "nasd", "nyse", "modal"),
    ),
    FilterSpec(
        "idx",
        "Index",
        "descriptive",
        "modalSelect",
        None,
        ("", "sp500", "ndx", "dji", "rut", "modal"),
    ),
    FilterSpec(
        "sec",
        "Sector",
        "descriptive",
        "modalSelect",
        "sector",
        ("", "basicmaterials", "communicationservices", "consumercyclical",
         "consumerdefensive", "energy", "financial", "healthcare", "industrials",
         "realestate", "technology", "utilities", "modal"),
    ),
    FilterSpec(
        "industry",
        "Industry",
        "descriptive",
        "modalSelect",
        "industry",
        ("", "stocksexshell", "shellcompanies", "advertisingagencies", "aerospacedefense",
         "agriculturalinputs", "airlines", "airportsairservices", "aluminum",
         "apparelmanufacturing", "apparelretail", "assetmanagement",
         "automanufacturers", "autoparts", "autotruckdealerships",
         "banksdiversified", "banksregional", "beveragesbrewers",
         "beveragesnonalcoholic", "beverageswineriesdistilleries", "biotechnology",
         "broadcasting", "buildingmaterials", "buildingproductsequipment",
         "businessequipmentsupplies", "capitalmarkets", "chemicals", "cokingcoal",
         "communicationequipment", "computerhardware", "confectioners",
         "conglomerates", "consultingservices", "consumerelectronics", "copper",
         "creditservices", "departmentstores", "diagnosticsresearch",
         "discountstores", "drugmanufacturersgeneral",
         "drugmanufacturersspecialtygeneric", "educationtrainingservices",
         "electricalequipmentparts", "electroniccomponents",
         "electronicgamingmultimedia", "electronicscomputerdistribution",
         "engineeringconstruction", "entertainment",
         "farmheavyconstructionmachinery", "farmproducts", "financialconglomerates",
         "financialdatastockexchanges", "fooddistribution", "footwearaccessories",
         "furnishingsfixturesappliances", "gambling", "gold", "grocerystores",
         "healthcareplans", "healthinformationservices", "homeimprovementretail",
         "householdpersonalproducts", "industrialdistribution",
         "informationtechnologyservices", "infrastructureoperations",
         "insurancebrokers", "insurancediversified", "insurancelife",
         "insurancepropertycasualty", "insurancereinsurance", "insurancespecialty",
         "integratedfreightlogistics", "internetcontentinformation",
         "internetretail", "leisure", "lodging", "lumberwoodproduction",
         "luxurygoods", "marineshipping", "medicalcarefacilities", "medicaldevices",
         "medicaldistribution", "medicalinstrumentssupplies", "metalfabrication",
         "mortgagefinance", "oilgasdrilling", "oilgasep", "oilgasequipmentservices",
         "oilgasintegrated", "oilgasmidstream", "oilgasrefiningmarketing",
         "otherindustrialmetalsmining", "otherpreciousmetalsmining",
         "packagedfoods", "packagingcontainers", "paperpaperproducts",
         "personalservices", "pharmaceuticalretailers",
         "pollutiontreatmentcontrols", "publishing", "railroads",
         "realestatedevelopment", "realestatediversified", "realestateservices",
         "recreationalvehicles", "reitdiversified", "reithealthcarefacilities",
         "reithotelmotel", "reitindustrial", "reitmortgage", "reitoffice",
         "reitresidential", "reitretail", "reitspecialty", "rentalleasingservices",
         "residentialconstruction", "resortscasinos", "restaurants",
         "scientifictechnicalinstruments", "securityprotectionservices",
         "semiconductorequipmentmaterials", "semiconductors", "silver",
         "softwareapplication", "softwareinfrastructure", "solar",
         "specialtybusinessservices", "specialtychemicals",
         "specialtyindustrialmachinery", "specialtyretail",
         "staffingemploymentservices", "steel", "telecomservices",
         "textilemanufacturing", "thermalcoal", "tobacco", "toolsaccessories",
         "travelservices", "trucking", "uranium", "utilitiesdiversified",
         "utilitiesindependentpowerproducers", "utilitiesregulatedelectric",
         "utilitiesregulatedgas", "utilitiesregulatedwater", "utilitiesrenewable",
         "wastemanagement", "modal"),
    ),
    FilterSpec(
        "geo",
        "Country",
        "descriptive",
        "modalSelect",
        "country",
        ("", "usa", "notusa", "asia", "europe", "latinamerica", "bric", "argentina",
         "australia", "bahamas", "belgium", "benelux", "bermuda", "brazil",
         "canada", "caymanislands", "chile", "china", "chinahongkong", "colombia",
         "cyprus", "denmark", "finland", "france", "germany", "greece", "hongkong",
         "hungary", "iceland", "india", "indonesia", "ireland", "israel", "italy",
         "japan", "jordan", "kazakhstan", "luxembourg", "malaysia", "malta",
         "mexico", "monaco", "netherlands", "newzealand", "norway", "panama",
         "peru", "philippines", "portugal", "russia", "singapore", "southafrica",
         "southkorea", "spain", "sweden", "switzerland", "taiwan", "thailand",
         "turkey", "unitedarabemirates", "unitedkingdom", "uruguay", "vietnam",
         "modal"),
    ),
    FilterSpec(
        "cap",
        "Market Cap.",
        "descriptive",
        "floatRangeSelect",
        "market_cap_usd",
        ("", "mega", "large", "mid", "small", "micro", "nano", "largeover", "midover",
         "smallover", "microover", "largeunder", "midunder", "smallunder",
         "microunder", "frange"),
    ),
    FilterSpec(
        "fa_div",
        "Dividend Yield",
        "descriptive",
        "floatRangeSelect",
        "dividend_yield",
        ("", "none", "pos", "high", "veryhigh", "o1", "o2", "o3", "o4", "o5", "o6", "o7",
         "o8", "o9", "o10", "frange"),
    ),
    FilterSpec(
        "sh_short",
        "Short Float",
        "descriptive",
        "floatRangeSelect",
        None,
        ("", "low", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5", "o10", "o15",
         "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "an_recom",
        "Analyst Recom.",
        "descriptive",
        "modalSelect",
        None,
        ("", "strongbuy", "buybetter", "buy", "holdbetter", "hold", "holdworse", "sell",
         "sellworse", "strongsell", "modal"),
    ),
    FilterSpec(
        "sh_opt",
        "Option/Short",
        "descriptive",
        "modalSelect",
        None,
        ("", "option", "short", "notoption", "notshort", "optionshort", "optionnotshort",
         "notoptionshort", "notoptionnotshort", "modal"),
    ),
    FilterSpec(
        "earningsdate",
        "Earnings Date",
        "descriptive",
        "modalSelect",
        "earnings_date",
        ("", "today", "todaybefore", "todayafter", "tomorrow", "tomorrowbefore",
         "tomorrowafter", "yesterday", "yesterdaybefore", "yesterdayafter",
         "nextdays5", "prevdays5", "thisweek", "nextweek", "prevweek", "thismonth",
         "modal"),
    ),
    FilterSpec(
        "sh_avgvol",
        "Average Volume",
        "descriptive",
        "floatRangeSelect",
        "avg_volume_30d",
        ("", "u50", "u100", "u500", "u750", "u1000", "o50", "o100", "o200", "o300", "o400",
         "o500", "o750", "o1000", "o2000", "100to500", "100to1000", "500to1000",
         "500to10000", "frange"),
    ),
    FilterSpec(
        "sh_relvol",
        "Relative Volume",
        "descriptive",
        "floatRangeSelect",
        "rel_volume_10d",
        ("", "o10", "o5", "o3", "o2", "o1.5", "o1", "o0.75", "o0.5", "o0.25", "u2", "u1.5",
         "u1", "u0.75", "u0.5", "u0.25", "u0.1", "frange"),
    ),
    FilterSpec(
        "sh_curvol",
        "Current Volume",
        "descriptive",
        "modalSelect",
        "volume",
        ("", "modal"),
    ),
    FilterSpec(
        "sh_trades",
        "Trades",
        "descriptive",
        "floatRangeSelect",
        None,
        ("", "u100", "u500", "u1000", "u5000", "u10000", "o0", "o100", "o500", "o1000",
         "o5000", "o10000", "o15000", "o20000", "o50000", "o100000", "frange"),
    ),
    FilterSpec(
        "sh_price",
        "Price $",
        "descriptive",
        "floatRangeSelect",
        "price",
        ("", "u1", "u2", "u3", "u4", "u5", "u7", "u10", "u15", "u20", "u30", "u40", "u50",
         "o1", "o2", "o3", "o4", "o5", "o7", "o10", "o15", "o20", "o30", "o40",
         "o50", "o60", "o70", "o80", "o90", "o100", "1to5", "1to10", "1to20",
         "5to10", "5to20", "5to50", "10to20", "10to50", "20to50", "50to100",
         "frange"),
    ),
    FilterSpec(
        "targetprice",
        "Target Price",
        "descriptive",
        "select",
        "target_price",
        ("", "a50", "a40", "a30", "a20", "a10", "a5", "above", "below", "b5", "b10", "b20",
         "b30", "b40", "b50", "modal"),
    ),
    FilterSpec(
        "ipodate",
        "IPO Date",
        "descriptive",
        "select",
        None,
        ("", "today", "yesterday", "prevweek", "prevmonth", "prevquarter", "prevyear",
         "prev2yrs", "prev3yrs", "prev5yrs", "more1", "more5", "more10", "more15",
         "more20", "more25", "modal"),
    ),
    FilterSpec(
        "sh_outstanding",
        "Shares Outstanding",
        "descriptive",
        "floatRangeSelect",
        "shares_outstanding",
        ("", "u1", "u5", "u10", "u20", "u50", "u100", "o1", "o2", "o5", "o10", "o20", "o50",
         "o100", "o200", "o500", "o1000", "frange"),
    ),
    FilterSpec(
        "sh_float",
        "Float",
        "descriptive",
        "select",
        "float_shares",
        ("", "u1", "u5", "u10", "u20", "u50", "u100", "o1", "o2", "o5", "o10", "o20", "o50",
         "o100", "o200", "o500", "o1000", "u10p", "u20p", "u30p", "u40p", "u50p",
         "u60p", "u70p", "u80p", "u90p", "o10p", "o20p", "o30p", "o40p", "o50p",
         "o60p", "o70p", "o80p", "o90p", "modal"),
    ),
    FilterSpec(
        "theme",
        "Theme",
        "descriptive",
        "modalSelect",
        None,
        ("", "agingpopulationlongevity", "agriculturefoodtech", "artificialintelligence",
         "autonomoussystems", "bigdata", "biometrics", "cloudcomputing",
         "commoditiesagriculture", "commoditiesenergy", "commoditiesmetals",
         "consumergoods", "cryptoblockchain", "cybersecurity", "defenseaerospace",
         "digitalentertainment", "ecommerce", "educationtechnology",
         "electricvehicles", "energyrenewable", "energytraditional",
         "environmentalsustainability", "fintech", "hardware", "healthcarebiotech",
         "healthyfoodnutrition", "industrialautomation", "internetofthings",
         "nanotechnology", "quantumcomputing", "realestatereits", "robotics",
         "semiconductors", "smarthome", "socialmedia", "software", "spacetech",
         "telecommunications", "transportationlogistics", "virtualaugmentedreality",
         "wearables", "modal"),
    ),
    FilterSpec(
        "subtheme",
        "Sub-theme",
        "descriptive",
        "modalSelect",
        None,
        ("", "agriculturealtprotein", "agriculturecropinputs", "agricultureindoorfarming",
         "agricultureprocessing", "agriculturesmartfarming", "aiadssearch", "aiagi",
         "aiapplications", "aicloud", "aicompute", "aidata", "aiedge", "aienergy",
         "aienterprise", "aimodels", "ainetworking", "airobotics", "aisecurity",
         "automationautomation", "automationdprinting", "automationiot",
         "automationlogistics", "automationmachinevision", "automationrobotics",
         "automationsoftware", "autonomousavmobility", "autonomousdefense",
         "autonomousindustrial", "autonomousmachinevision", "autonomoussoftware",
         "autonomousspecialized", "bigdataaiplatforms", "bigdataanalyticsbi",
         "bigdatainfrastructure", "bigdataproviders", "biometricsgovdefense",
         "biometricshardware", "biometricsidentity", "biometricssoftware",
         "blockchainenterprise", "blockchaininfrastructure", "blockchainmining",
         "blockchainpayments", "blockchainplatforms", "blockchaintokenization",
         "clouddatabases", "clouddatacenters", "clouddevops", "cloudedge",
         "cloudhardware", "cloudhsaas", "cloudhybridcloud", "cloudhyperscalers",
         "cloudmulticloud", "cloudpaas", "cloudsecurity", "cloudserverless",
         "commagribiofuels", "commagrifertilizers", "commagrigrains",
         "commagrilivestock", "commagrisofts", "commenergybiofuels",
         "commenergygaslng", "commenergyoil", "commenergyuranium",
         "commmetalsbattery", "commmetalsgold", "commmetalsindustrial",
         "commmetalsprecious", "commmetalsrareearth", "commmetalsrecycling",
         "commmetalssilver", "consumerapparel", "consumerfarmdirect",
         "consumerfood", "consumerhousehold", "consumerluxury",
         "consumersecondhand", "cybersecurityappsecurity", "cybersecuritycloud",
         "cybersecurityendpoint", "cybersecurityidentityiam",
         "cybersecuritynetwork", "cybersecuritysiem", "cybersecuritythreatops",
         "cybersecurityzerotrust", "defenseaviation", "defensecyberdefense",
         "defensedrones", "defensemanufacturing", "defensemissiles",
         "defensespacetech", "defenseweapons", "ecommerceadsmedia", "ecommercedtc",
         "ecommercegrocery", "ecommercelogistics", "ecommercemarketplaces",
         "ecommerceomnichannel", "ecommerceplatforms", "ecommercesecondhand",
         "ecommercesocial", "educationcurriculum", "educationinfrastructure",
         "educationplatforms", "educationworkforce", "energybasemajors",
         "energybasenuclear", "energybaseoilproduction", "energybaseoilrefining",
         "energybaseoilservices", "energybasethermal", "energybaseutilities",
         "energycleanbatteries", "energycleanbiofuels", "energycleangeothermal",
         "energycleanhydrogen", "energycleanmaterials", "energycleansmartgrid",
         "energycleansolar", "energycleanutilities", "energycleanwind",
         "entertainmentbetting", "entertainmentgambling", "entertainmentgaming",
         "entertainmentinfrastructure", "entertainmentmusic", "entertainmentvideo",
         "environmentalagriculture", "environmentalairquality",
         "environmentalclimate", "environmentalwaste", "environmentalwater",
         "evsbatteries", "evscharging", "evschips", "evsfleets", "evsmanufacturers",
         "evsselfdriving", "evssuppliers", "fintechblockchain", "fintechexchanges",
         "fintechinsurance", "fintechlending", "fintechneobanks", "fintechpayments",
         "fintechtrading", "hardwaredatacenters", "hardwareelectronics",
         "hardwaregaming", "hardwareindustrialiot", "hardwarenetworking",
         "hardwarenextgen", "hardwarepcsdevices", "hardwareprinting",
         "hardwareservers", "hardwarestorage", "hardwaretelecom",
         "healthcaredevices", "healthcarediagnostics", "healthcaregenomics",
         "healthcareitdata", "healthcaremetabolic", "healthcarenextgen",
         "healthcareoncology", "healthcaretelemedicine", "healthcaretherapeutics",
         "iotedgedevices", "iotenterprise", "iothardware", "iotnetworking",
         "iotsecurity", "iotsoftware", "longevityagingpharma",
         "longevityhealthcare", "longevityhealthyaging", "longevityseniorliving",
         "nanotechelectronics", "nanotechenergy", "nanotechmaterials",
         "nanotechmedicine", "nanotechproducts", "nanotechresearchtools",
         "nutritionaltprotein", "nutritionmealdelivery", "nutritionretailers",
         "nutritionsupplements", "quantumapplications", "quantumcloud",
         "quantumenablingtech", "quantumhardware", "quantumnetworking",
         "quantumsoftware", "realestatehealthcare", "realestatehousing",
         "realestateittelecom", "realestateoffice", "realestateretail",
         "realestatetourism", "realestatewarehousing", "roboticsautomation",
         "roboticsavmobility", "roboticsconsumer", "roboticslogistics",
         "roboticsmachinevision", "roboticsmedical", "semisanalog", "semiscompute",
         "semisdesigntools", "semisfoundries", "semislithography", "semismemory",
         "semisnextgen", "semispackaging", "semiswireless", "smarthomeautomation",
         "smarthomedevices", "smarthomeenergy", "smarthomenetworking",
         "smarthomesecurity", "smarthomevoiceai", "socialadvertising",
         "socialgaming", "socialnetworks", "socialniche", "socialvisualcontent",
         "softwarecollaboration", "softwarecrm", "softwaredataanalytics",
         "softwaredesign", "softwaredevops", "softwareecommerce",
         "softwareenterprise", "softwaregaming", "softwarehsaas", "softwareos",
         "softwaresecurity", "softwarevsaas", "spacedataanalytics", "spacedefense",
         "spaceinfrastructure", "spacelaunch", "spacesatellites",
         "telecomcloudedge", "telecomenterprise", "telecomg",
         "telecominfrastructure", "telecomsatcom", "telecomwireless",
         "transportationaircargo", "transportationairtravel",
         "transportationinfrastructure", "transportationmaritime",
         "transportationnextgen", "transportationrail", "transportationtrucking",
         "transportationwarehousing", "varealityapplications",
         "varealityenterprise", "varealityhardware", "varealityinfrastructure",
         "varealitysoftware", "wearablesimmersive", "wearablesmedical",
         "wearablessmartwatches", "wearablessoftware", "wearablessport", "modal"),
    ),
    FilterSpec(
        "fa_pe",
        "P/E",
        "fundamental",
        "floatRangeSelect",
        "pe_ttm",
        ("", "low", "profitable", "high", "u5", "u10", "u15", "u20", "u25", "u30", "u35",
         "u40", "u45", "u50", "o5", "o10", "o15", "o20", "o25", "o30", "o35", "o40",
         "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_fpe",
        "Forward P/E",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "low", "profitable", "high", "u5", "u10", "u15", "u20", "u25", "u30", "u35",
         "u40", "u45", "u50", "o5", "o10", "o15", "o20", "o25", "o30", "o35", "o40",
         "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_peg",
        "PEG",
        "fundamental",
        "floatRangeSelect",
        "peg",
        ("", "low", "high", "u1", "u2", "u3", "o1", "o2", "o3", "frange"),
    ),
    FilterSpec(
        "fa_ps",
        "P/S",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "low", "high", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10",
         "o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o9", "o10", "frange"),
    ),
    FilterSpec(
        "fa_pb",
        "P/B",
        "fundamental",
        "floatRangeSelect",
        "pb",
        ("", "low", "high", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10",
         "o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o9", "o10", "frange"),
    ),
    FilterSpec(
        "fa_pc",
        "Price/Cash",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "low", "high", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10",
         "o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o9", "o10", "o20", "o30",
         "o40", "o50", "frange"),
    ),
    FilterSpec(
        "fa_pfcf",
        "Price/Free Cash Flow",
        "fundamental",
        "floatRangeSelect",
        "pfcf",
        ("", "low", "high", "u5", "u10", "u15", "u20", "u25", "u30", "u35", "u40", "u45",
         "u50", "u60", "u70", "u80", "u90", "u100", "o5", "o10", "o15", "o20",
         "o25", "o30", "o35", "o40", "o45", "o50", "o60", "o70", "o80", "o90",
         "o100", "frange"),
    ),
    FilterSpec(
        "fa_evebitda",
        "EV/EBITDA",
        "fundamental",
        "floatRangeSelect",
        "ev_ebitda",
        ("", "negative", "low", "profitable", "high", "u5", "u10", "u15", "u20", "u25",
         "u30", "u35", "u40", "u45", "u50", "o5", "o10", "o15", "o20", "o25", "o30",
         "o35", "o40", "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_evsales",
        "EV/Sales",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "negative", "low", "positive", "high", "u1", "u2", "u3", "u4", "u5", "u6",
         "u7", "u8", "u9", "u10", "o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8",
         "o9", "o10", "frange"),
    ),
    FilterSpec(
        "fa_divgrowth",
        "Dividend Growth",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "1ypos", "1yo5", "1yo10", "1yo15", "1yo20", "1yo25", "1yo30", "3ypos", "3yo5",
         "3yo10", "3yo15", "3yo20", "3yo25", "3yo30", "5ypos", "5yo5", "5yo10",
         "5yo15", "5yo20", "5yo25", "5yo30", "cy1", "cy2", "cy3", "cy4", "cy5",
         "cy6", "cy7", "cy8", "cy9", "modal"),
    ),
    FilterSpec(
        "fa_epsyoy",
        "EPS Growth\nThis Year",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_epsyoy1",
        "EPS Growth\nNext Year",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_epsqoq",
        "EPS Growth\nQtr Over Qtr",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_epsyoyttm",
        "EPS Growth TTM",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_eps3years",
        "EPS Growth\nPast 3 Years",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_eps5years",
        "EPS Growth\nPast 5 Years",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_estltgrowth",
        "EPS Growth\nNext 5 Years",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_salesqoq",
        "Sales Growth\nQtr Over Qtr",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_salesyoyttm",
        "Sales Growth TTM",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_sales3years",
        "Sales Growth\nPast 3 Years",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_sales5years",
        "Sales Growth\nPast 5 Years",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "neg", "pos", "poslow", "high", "u5", "u10", "u15", "u20", "u25", "u30", "o5",
         "o10", "o15", "o20", "o25", "o30", "frange"),
    ),
    FilterSpec(
        "fa_epsrev",
        "Earnings & Revenue Surprise",
        "fundamental",
        "modalSelect",
        None,
        ("", "bp", "bm", "bn", "modal"),
    ),
    FilterSpec(
        "fa_roa",
        "Return on Assets",
        "fundamental",
        "floatRangeSelect",
        "roa",
        ("", "pos", "neg", "verypos", "veryneg", "u-50", "u-45", "u-40", "u-35", "u-30",
         "u-25", "u-20", "u-15", "u-10", "u-5", "o5", "o10", "o15", "o20", "o25",
         "o30", "o35", "o40", "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_roe",
        "Return on Equity",
        "fundamental",
        "floatRangeSelect",
        "roe",
        ("", "pos", "neg", "verypos", "veryneg", "u-50", "u-45", "u-40", "u-35", "u-30",
         "u-25", "u-20", "u-15", "u-10", "u-5", "o5", "o10", "o15", "o20", "o25",
         "o30", "o35", "o40", "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_roi",
        "Return on Invested Capital",
        "fundamental",
        "floatRangeSelect",
        "roic",
        ("", "pos", "neg", "verypos", "veryneg", "u-50", "u-45", "u-40", "u-35", "u-30",
         "u-25", "u-20", "u-15", "u-10", "u-5", "o5", "o10", "o15", "o20", "o25",
         "o30", "o35", "o40", "o45", "o50", "frange"),
    ),
    FilterSpec(
        "fa_curratio",
        "Current Ratio",
        "fundamental",
        "floatRangeSelect",
        "current_ratio",
        ("", "high", "low", "u1", "u0.5", "o0.5", "o1", "o1.5", "o2", "o3", "o4", "o5",
         "o10", "frange"),
    ),
    FilterSpec(
        "fa_quickratio",
        "Quick Ratio",
        "fundamental",
        "floatRangeSelect",
        "quick_ratio",
        ("", "high", "low", "u1", "u0.5", "o0.5", "o1", "o1.5", "o2", "o3", "o4", "o5",
         "o10", "frange"),
    ),
    FilterSpec(
        "fa_ltdebteq",
        "LT Debt/Equity",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "high", "low", "u1", "u0.9", "u0.8", "u0.7", "u0.6", "u0.5", "u0.4", "u0.3",
         "u0.2", "u0.1", "o0.1", "o0.2", "o0.3", "o0.4", "o0.5", "o0.6", "o0.7",
         "o0.8", "o0.9", "o1", "frange"),
    ),
    FilterSpec(
        "fa_debteq",
        "Debt/Equity",
        "fundamental",
        "floatRangeSelect",
        "debt_to_equity",
        ("", "high", "low", "u1", "u0.9", "u0.8", "u0.7", "u0.6", "u0.5", "u0.4", "u0.3",
         "u0.2", "u0.1", "o0.1", "o0.2", "o0.3", "o0.4", "o0.5", "o0.6", "o0.7",
         "o0.8", "o0.9", "o1", "frange"),
    ),
    FilterSpec(
        "fa_grossmargin",
        "Gross Margin",
        "fundamental",
        "floatRangeSelect",
        "gross_margin",
        ("", "pos", "neg", "high", "u90", "u80", "u70", "u60", "u50", "u45", "u40", "u35",
         "u30", "u25", "u20", "u15", "u10", "u5", "u0", "u-10", "u-20", "u-30",
         "u-50", "u-70", "u-100", "o0", "o5", "o10", "o15", "o20", "o25", "o30",
         "o35", "o40", "o45", "o50", "o60", "o70", "o80", "o90", "frange"),
    ),
    FilterSpec(
        "fa_opermargin",
        "Operating Margin",
        "fundamental",
        "floatRangeSelect",
        "oper_margin",
        ("", "pos", "neg", "veryneg", "high", "u90", "u80", "u70", "u60", "u50", "u45",
         "u40", "u35", "u30", "u25", "u20", "u15", "u10", "u5", "u0", "u-10",
         "u-20", "u-30", "u-50", "u-70", "u-100", "o0", "o5", "o10", "o15", "o20",
         "o25", "o30", "o35", "o40", "o45", "o50", "o60", "o70", "o80", "o90",
         "frange"),
    ),
    FilterSpec(
        "fa_netmargin",
        "Net Profit Margin",
        "fundamental",
        "floatRangeSelect",
        "profit_margin",
        ("", "pos", "neg", "veryneg", "high", "u90", "u80", "u70", "u60", "u50", "u45",
         "u40", "u35", "u30", "u25", "u20", "u15", "u10", "u5", "u0", "u-10",
         "u-20", "u-30", "u-50", "u-70", "u-100", "o0", "o5", "o10", "o15", "o20",
         "o25", "o30", "o35", "o40", "o45", "o50", "o60", "o70", "o80", "o90",
         "frange"),
    ),
    FilterSpec(
        "fa_payoutratio",
        "Payout Ratio",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "none", "pos", "low", "high", "o0", "o10", "o20", "o30", "o40", "o50", "o60",
         "o70", "o80", "o90", "o100", "u10", "u20", "u30", "u40", "u50", "u60",
         "u70", "u80", "u90", "u100", "frange"),
    ),
    FilterSpec(
        "sh_insiderown",
        "Insider\nOwnership",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "low", "high", "veryhigh", "o10", "o20", "o30", "o40", "o50", "o60", "o70",
         "o80", "o90", "frange"),
    ),
    FilterSpec(
        "sh_insidertrans",
        "Insider\nTransactions",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "veryneg", "neg", "pos", "verypos", "u-90", "u-80", "u-70", "u-60", "u-50",
         "u-45", "u-40", "u-35", "u-30", "u-25", "u-20", "u-15", "u-10", "u-5",
         "o5", "o10", "o15", "o20", "o25", "o30", "o35", "o40", "o45", "o50", "o60",
         "o70", "o80", "o90", "frange"),
    ),
    FilterSpec(
        "sh_instown",
        "Institutional\nOwnership",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "low", "high", "u90", "u80", "u70", "u60", "u50", "u40", "u30", "u20", "u10",
         "o10", "o20", "o30", "o40", "o50", "o60", "o70", "o80", "o90", "frange"),
    ),
    FilterSpec(
        "sh_insttrans",
        "Institutional\nTransactions",
        "fundamental",
        "floatRangeSelect",
        None,
        ("", "veryneg", "neg", "pos", "verypos", "u-50", "u-45", "u-40", "u-35", "u-30",
         "u-25", "u-20", "u-15", "u-10", "u-5", "o5", "o10", "o15", "o20", "o25",
         "o30", "o35", "o40", "o45", "o50", "frange"),
    ),
    FilterSpec(
        "ta_perf",
        "Performance",
        "technical",
        "select",
        None,
        ("", "modal_intraday", "dup", "ddown", "d15u", "d10u", "d5u", "d5o", "d10o", "d15o",
         "1w30u", "1w20u", "1w10u", "1wdown", "1wup", "1w10o", "1w20o", "1w30o",
         "4w50u", "4w30u", "4w20u", "4w10u", "4wdown", "4wup", "4w10o", "4w20o",
         "4w30o", "4w50o", "13w50u", "13w30u", "13w20u", "13w10u", "13wdown",
         "13wup", "13w10o", "13w20o", "13w30o", "13w50o", "26w75u", "26w50u",
         "26w30u", "26w20u", "26w10u", "26wdown", "26wup", "26w10o", "26w20o",
         "26w30o", "26w50o", "26w100o", "ytd75u", "ytd50u", "ytd30u", "ytd20u",
         "ytd10u", "ytd5u", "ytddown", "ytdup", "ytd5o", "ytd10o", "ytd20o",
         "ytd30o", "ytd50o", "ytd100o", "52w75u", "52w50u", "52w30u", "52w20u",
         "52w10u", "52wdown", "52wup", "52w10o", "52w20o", "52w30o", "52w50o",
         "52w100o", "52w200o", "52w300o", "52w500o", "3y90u", "3y75u", "3y50u",
         "3y30u", "3y20u", "3y10u", "3ydown", "3yup", "3y10o", "3y20o", "3y30o",
         "3y50o", "3y100o", "3y200o", "3y300o", "3y500o", "3y1000o", "5y90u",
         "5y75u", "5y50u", "5y30u", "5y20u", "5y10u", "5ydown", "5yup", "5y10o",
         "5y20o", "5y30o", "5y50o", "5y100o", "5y200o", "5y300o", "5y500o",
         "5y1000o", "10y90u", "10y75u", "10y50u", "10y30u", "10y20u", "10y10u",
         "10ydown", "10yup", "10y10o", "10y20o", "10y30o", "10y50o", "10y100o",
         "10y200o", "10y300o", "10y500o", "10y1000o", "modal"),
    ),
    FilterSpec(
        "ta_perf2",
        "Performance 2",
        "technical",
        "select",
        None,
        ("", "modal_intraday", "dup", "ddown", "d15u", "d10u", "d5u", "d5o", "d10o", "d15o",
         "1w30u", "1w20u", "1w10u", "1wdown", "1wup", "1w10o", "1w20o", "1w30o",
         "4w50u", "4w30u", "4w20u", "4w10u", "4wdown", "4wup", "4w10o", "4w20o",
         "4w30o", "4w50o", "13w50u", "13w30u", "13w20u", "13w10u", "13wdown",
         "13wup", "13w10o", "13w20o", "13w30o", "13w50o", "26w75u", "26w50u",
         "26w30u", "26w20u", "26w10u", "26wdown", "26wup", "26w10o", "26w20o",
         "26w30o", "26w50o", "26w100o", "ytd75u", "ytd50u", "ytd30u", "ytd20u",
         "ytd10u", "ytd5u", "ytddown", "ytdup", "ytd5o", "ytd10o", "ytd20o",
         "ytd30o", "ytd50o", "ytd100o", "52w75u", "52w50u", "52w30u", "52w20u",
         "52w10u", "52wdown", "52wup", "52w10o", "52w20o", "52w30o", "52w50o",
         "52w100o", "52w200o", "52w300o", "52w500o", "3y90u", "3y75u", "3y50u",
         "3y30u", "3y20u", "3y10u", "3ydown", "3yup", "3y10o", "3y20o", "3y30o",
         "3y50o", "3y100o", "3y200o", "3y300o", "3y500o", "3y1000o", "5y90u",
         "5y75u", "5y50u", "5y30u", "5y20u", "5y10u", "5ydown", "5yup", "5y10o",
         "5y20o", "5y30o", "5y50o", "5y100o", "5y200o", "5y300o", "5y500o",
         "5y1000o", "10y90u", "10y75u", "10y50u", "10y30u", "10y20u", "10y10u",
         "10ydown", "10yup", "10y10o", "10y20o", "10y30o", "10y50o", "10y100o",
         "10y200o", "10y300o", "10y500o", "10y1000o", "modal"),
    ),
    FilterSpec(
        "ta_volatility",
        "Volatility",
        "technical",
        "select",
        None,
        ("", "wo2", "wo3", "wo4", "wo5", "wo6", "wo7", "wo8", "wo9", "wo10", "wo12", "wo15",
         "mo2", "mo3", "mo4", "mo5", "mo6", "mo7", "mo8", "mo9", "mo10", "mo12",
         "mo15", "modal"),
    ),
    FilterSpec(
        "ta_rsi",
        "RSI (14)",
        "technical",
        "floatRangeSelect",
        "rsi_14",
        ("", "ob90", "ob80", "ob70", "ob60", "os40", "os30", "os20", "os10", "nob60",
         "nob50", "nos50", "nos40", "frange"),
    ),
    FilterSpec(
        "ta_gap",
        "Gap",
        "technical",
        "floatRangeSelect",
        "gap_pct",
        ("", "u", "u0", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10", "u15",
         "u20", "d", "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9",
         "d10", "d15", "d20", "frange"),
    ),
    FilterSpec(
        "ta_sma20",
        "20-Day Simple Moving Average",
        "technical",
        "select",
        "sma_20",
        ("", "pb", "pb10", "pb20", "pb30", "pb40", "pb50", "pa", "pa10", "pa20", "pa30",
         "pa40", "pa50", "pc", "pca", "pcb", "cross50", "cross50a", "cross50b",
         "cross200", "cross200a", "cross200b", "sa50", "sb50", "sa200", "sb200",
         "modal"),
    ),
    FilterSpec(
        "ta_sma50",
        "50-Day Simple Moving Average",
        "technical",
        "select",
        "sma_50",
        ("", "pb", "pb10", "pb20", "pb30", "pb40", "pb50", "pa", "pa10", "pa20", "pa30",
         "pa40", "pa50", "pc", "pca", "pcb", "cross20", "cross20a", "cross20b",
         "cross200", "cross200a", "cross200b", "sa20", "sb20", "sa200", "sb200",
         "modal"),
    ),
    FilterSpec(
        "ta_sma200",
        "200-Day Simple Moving Average",
        "technical",
        "select",
        "sma_200",
        ("", "pb", "pb10", "pb20", "pb30", "pb40", "pb50", "pb60", "pb70", "pb80", "pb90",
         "pa", "pa10", "pa20", "pa30", "pa40", "pa50", "pa60", "pa70", "pa80",
         "pa90", "pa100", "pc", "pca", "pcb", "cross20", "cross20a", "cross20b",
         "cross50", "cross50a", "cross50b", "sa20", "sb20", "sa50", "sb50", "modal"),
    ),
    FilterSpec(
        "ta_change",
        "Change",
        "technical",
        "floatRangeSelect",
        "change_pct",
        ("", "u", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10", "u15", "u20",
         "d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10", "d15",
         "d20", "frange"),
    ),
    FilterSpec(
        "ta_changeopen",
        "Change from Open",
        "technical",
        "floatRangeSelect",
        None,
        ("", "u", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10", "u15", "u20",
         "d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10", "d15",
         "d20", "frange"),
    ),
    FilterSpec(
        "ta_highlow20d",
        "20-Day High/Low",
        "technical",
        "select",
        None,
        ("", "nh", "nl", "b5h", "b10h", "b15h", "b20h", "b30h", "b40h", "b50h", "b0to3h",
         "b0to5h", "b0to10h", "a5h", "a10h", "a15h", "a20h", "a30h", "a40h", "a50h",
         "a0to3h", "a0to5h", "a0to10h", "modal"),
    ),
    FilterSpec(
        "ta_highlow50d",
        "50-Day High/Low",
        "technical",
        "select",
        None,
        ("", "nh", "nl", "b5h", "b10h", "b15h", "b20h", "b30h", "b40h", "b50h", "b0to3h",
         "b0to5h", "b0to10h", "a5h", "a10h", "a15h", "a20h", "a30h", "a40h", "a50h",
         "a0to3h", "a0to5h", "a0to10h", "modal"),
    ),
    FilterSpec(
        "ta_highlow52w",
        "52-Week High/Low",
        "technical",
        "select",
        None,
        ("", "nh", "nl", "b5h", "b10h", "b15h", "b20h", "b30h", "b40h", "b50h", "b60h",
         "b70h", "b80h", "b90h", "b0to3h", "b0to5h", "b0to10h", "a5h", "a10h",
         "a15h", "a20h", "a30h", "a40h", "a50h", "a60h", "a70h", "a80h", "a90h",
         "a100h", "a120h", "a150h", "a200h", "a300h", "a500h", "a0to3h", "a0to5h",
         "a0to10h", "modal"),
    ),
    FilterSpec(
        "ta_alltime",
        "All-Time High/Low",
        "technical",
        "select",
        None,
        ("", "nh", "nl", "b5h", "b10h", "b15h", "b20h", "b30h", "b40h", "b50h", "b60h",
         "b70h", "b80h", "b90h", "b0to3h", "b0to5h", "b0to10h", "a5h", "a10h",
         "a15h", "a20h", "a30h", "a40h", "a50h", "a60h", "a70h", "a80h", "a90h",
         "a100h", "a120h", "a150h", "a200h", "a300h", "a500h", "a0to3h", "a0to5h",
         "a0to10h", "modal"),
    ),
    FilterSpec(
        "ta_pattern",
        "Pattern",
        "technical",
        "modalSelect",
        None,
        ("", "horizontal", "horizontal2", "tlresistance", "tlresistance2", "tlsupport",
         "tlsupport2", "wedgeup", "wedgeup2", "wedgedown", "wedgedown2",
         "wedgeresistance", "wedgeresistance2", "wedgesupport", "wedgesupport2",
         "wedge", "wedge2", "channelup", "channelup2", "channeldown",
         "channeldown2", "channel", "channel2", "doubletop", "doublebottom",
         "multipletop", "multiplebottom", "headandshoulders", "headandshouldersinv",
         "modal"),
    ),
    FilterSpec(
        "ta_candlestick",
        "Candlestick",
        "technical",
        "modalSelect",
        None,
        ("", "lls", "lus", "h", "ih", "stw", "stb", "d", "dd", "gd", "mw", "mb", "modal"),
    ),
    FilterSpec(
        "ta_beta",
        "Beta",
        "technical",
        "floatRangeSelect",
        "beta_1y",
        ("", "u0", "u0.5", "u1", "u1.5", "u2", "o0", "o0.5", "o1", "o1.5", "o2", "o2.5",
         "o3", "o4", "0to0.5", "0to1", "0.5to1", "0.5to1.5", "1to1.5", "1to2",
         "frange"),
    ),
    FilterSpec(
        "ta_averagetruerange",
        "Average True Range",
        "technical",
        "floatRangeSelect",
        "atr_14",
        ("", "o0.25", "o0.5", "o0.75", "o1", "o1.5", "o2", "o2.5", "o3", "o3.5", "o4",
         "o4.5", "o5", "u0.25", "u0.5", "u0.75", "u1", "u1.5", "u2", "u2.5", "u3",
         "u3.5", "u4", "u4.5", "u5", "frange"),
    ),
    FilterSpec(
        "ah_close",
        "After-Hours Close",
        "technical",
        "floatRangeSelect",
        None,
        ("", "u1", "u2", "u3", "u4", "u5", "u7", "u10", "u15", "u20", "u30", "u40", "u50",
         "o1", "o2", "o3", "o4", "o5", "o7", "o10", "o15", "o20", "o30", "o40",
         "o50", "o60", "o70", "o80", "o90", "o100", "1to5", "1to10", "1to20",
         "5to10", "5to20", "5to50", "10to20", "10to50", "20to50", "50to100",
         "frange"),
    ),
    FilterSpec(
        "ah_change",
        "After-Hours Change",
        "technical",
        "floatRangeSelect",
        None,
        ("", "u", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10", "u15", "u20",
         "d", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10", "d15",
         "d20", "frange"),
    ),
    FilterSpec(
        "news_date",
        "Latest News",
        "news",
        "modalSelect",
        None,
        ("", "today", "todayafter", "sinceyesterday", "sinceyesterdayafter", "yesterday",
         "yesterdayafter", "prevminutes5", "prevminutes30", "prevhours1",
         "prevhours24", "prevdays7", "prevmonth", "modal"),
    ),
    FilterSpec(
        "news_keywords",
        "News Keywords",
        "news",
        "tickersInput",
        None,
        ("",),
    ),
    FilterSpec(
        "etf_heldby",
        "Held By",
        "etf",
        "tickersInput",
        None,
        ("",),
    ),
)

FILTER_CATALOG: Mapping[str, FilterSpec] = MappingProxyType(
    {spec.key: spec for spec in _CATALOG_SPECS}
)
FILTER_KEYS: tuple[str, ...] = tuple(FILTER_CATALOG)

__all__ = [
    "FIELDS",
    "FILTER_CATALOG",
    "FILTER_KEYS",
    "FILTER_TABS",
    "Filter",
    "FilterSpec",
    "NUMERIC_OPS",
    "OPERATORS",
    "TEXT_OPS",
    "UI_TYPES",
    "apply_filters",
    "compile_filter",
    "parse_filters",
]
