"""Every one of the 82 Finviz filter keys: a row field, or a refusal by name.

:mod:`app.screen.filters` answers "does Ztockz accept the same filter keys as
Finviz" and leaves ``FilterSpec.field`` as ``None`` for the keys no screen row
can answer. This module answers the next question, and it is the one a selection
endpoint cannot dodge: **what happens when a caller sends a key that has no
column?**

The answer is a refusal that names the key, the Finviz label and the metric that
is missing. Silence is not an option. A selection that accepts ``fa_pe`` and
drops it returns the unfiltered screen, and the caller reads that as "every
stock has a P/E below 10" — a fabricated answer produced by an ignored
parameter. So the contract is: **a filter key either filters a row — directly,
or through one of its option tokens — or the request is rejected (422) with the
reason**, never applied as a no-op.

Forty-eight keys can filter a row directly, three more through their option
vocabulary and twenty-one through a named bucket — **seventy-two live** — while
ten are refused:

* **Seven light columns** — exchange, sector, country, market cap, price,
  change %, current volume — have been on the browse row from the start;
* **twenty-nine parity metrics** joined them when the bulk scan started copying
  the provider's P/E, beta, RSI, SMA and ratio columns
  (:data:`app.screen.universe.PARITY_FIELDS`). They are real columns on a real
  row: a null cell means the provider did not report that metric for that
  company, and a filter on it simply does not match (unknown is not zero). The
  last four were added only after a live probe showed the provider really fills
  them: ``industry`` (a label such as ``Semiconductors``), ``dividends_yield``
  (percent: ``0.47`` = 0.47%), ``gap`` (signed percent) and ``ATR`` (price
  units);
* **three derived columns** answer the keys whose provider cell is on another
  scale or in another shape (:data:`app.screen.universe.DERIVED_FIELDS`, owned by
  :mod:`app.screen.derived`): ``an_recom`` → ``recom_score`` (Finviz's 1..5
  score, ``3 - 2 * recommendation``), ``ta_changeopen`` → ``change_from_open``
  (percent from the session open, derived from close/change/gap) and
  ``ta_highlow52w`` → ``pct_from_52w_high`` (percent below the 52-week high, one
  direction only). Each is an identity over cells the row already carries, so it
  is a column like any other: a row whose inputs the provider did not send holds
  ``None`` and does not match, exactly like a null P/E;
* **five fundamentals columns** answer the keys the scan answers ``null`` for on
  every row (:mod:`app.screen.fundamentals`). Three are copied from
  ``fundamentals_snapshots`` by symbol — ``fa_fpe`` → ``pe_forward``, ``fa_ps``
  → ``ps`` and ``fa_payoutratio`` → ``payout_ratio`` — and only while the row is
  fresh; two are computed from the same snapshot cells, because the store keeps
  the inputs of a ratio rather than the ratio: ``fa_pc`` → ``price_to_cash``
  (``market_cap / cash_and_equivalents``) and ``fa_evsales`` → ``ev_to_sales``
  (``(market_cap + total_debt - cash_and_equivalents) / revenue_ttm``). A symbol
  the store does not hold, or whose inputs the snapshot did not fill, keeps
  ``None`` and does not match. That is a real observation from the table that
  owns the metric, or an identity over four of its cells — never a computed
  placeholder;
* **three facts columns** answer the keys whose metric the bulk scan cannot
  supply at all (:mod:`app.screen.facts`): ``sh_short`` → ``short_float`` (the
  scan's column answers ``null`` for every row), ``sh_insiderown`` →
  ``insider_own`` and ``sh_instown`` → ``inst_own`` (the scan has no ownership
  column). They
  are percentage points read from the ``screen_facts`` table (Finviz's stock
  page: ``Short Float 0.83%``, ``Insider Own 0.12%``, ``Inst Own 68.93%``), and
  the join copies them only while the row is fresh. A symbol the store does not
  hold keeps ``None``, which matches no filter;
* **three choice keys** — ``ta_perf``, ``ta_perf2`` and ``ta_volatility`` —
  filter through Finviz's option vocabulary instead of one column. The request
  is still ``{key, op: "eq", value: <option>}``, and
  :mod:`app.screen.choice_filters` compiles the token into one predicate over
  the column that window owns (``ta_perf: 1w10u`` → ``ret_1w <= -10``). No
  single column could name ten performance windows, so ``Binding.field`` stays
  ``None`` and ``Binding.choice`` is ``True``: the key is available, it just
  answers per token;
* **twenty-one answer a bucket family instead of one column** — the fundamental
  multiples, growth rates, margins and ownership cells, and the technical
  oscillators, moving averages and range controls. Their cells are depth-store
  columns published under their catalogue names
  (:data:`app.screen.universe.DEPTH_FIELDS`), and their Finviz tokens are named
  server-side predicates (:mod:`app.screen.buckets`): ``{"key": "fa_pe", "op":
  "eq", "value": "pe:u15"}``. A key may be **both** mapped and bucketed
  (``fa_ltdebteq`` answers a number *and* ``ltdebteq:o0.5``), because a token and
  a number are the same question asked two ways;
* **ten are refused**, each with the metric it needs: no column measures it
  (theme, sub-theme, a print count, a withdrawn insider flow, a 20-day window, a
  pattern engine, the after-hours session, an article corpus, fund holdings),
  where a filter could only ever return an empty screen. Nine of them have no
  column at all; the tenth,
  ``sh_insidertrans``, owns a 32-token bucket family in which **every** token is
  refused (:data:`app.screen.buckets.INSIDER_TRANSACTIONS_REASON`), so the key
  can answer no value either. Its sentence is not copied into
  :data:`UNAVAILABLE` — it is read from the family
  (:data:`BUCKET_REFUSALS`), and a request naming one of its tokens is answered
  by the registry's own code (``unavailable_bucket``) rather than by a
  paraphrase of it.

Three things this module refuses to invent:

* **No placeholder field.** A key without a column has no entry in
  :data:`ROW_FIELDS`; :func:`binding` raises instead of answering with a column
  that is always null.
* **No arithmetic here.** A derived column is named by
  :data:`app.screen.universe.DERIVED_FIELDS` and computed by
  :mod:`app.screen.derived`; the two fundamentals ratios
  (``price_to_cash``/``ev_to_sales``) are computed by
  :mod:`app.screen.fundamentals` from the snapshot's own cells. This module
  resolves a key to a column and never grows a formula of its own, so "which
  column" and "what number" stay one decision each.
* **No option-vocabulary translation outside the choice keys.** Finviz's
  ``mega``, ``u5`` and ``ob70`` are *display* tokens for range buckets. Turning
  ``cap: mega`` into ``market_cap_usd >= 200e9`` is a metric decision that
  belongs with the metric, not with a key table, so a caller sends an explicit
  operator and a number (``{"key": "cap", "op": "gte", "value": 200000000000}``)
  and this module only resolves the key to its column. The same holds for
  ``an_recom``: its probe vocabulary is ``strongbuy``/``hold``/…, and the column
  takes Finviz's 1..5 number (``{"key": "an_recom", "op": "lt", "value": 2}``).
  The three choice keys are the documented exception, and only because their
  tokens need no new metric: each token names a window the row already has and a
  direction the row's own column can be compared against, so
  :mod:`app.screen.choice_filters` states that mapping once, with a column, an
  operator and a number per token.

* **one is a bucket and no column at all** — ``idx``. Its four options are
  membership tests, not a metric a scan carries, so it filters through
  :mod:`app.screen.buckets` and :mod:`app.screen.index_membership` publishes the
  lists. ``Binding.field`` stays ``None`` and ``Binding.bucket`` is ``True``; a
  request still reads ``{"key": "idx", "op": "eq", "value": "sp500"}``. The
  bucket flag also rides on every *mapped* key that accepts its Finviz tokens
  (``sh_price`` + ``price:u5``), because a token and a number are the same key
  asked two ways.

The mapping is not a second source of truth: :data:`ROW_FIELDS`,
:data:`CHOICE_FIELDS` and :data:`BUCKET_KEYS` are *derived* from
``FILTER_CATALOG[*].field``/``[*].choice`` plus :mod:`app.screen.choice_filters`
and :mod:`app.screen.buckets`, and :func:`_validate` fails the process if the
three ever disagree or if any of the 82 keys is left without a disposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.screen import buckets, choice_filters
from app.screen.filters import FIELDS, FILTER_CATALOG

#: Finviz key → canonical row column, for every key the catalogue gives a field.
#: Derived from ``FILTER_CATALOG`` so the two can never drift: one table says
#: which column a key filters, and this module publishes it under the shape the
#: selection endpoint consumes. Panel order is preserved.
ROW_FIELDS: Mapping[str, str] = MappingProxyType(
    {key: spec.field for key, spec in FILTER_CATALOG.items() if spec.field is not None}
)

#: Finviz key → the columns its options read, for the three keys that filter
#: through their vocabulary (``ta_perf``/``ta_perf2``/``ta_volatility``). Derived
#: from :mod:`app.screen.choice_filters` in panel order: a key with ten windows
#: cannot name one column, so it publishes the set instead of a lie.
CHOICE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        key: choice_filters.fields_for(key)
        for key in FILTER_CATALOG
        if key in choice_filters.CHOICES
    }
)

#: The Finviz keys that also accept one of their bucket tokens
#: (``{"key": "sh_price", "op": "eq", "value": "price:u5"}``). Derived from
#: :mod:`app.screen.buckets`, so the vocabulary and the flag that publishes it
#: cannot drift; ``idx`` is the one key that is **only** a bucket (no column).
#: :data:`app.screen.buckets.DYNAMIC_KEYS` are included: ``news_keywords`` and
#: ``etf_heldby`` take a token this registry does not enumerate (a keyword, a
#: fund), but the request shape is the same one and their resolver is the same
#: :func:`app.screen.buckets.bucket`.
BUCKET_KEYS: frozenset[str] = frozenset(buckets.KEYS) | frozenset(buckets.DYNAMIC_KEYS)

#: Why a key cannot filter yet: one sentence per group of keys, naming the metric
#: the deployment cannot compare — so a 422 can say *what* is absent instead of
#: "invalid". Three of these sentences are the **partial** shape rather than the
#: missing one: ``ipodate``, ``sh_opt`` and ``ta_pattern`` name a cell the depth
#: store really fills and say why it cannot answer the control (a text spelling,
#: a vendor string, a candlestick flag where a chart pattern is asked for). A
#: reason that claims a missing source the data actually has is the failure mode
#: this table was audited for on 2026-11-01, so it is measured against the store
#: in ``tests/test_screen_groups_adversarial.py``.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "needs theme/sub-theme classification: no layer carries a theme column — "
        "not the scan, not the depth store's 106 cells, not the snapshot, not "
        "SQLite — so the source panel's 40 theme and 268 sub-theme tokens name a "
        "taxonomy "
        "nothing in this deployment speaks",
        ("theme", "subtheme"),
    ),
    (
        "no trade-count feed; Finra publishes short-sale volume, not trade "
        "counts — the scan carries volume in shares and turnover in currency, "
        "the depth store has no print count, and deriving a trade count from "
        "volume would be a fabricated number (Polygon.io aggregates, IEX HIST "
        "or Databento would answer it). The control is premium-tier like the "
        "rest of the source panel's paid set; this refusal is about the "
        "missing feed, not about the tier",
        ("sh_trades",),
    ),
    (
        "needs a 20-day high/low column: the scan carries the 52-week extremes "
        "and the depth store carries the 50-day ones, so a 50-day figure cannot "
        "be narrowed to 20 days",
        ("ta_highlow20d",),
    ),
    (
        "needs a chart-pattern detector: the depth store's `candlestick_pattern` "
        "flag has three classes (doji/engulfing/hammer) but no price series, and "
        "the source panel's 28 chart-pattern tokens need daily bars to detect — no "
        "bars "
        "store is ingested",
        ("ta_pattern",),
    ),
)

def _refused_bucket_keys() -> dict[str, str]:
    """Keys whose whole bucket family is refused — the family owns the sentence.

    ``sh_insidertrans`` has a registered vocabulary (32 tokens) and not one of
    them can run, so the key answers no value at all and belongs in the refusal
    list beside the nine groups above. Its sentence is **not** copied here: it
    is read from the family, where the tokens and their reason live
    (:data:`app.screen.buckets.INSIDER_TRANSACTIONS_REASON`), so the schema, the
    document and the 422 cannot drift into three paraphrases.
    """

    refusals: dict[str, str] = {}
    for key in (*buckets.KEYS, *buckets.WITHDRAWN_KEYS):
        family = buckets.for_key(key)
        if not family or any(item.available for item in family):
            continue
        reason = next((item.reason for item in family if item.reason), None)
        if reason:
            refusals[key] = reason
    return refusals


#: Key → reason for a key refused because its whole vocabulary is.
BUCKET_REFUSALS: Mapping[str, str] = MappingProxyType(_refused_bucket_keys())

#: Key → reason, for every key that can filter nothing: one sentence per group of
#: keys, plus the key whose whole bucket family is refused. The last is **merged
#: from** :data:`BUCKET_REFUSALS`, never retyped — the family owns that sentence,
#: and ``_validate`` fails the import if the two ever disagree.
UNAVAILABLE: Mapping[str, str] = MappingProxyType(
    {**{key: reason for reason, keys in _GROUPS for key in keys}, **BUCKET_REFUSALS}
)


class FilterKeyError(ValueError):
    """A filter key that cannot be applied, carrying a stable machine code."""

    code = "invalid_filter_key"

    def __init__(self, key: object, message: str, *, label: str | None = None) -> None:
        super().__init__(message)
        self.key = key
        self.label = label


class UnknownFilterKey(FilterKeyError):
    """The key is not one of the 82 Finviz keys (a typo or an invention)."""

    code = "unknown_filter_key"


class UnavailableFilterKey(FilterKeyError):
    """The key exists in Finviz, but no screen row column can filter on it."""

    code = "unavailable_filter_key"


class ChoiceFilterKey(FilterKeyError):
    """The key filters through its options, so it has no single column."""

    code = "choice_filter_key"


@dataclass(frozen=True, slots=True)
class Binding:
    """One catalogue key and where it points.

    ``field``/``kind`` are ``None`` for the keys no row can answer; ``reason`` is
    then the sentence a 422 repeats. A binding never carries both a field and a
    reason — that would be a key that is somehow available and unavailable at
    once. A **choice** binding is the third shape: ``field`` is ``None`` because
    the key spans several columns, ``choice`` is ``True``, ``reason`` is ``None``
    and one option token compiles to one column predicate
    (:mod:`app.screen.choice_filters`). A **bucket** binding is the fourth: the
    key accepts a token that names a server-side predicate
    (:mod:`app.screen.buckets`), which may read one column (``price:u5``), two
    (``targetprice:a50``) or a date against the market's clock (``earn:today``).
    A key can be both mapped and bucket-driven: ``sh_price`` still compares
    ``price`` for ``{"op": "lt", "value": 5}``. The fifth shape is the
    **withdrawn** one (``sh_insidertrans``): it owns a registered vocabulary in
    which every token is refused, so no value it can answer exists — it is a
    refused key whose *tokens* carry the refusal.
    """

    key: str
    label: str
    tab: str
    field: str | None
    kind: str | None
    reason: str | None
    choice: bool = False
    bucket: bool = False

    @property
    def is_available(self) -> bool:
        """Can the key answer at least one value?

        A bucket key whose whole family is refused (``sh_insidertrans``) answers
        none, so it is refused exactly like the nine keys with no column: the
        published schema must not advertise a vocabulary for a key that can
        filter nothing. What it still owns is the *token* level story — the
        registry refuses each of its 32 ids by name — and that is
        :mod:`app.screen.selection`'s job, not this flag's.
        """

        if self.reason is not None:
            return False
        return self.field is not None or self.choice or self.bucket

    def as_dict(self) -> dict[str, object]:
        """The published form: what a client reads in the schema.

        ``tab`` and ``kind`` are published because a UI has to group the filters
        the way Finviz does and pick the right input for the value; ``reason``
        appears only for the keys that are refused, exactly when the client needs
        to know why. A choice key publishes ``choice`` instead of ``kind``: its
        value is an option token, and the block carries the grammar, the columns
        and the predicate every token compiles to.
        """

        entry: dict[str, object] = {
            "label": self.label,
            "tab": self.tab,
            "field": self.field,
        }
        if self.kind is not None:
            entry["kind"] = self.kind
        if self.choice:
            entry["choice"] = choice_filters.describe(self.key)
        if self.bucket:
            entry["bucket"] = True
        if self.reason is not None:
            entry["reason"] = self.reason
        return entry


def _make(spec_key: str, field: str | None) -> Binding:
    spec = FILTER_CATALOG[spec_key]
    return Binding(
        key=spec.key,
        label=spec.label,
        tab=spec.tab,
        field=field,
        kind=FIELDS[field] if field is not None else None,
        reason=UNAVAILABLE.get(spec_key),
        choice=spec.choice,
        bucket=spec_key in BUCKET_KEYS,
    )


#: Panel order, exactly ``FILTER_KEYS``: the same sequence the Finviz probe
#: returned, so a UI that renders the catalogue reads one list.
CATALOGUE: tuple[Binding, ...] = tuple(
    _make(key, ROW_FIELDS.get(key)) for key in FILTER_CATALOG
)
BINDINGS: Mapping[str, Binding] = MappingProxyType(
    {binding_.key: binding_ for binding_ in CATALOGUE}
)


def _validate() -> None:
    """Fail at import: coverage, drift and placeholders are boot errors."""

    keys = tuple(FILTER_CATALOG)
    if len(CATALOGUE) != len(keys) or tuple(b.key for b in CATALOGUE) != keys:
        raise RuntimeError(
            "filter_fields must cover the catalogue in panel order; "
            f"got {len(CATALOGUE)} bindings for {len(keys)} keys"
        )

    mapped = {key: spec.field for key, spec in FILTER_CATALOG.items() if spec.field}
    if dict(ROW_FIELDS) != mapped:
        raise RuntimeError(
            "filter_fields.ROW_FIELDS drifted from filters.FILTER_CATALOG: "
            f"declared={sorted(ROW_FIELDS.items())}, catalogue={sorted(mapped.items())}"
        )

    chosen = {key for key, spec in FILTER_CATALOG.items() if spec.choice}
    if set(CHOICE_FIELDS) != chosen:
        raise RuntimeError(
            "filter_fields.CHOICE_FIELDS drifted from the choice keys: "
            f"declared={sorted(CHOICE_FIELDS)}, catalogue={sorted(chosen)}"
        )
    if set(CHOICE_FIELDS) != set(choice_filters.CHOICES):
        raise RuntimeError(
            "filter_fields and app.screen.choice_filters disagree on the "
            f"option-driven keys: {sorted(set(CHOICE_FIELDS) ^ set(choice_filters.CHOICES))}"
        )

    covered = (
        set(ROW_FIELDS)
        | set(CHOICE_FIELDS)
        | set(BUCKET_KEYS)
        | set(BUCKET_REFUSALS)
        | set(UNAVAILABLE)
    )
    missing = [key for key in keys if key not in covered]
    unknown = sorted(covered - set(keys))
    # A bucket key may also be a mapped key (``sh_price`` filters a column *and*
    # accepts ``price:u5``); what no key may be is refused twice, or answered by
    # two different compilers.
    overlap = sorted(
        (set(ROW_FIELDS) & set(UNAVAILABLE))
        | (set(CHOICE_FIELDS) & set(UNAVAILABLE))
        | (set(BUCKET_KEYS) & set(UNAVAILABLE))
        | (set(ROW_FIELDS) & set(CHOICE_FIELDS))
    )
    if missing or unknown or overlap:
        raise RuntimeError(
            "every filter key needs exactly one disposition: "
            f"missing={missing}, unknown={unknown}, both={overlap}"
        )
    # A key refused because its whole vocabulary is refused is still one
    # disposition, not two: its sentence is merged into UNAVAILABLE from the
    # family (one owner), so the two must be the same string — not a paraphrase,
    # and not a second table a later edit can forget. The reverse is a real bug:
    # a key that can still filter a column is not unavailable because one family
    # of tokens ran out.
    contradicted = sorted(set(BUCKET_REFUSALS) & (set(ROW_FIELDS) | set(CHOICE_FIELDS)))
    if contradicted:
        raise RuntimeError(
            "a key that filters a column or an option vocabulary cannot be "
            f"refused for a refused bucket family: {contradicted}"
        )
    drifted = sorted(
        key for key, reason in BUCKET_REFUSALS.items() if UNAVAILABLE.get(key) != reason
    )
    if drifted:
        raise RuntimeError(
            "the refusal list and the bucket family disagree on the sentence: "
            f"{drifted}"
        )
    if len(keys) != 83:
        raise RuntimeError(f"the filter catalogue is 83 keys, got {len(keys)}")
    for key, field in ROW_FIELDS.items():
        if field not in FIELDS:
            raise RuntimeError(f"filter key {key!r} names non-existent row field {field!r}")
    for key, columns in CHOICE_FIELDS.items():
        if not columns:
            raise RuntimeError(f"choice key {key!r} reads no row column")
        for column in columns:
            if column not in FIELDS:
                raise RuntimeError(
                    f"choice key {key!r} names non-existent row field {column!r}"
                )
    for key, reason in UNAVAILABLE.items():
        if not reason.strip():
            raise RuntimeError(f"filter key {key!r} has an empty unavailability reason")
    for key in BUCKET_KEYS:
        if key not in keys:
            raise RuntimeError(f"bucket key {key!r} is not one of the 82 filter keys")
        if key in buckets.DYNAMIC_KEYS:
            # A dynamic control has no enumerable vocabulary: `bucket()` asks its
            # resolver, which validates the token (a keyword's alphabet, a fund's
            # name). What it must still have is a control to resolve under.
            if buckets.control_for(key) is None:  # pragma: no cover - boot gate
                raise RuntimeError(f"dynamic bucket key {key!r} has no control")
            continue
        if not buckets.for_key(key):
            raise RuntimeError(f"bucket key {key!r} has no bucket family")
        field = ROW_FIELDS.get(key)
        if field is not None and field not in FIELDS:
            raise RuntimeError(f"bucket key {key!r} names non-existent row field {field!r}")

    # A gated feature must be **key-only**. The plan is enforced on the key a
    # request names, so a feature that also had a `field` spelling could be asked
    # for by column and skip the gate — one more reason `field` is not a filter
    # vocabulary the panel publishes.
    from app.screen import entitlements

    leaked = sorted(key for key in entitlements.FEATURES if key in ROW_FIELDS)
    if leaked:
        raise RuntimeError(
            "a tier-gated feature must not map to a row field, because the "
            f"`field` spelling would bypass the plan: {leaked}"
        )
    unknown_features = sorted(key for key in entitlements.FEATURES if key not in keys)
    if unknown_features:
        raise RuntimeError(
            f"entitlements gates keys the catalogue does not have: {unknown_features}"
        )

    # The parity table is the other half of the same claim: a field a key filters
    # must be a field a row can carry — a light column, a parity metric, a
    # derived column, or one of the cells a join copies onto the row
    # (``app.screen.fundamentals`` and ``app.screen.facts``). ``CARRIED_FIELDS``
    # is the union the row layer owns; the two join tables are the additions,
    # because those cells come from a second store rather than from the scan.
    from app.screen import facts as facts_store
    from app.screen import fundamentals as joined
    from app.screen import universe as parity

    readable = (
        parity.CARRIED_FIELDS
        | frozenset(joined.FUNDAMENTALS_ROW_FIELDS)
        | frozenset(facts_store.FACTS_ROW_FIELDS)
    )
    unreadable = sorted(
        field for field in mapped.values() if field not in readable
    ) + sorted(
        column for columns in CHOICE_FIELDS.values() for column in columns
        if column not in readable
    )
    if unreadable:
        raise RuntimeError(
            "filter keys point at fields no row carries (neither a light column, "
            "a parity metric, a derived column, a joined fundamental nor a joined "
            f"facts cell): {unreadable}"
        )


_validate()


def unavailable_filter_key(key: object, found: Binding) -> UnavailableFilterKey:
    """The 422 a refused key raises — one sentence, two entry points.

    :func:`binding` raises it for the nine keys with no column;
    :mod:`app.screen.selection` raises it for a withdrawn bucket key whose value
    is not one of its tokens. Two call sites, one sentence: a refusal that says
    one thing in the schema and another over HTTP is a bug a caller cannot
    debug.
    """

    return UnavailableFilterKey(
        key,
        f"filter key {key!r} ({found.label}) cannot be applied: it {found.reason}. "
        "A filter that cannot change the result is refused instead of ignored, "
        "because ignoring it would return the unfiltered screen as if it matched",
        label=found.label,
    )


def binding(key: object) -> Binding:
    """The binding for ``key``; unknown keys and column-less refusals raise.

    The failures are separate types because they mean different things to a
    caller: ``UnknownFilterKey`` is a typo or a key Finviz does not have, while
    ``UnavailableFilterKey`` is a real Finviz filter whose metric no screen row
    carries yet. An option-driven key is **available** and still returns
    ``field=None``: no single column could name it, and :attr:`Binding.choice`
    plus :mod:`app.screen.choice_filters` are where its columns live.

    A **withdrawn bucket key** (``sh_insidertrans``) raises like any other refusal,
    and that is the point: the key can answer no value. Its tokens are buckets,
    so :mod:`app.screen.selection` lets a *token* answer first — the registry's
    ``unavailable_bucket`` — and this function's sentence answers everything else.
    """

    if not isinstance(key, str) or not key:
        raise UnknownFilterKey(key, f"filter key must be a non-empty string, got {key!r}")

    found = BINDINGS.get(key)
    if found is None:
        raise UnknownFilterKey(
            key,
            f"unknown filter key {key!r}: it is not one of the 82 filter keys. "
            f"Keys that can filter a row today: {', '.join(ROW_FIELDS)}; "
            f"option-driven keys: {', '.join(CHOICE_FIELDS)}; "
            f"bucket-driven keys: {', '.join(BUCKET_KEYS)}. "
            "The full catalogue, with the reason each other key is refused, is "
            "served at GET /screens/select/schema",
        )
    if not found.is_available:
        raise unavailable_filter_key(key, found)
    return found


def field_for(key: object) -> str:
    """The row column ``key`` filters; refuses exactly like :func:`binding`.

    An option-driven key has no single column: it raises :class:`ChoiceFilterKey`
    naming the columns to use instead, because answering with one of them would
    silently pick a window the caller never asked for. A **bucket** key has no
    column either — ``idx`` answers a set membership test and a withdrawn one
    answers its own refusal — so neither is guessed at.
    """

    found = binding(key)
    if found.choice:
        raise ChoiceFilterKey(
            key,
            f"filter key {key!r} ({found.label}) filters through its options, so it "
            "has no single row column; it reads "
            f"{', '.join(CHOICE_FIELDS[found.key])}. Send op 'eq' with one of its "
            "option tokens (app.screen.choice_filters)",
            label=found.label,
        )
    if found.field is None:
        if not found.is_available:
            raise unavailable_filter_key(key, found)
        raise ChoiceFilterKey(
            key,
            f"filter key {key!r} ({found.label}) filters through its bucket tokens "
            "(app.screen.buckets), so it has no single row column; send op 'eq' "
            f"with one of {', '.join(item.id for item in buckets.for_key(found.key))}",
            label=found.label,
        )
    return found.field


def is_available(key: object) -> bool:
    """Can a filter on ``key`` run today? (Unknown keys are not available.)"""

    found = BINDINGS.get(key) if isinstance(key, str) else None
    return found is not None and found.is_available


def reason_for(key: str) -> str | None:
    """The refusal sentence for ``key``, or ``None`` when it maps to a column."""

    found = BINDINGS.get(key)
    return found.reason if found is not None else None


def mapped_keys() -> tuple[str, ...]:
    """The keys that filter a row today, in panel order (columns or options)."""

    return tuple(b.key for b in CATALOGUE if b.is_available)


def choice_keys() -> tuple[str, ...]:
    """The keys that filter through their options, in panel order."""

    return tuple(b.key for b in CATALOGUE if b.choice)


def unavailable_keys() -> tuple[str, ...]:
    """The keys that are refused, in panel order."""

    return tuple(b.key for b in CATALOGUE if not b.is_available)


__all__ = [
    "BINDINGS",
    "BUCKET_KEYS",
    "BUCKET_REFUSALS",
    "CATALOGUE",
    "CHOICE_FIELDS",
    "ChoiceFilterKey",
    "FilterKeyError",
    "ROW_FIELDS",
    "UNAVAILABLE",
    "UnavailableFilterKey",
    "UnknownFilterKey",
    "Binding",
    "binding",
    "choice_keys",
    "field_for",
    "is_available",
    "mapped_keys",
    "reason_for",
    "unavailable_filter_key",
    "unavailable_keys",
]
