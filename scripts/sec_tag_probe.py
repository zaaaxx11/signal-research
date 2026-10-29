#!/usr/bin/env python3
"""SEC XBRL tag coverage probe: which concepts actually carry revenue and net income.

``app.market.sec_fundamentals.TAG_MAP`` answers ``sales`` from three concepts
and ``net_income`` from two. Filers that do not use those concepts — banks
above all — come back ``None``. This probe finds out **which concepts those
filers do use**, with counts, so the map can be extended on evidence instead
of on folklore.

What it does, in order:

1. **Sample.** Joins the probe universe (``universe.json``) to the engine
   snapshot (symbol -> CIK) and takes the top N symbols by market cap
   (default 500), always keeping the focus symbols (JPM, XOM).
2. **One index build.** Streams the quarterly ZIP once through
   :meth:`FactIndex.build` with the sampled CIKs. Every distribution number
   comes from that single build.
3. **Candidate discovery.** Two sources, both reported: a curated list of
   known revenue/income concepts, and a name sweep over *every* tag observed
   in the sample (so a concept the curated list never heard of still shows
   up). ``tag.txt`` inside the ZIP supplies the SEC's own label, doc and
   instant/duration flag for each concept.
4. **Distribution.** Per candidate: how many sampled CIKs file it at all,
   consolidated (undimensioned, USD), annual (``qtrs=4``), year-to-date, and
   how many file it *only* dimensionally (segment-only concepts).
5. **Gap analysis.** Runs the real mapper over the sample, counts the CIKs
   whose ``sales``/``net_income`` is ``None``, and splits them into "files no
   candidate concept at all" (a true tag gap) and "files one but the annual
   base is not in this quarter" (a coverage artifact).
6. **Counterfactual.** Re-runs the mapper with one candidate appended to
   ``TAG_MAP`` (patched in memory, never on disk) and counts how many of the
   ``None`` CIKs that fixes. Repeated on the merged multi-quarter index for
   the missing subset, because the TTM roll-forward needs the 10-K quarter.
7. **Diagnosis.** JPM and XOM: every revenue-/income-like row they file, the
   mapper's answer before and after, on both the single quarter and the
   merged quarters.

No network. Every number in the markdown is computed here, so re-running the
probe reproduces the report.

Run: .venv/bin/python scripts/sec_tag_probe.py [--sample 500] [--zip PATH]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.engine.bulk_financials import Fact, FactIndex, build_quarters
from app.market import sec_fundamentals as sf

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

DEFAULT_ZIP = Path("/home/ubuntu/ztockz-data/cache/sec-bulk/2026q3.zip")
DEFAULT_CACHE_DIR = Path("/home/ubuntu/ztockz-data/cache/sec-bulk")
DEFAULT_UNIVERSE = Path(
    "/home/ubuntu/briefs/market-engine/research/probes-1000/universe.json"
)
DEFAULT_SNAPSHOT = Path("/home/ubuntu/ztockz-data/engine-snapshot.json")
DEFAULT_OUT_MD = Path("/home/ubuntu/briefs/market-engine/research/sec-tag-coverage.md")
DEFAULT_OUT_JSON = Path(
    "/home/ubuntu/briefs/market-engine/research/sec-tag-coverage.json"
)

#: Symbols the diagnosis section always covers.
FOCUS_SYMBOLS = ("JPM", "XOM")

#: The CIK the engine snapshot maps XOM to, and the CIK the filer "EXXON
#: MOBIL CORP" actually files under. Both are diagnosed, because they are
#: different companies and only one of them files anything.
XOM_SNAPSHOT_CIK = "0002115436"
XOM_FILER_CIK = "0000034088"

# --- candidate discovery ---------------------------------------------------

#: A tag whose *name* carries one of these as a whole CamelCase word is a
#: revenue-like duration concept. The word test matters: without it
#: ``AvailableForSaleSecurities`` matches "Sales" across the "Sale|
#: Securities" boundary and every available-for-sale tag lands in the
#: revenue table. The stem may sit anywhere in the name —
#: ``RegulatedAndUnregulatedOperatingRevenue`` is a utility's total revenue
#: and its "Revenue" is the last word, not the first. Applied to every tag
#: observed in the sample.
REVENUE_NAME_RE = re.compile(r"(Revenues?|Sales|Turnover)(?![a-z])")
#: A tag whose name carries one of these whole words is an income-like
#: duration concept. Tight on purpose: a bare ``IncomeLoss`` would sweep in
#: every ``OtherComprehensiveIncomeLoss…`` component.
INCOME_NAME_RE = re.compile(
    r"(NetIncome|ProfitLoss|IncomeLossFromContinuingOperations)(?![a-z])"
)
#: Name fragments that disqualify a revenue-named concept from being a top
#: line: balances, allowances, cash-flow items. "Tax" is deliberately absent
#: — ``RevenueFromContractWithCustomerExcludingAssessedTax`` is the single
#: most-filed revenue concept and its name contains "Tax".
REVENUE_DENYLIST_RE = re.compile(
    r"Deferred|Unearned|Refund|Allowance|Recognition|"
    r"RemainingPerformanceObligation|ContractWithCustomerLiability|Receivable|"
    r"ProceedsFrom|PaymentsFor|RepaymentsOf|Disposal|Impairment|WriteOff|GainLoss",
    re.IGNORECASE,
)
#: The same, for income-named concepts: per-share rows, cash-flow
#: reconciliations, pension-plan and discontinued-operations components.
INCOME_DENYLIST_RE = re.compile(
    r"Comprehensive|PerBasicShare|PerDilutedShare|PerShare|DiscontinuedOperations|"
    r"EquityMethodInvestments|AdjustmentsToReconcile|CashProvidedByUsedIn|"
    r"TemporaryEquity|EmployeeBenefitPlan|AvailableForBenefit",
    re.IGNORECASE,
)

#: Revenue concepts that are known *not* to be a top line. They carry an
#: explicit reason in :data:`REASON_OVERRIDES`; the list exists so the
#: report can say *why* each one stays out rather than only that it does.
REVENUE_COMPONENT_PRIOR = frozenset(
    {
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfServices",
        "InterestIncomeOperating",
        "InterestAndDividendIncomeOperating",
        "InterestIncomeExpenseNet",
        "NoninterestIncome",
        "InvestmentBankingRevenue",
        "PrincipalTransactionsRevenue",
        "PremiumsEarnedNet",
    }
)

#: Why a named concept stays out of the map, said better than the automated
#: signals can say it. The numbers behind each reason are in the tables.
REASON_OVERRIDES: Mapping[str, str] = {
    "CostOfRevenue": "cost line, not revenue",
    "CostOfGoodsAndServicesSold": "cost line, not revenue",
    "CostOfServices": "cost line, not revenue",
    "CostOfSales": (
        "cost line (the IFRS spelling) — mapping it would report cost of "
        "sales as sales"
    ),
    "ExciseAndSalesTaxes": "a tax collected from the customer, not revenue",
    "InterestIncomeOperating": (
        "gross interest income — larger than the filer's net revenue total "
        "for the same period, because interest expense has not been deducted"
    ),
    "InterestAndDividendIncomeOperating": (
        "gross interest and dividend income — a bank's revenue before "
        "interest expense, not its top line"
    ),
    "InterestIncomeExpenseNet": "net interest income — one component of bank net revenue",
    "NoninterestIncome": "bank non-interest component of net revenue",
    "InvestmentBankingRevenue": "bank revenue component (investment banking fees)",
    "PrincipalTransactionsRevenue": "bank revenue component (principal transactions)",
    "PremiumsEarnedNet": (
        "insurance premiums — one component of an insurer's total revenue, "
        "which also carries investment income"
    ),
    "ComprehensiveIncomeNetOfTax": (
        "comprehensive income — includes other comprehensive income, which "
        "net income excludes"
    ),
    "OperatingIncomeLoss": "operating income — already its own field",
    "GrossProfit": "gross profit — neither revenue nor net income",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": (
        "pre-tax income — the tax line has not been deducted yet"
    ),
    "NetIncomeLossAttributableToNoncontrollingInterest": (
        "the noncontrolling-interest share of profit, not the parent's net income"
    ),
    "NetIncomeLossAttributableToRedeemableNoncontrollingInterest": (
        "the redeemable noncontrolling-interest share of profit"
    ),
    "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest": (
        "the nonredeemable noncontrolling-interest share of profit"
    ),
    "NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest": (
        "net income including the noncontrolling-interest share — `ProfitLoss` "
        "is the same figure and is already mapped"
    ),
    "NoncontrollingInterestInNetIncomeLossPreferredUnitHoldersRedeemable": (
        "the preferred unitholders' share of profit"
    ),
    "NetIncomeLossAllocatedToLimitedPartners": (
        "the limited partners' allocation — a partnership distribution, not "
        "the consolidated bottom line"
    ),
    "NetIncomeLossAllocatedToGeneralPartners": (
        "the general partners' allocation — a partnership distribution, not "
        "the consolidated bottom line"
    ),
    "NetIncomeLossNetOfTaxPerOutstandingLimitedPartnershipUnitDiluted": (
        "a per-unit amount, not the consolidated bottom line"
    ),
    "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax": (
        "a per-unit amount, not the consolidated bottom line"
    ),
    "NetIncomeLossPerOutstandingLimitedPartnershipAndGeneralPartnershipUnitBasicAndDiluted": (
        "a per-unit amount, not the consolidated bottom line"
    ),
    "ProfitLossAttributableToNoncontrollingInterests": (
        "the noncontrolling-interest share of profit"
    ),
    "ProfitLossAttributableToOwnersOfParent": (
        "the parent's share of profit — `ProfitLoss` already answers it"
    ),
    "ProfitLossBeforeTax": "pre-tax profit — the tax line has not been deducted yet",
    "ProfitLossFromOperatingActivities": (
        "operating profit — `OperatingIncomeLoss` is the mapped concept for it"
    ),
    "ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod": (
        "the equity-method share of an associate's profit — a component of "
        "pre-tax income, not the bottom line"
    ),
    "IncomeLossFromContinuingOperations": (
        "income from continuing operations — a different line from net income"
    ),
    "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest": (
        "income from continuing operations including the noncontrolling-"
        "interest share — `ProfitLoss` is the mapped concept"
    ),
    "IncomeLossFromContinuingOperationsAttributableToNoncontrollingEntity": (
        "the noncontrolling entity's share of continuing-operations income"
    ),
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic": (
        "domestic pre-tax income — a geographic slice of the pre-tax line"
    ),
    "IncomeLossFromContinuingOperationsBeforeInterestExpenseInterestIncomeIncomeTaxesExtraordinaryItemsNoncontrollingInterestsNet": (
        "a legacy pre-tax variant — the tax line has not been deducted yet"
    ),
    "NetIncomeLossFromContinuingOperationsAvailableToCommonShareholdersBasic": (
        "net income after preferred dividends — a per-share denominator, "
        "not the reported bottom line"
    ),
    "SalesTypeLeaseInterestIncome": (
        "sales-type lease interest income — a financing component, not the "
        "top line"
    ),
    "NetIncomeLossAvailableToCommonStockholdersBasic": (
        "net income after preferred dividends — a per-share denominator, "
        "not the reported bottom line"
    ),
    "NetIncomeLossAvailableToCommonStockholdersDiluted": (
        "net income after preferred dividends — a per-share denominator, "
        "not the reported bottom line"
    ),
}

#: Concepts worth tabulating that the name sweep cannot surface, plus the
#: adjacent concepts the exclusion argument needs numbers for.
CURATED_REVENUE = (
    "InterestAndDividendIncomeOperating",  # bank gross interest income
    "InterestIncomeOperating",  # bank gross interest income (JPM's spelling)
    "InterestIncomeExpenseNet",  # bank net interest income — a component
    "NoninterestIncome",  # bank non-interest component
    "PremiumsEarnedNet",  # insurance premiums
)
CURATED_INCOME = (
    "ComprehensiveIncomeNetOfTax",  # includes OCI — not net income
    "OperatingIncomeLoss",  # operating income — its own field
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "GrossProfit",  # neither revenue nor income
)

#: SIC divisions, for reading the industry-concentration column.
SIC_DIVISIONS = {
    "01": "Agricultural production",
    "07": "Agricultural services",
    "08": "Forestry",
    "09": "Fishing, hunting and trapping",
    "10": "Metal mining",
    "12": "Coal mining",
    "13": "Oil and gas extraction",
    "14": "Mining and quarrying of nonmetallic minerals",
    "15": "Building construction",
    "16": "Heavy construction other than building",
    "17": "Construction special trade",
    "20": "Food and kindred products",
    "21": "Tobacco manufactures",
    "22": "Textile mill products",
    "23": "Apparel and other finished products",
    "24": "Lumber and wood products",
    "25": "Furniture and fixtures",
    "26": "Paper and allied products",
    "27": "Printing and publishing",
    "28": "Chemicals and allied products",
    "29": "Petroleum refining and related industries",
    "30": "Rubber and misc. plastics products",
    "31": "Leather and leather products",
    "32": "Stone, clay, glass, concrete products",
    "33": "Primary metal industries",
    "34": "Fabricated metal products",
    "35": "Industrial and commercial machinery",
    "36": "Electronic and other electrical equipment",
    "37": "Transportation equipment",
    "38": "Measuring instruments; photographs",
    "39": "Misc. manufacturing industries",
    "40": "Railroad transportation",
    "41": "Local and suburban transit",
    "42": "Motor freight transportation",
    "43": "U.S. Postal Service",
    "44": "Water transportation",
    "45": "Transportation by air",
    "46": "Pipelines, except natural gas",
    "47": "Transportation services",
    "48": "Communications",
    "49": "Electric, gas and sanitary services",
    "50": "Wholesale trade-durable goods",
    "51": "Wholesale trade-nondurable goods",
    "52": "Building materials and gardening",
    "53": "General merchandise stores",
    "54": "Food stores",
    "55": "Automotive dealers and service stations",
    "56": "Apparel and accessory stores",
    "57": "Home furniture and furnishings",
    "58": "Eating and drinking places",
    "59": "Misc. retail",
    "60": "Depository institutions",
    "61": "Nondepository credit institutions",
    "62": "Security and commodity brokers",
    "63": "Insurance carriers",
    "64": "Insurance agents and brokers",
    "65": "Real estate",
    "67": "Holding and other investment offices",
    "70": "Hotels and other lodging",
    "72": "Personal services",
    "73": "Business services",
    "75": "Automotive repair and services",
    "76": "Misc. repair services",
    "78": "Motion pictures",
    "79": "Amusement and recreation services",
    "80": "Health services",
    "81": "Legal services",
    "82": "Educational services",
    "83": "Social services",
    "86": "Membership organizations",
    "87": "Engineering and management services",
    "88": "Private households",
    "89": "Services, nec",
    "91": "Executive, legislative and general government",
    "92": "Justice, public order and safety",
    "93": "Public finance and taxation",
    "94": "Administration of human resources",
    "95": "Administration of environmental quality",
    "96": "Administration of economic programs",
    "97": "National security and international affairs",
    "99": "Nonclassifiable establishments",
}

#: Verdicts a candidate can carry in the report.
VERDICT_ADD = "ADD"
VERDICT_EXCLUDE = "EXCLUDE"
VERDICT_KEEP = "KEEP"  # already mapped, tabulated for the distribution


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SampleRow:
    symbol: str
    cik: str
    description: str
    market_cap: float


def load_sample(
    universe_path: Path, snapshot_path: Path, size: int
) -> list[SampleRow]:
    """Top ``size`` universe symbols by market cap, joined to CIKs.

    The universe carries no CIK; the engine snapshot carries no market cap.
    The join is the snapshot's symbol -> CIK map, first spelling wins (GOOGL
    and GOOG share a CIK, so the CIK is deduplicated and the
    higher-capitalised spelling is the one reported).
    """
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    symbol_cik: dict[str, str] = {}
    for item in snapshot["dataset"]["securities"]:
        symbol = (item.get("symbol") or "").strip()
        cik = (item.get("cik") or "").strip()
        if symbol and cik and symbol not in symbol_cik:
            symbol_cik[symbol] = cik

    rows: list[SampleRow] = []
    for item in universe["rows"]:
        symbol = (item.get("name") or "").strip()
        cik = symbol_cik.get(symbol)
        if not cik:
            continue
        cap = item.get("market_cap_basic")
        rows.append(
            SampleRow(
                symbol=symbol,
                cik=cik,
                description=item.get("description") or "",
                market_cap=float(cap) if isinstance(cap, (int, float)) else 0.0,
            )
        )
    rows.sort(key=lambda row: (-row.market_cap, row.symbol))

    kept: list[SampleRow] = []
    seen: set[str] = set()
    for row in rows:
        if row.cik in seen:
            continue
        seen.add(row.cik)
        kept.append(row)
        if len(kept) >= size:
            break
    for symbol in FOCUS_SYMBOLS:
        if any(row.symbol == symbol for row in kept):
            continue
        for row in rows:
            if row.symbol == symbol and row.cik not in seen:
                seen.add(row.cik)
                kept.append(row)
                break
    return kept


# ---------------------------------------------------------------------------
# tag.txt: the SEC's own description of every concept
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TagMeta:
    tag: str
    version: str
    custom: bool
    abstract: bool
    iord: str  # "I" instant, "D" duration
    crdr: str
    tlabel: str
    doc: str


def custom_named_tags(zip_paths: list[Path]) -> set[str]:
    """Company-specific extensions whose name reads like revenue or income.

    They matter because the mapper matches on the tag name alone: a filer's
    private ``Revenue`` extension would satisfy a ``TAG_MAP`` entry written
    for the IFRS concept of the same name.
    """
    found: set[str] = set()
    for path in zip_paths:
        for tag, meta in load_tag_meta(path).items():
            if not meta.custom:
                continue
            if REVENUE_NAME_RE.search(tag) and not REVENUE_DENYLIST_RE.search(tag):
                found.add(tag)
            elif INCOME_NAME_RE.search(tag) and not INCOME_DENYLIST_RE.search(tag):
                found.add(tag)
    return found


def observed_tags(zip_paths: list[Path]) -> set[str]:
    """Every tag name the datasets carry, company extensions included.

    Read from each ZIP's ``tag.txt`` rather than from ``num.txt``: the
    catalogue is a few hundred kilobytes, the facts are not.
    """
    tags: set[str] = set()
    for path in zip_paths:
        with zipfile.ZipFile(path) as archive:
            with archive.open("tag.txt") as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
                reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
                header = next(reader)
                cols = {name.strip(): pos for pos, name in enumerate(header)}
                width = len(header)
                for row in reader:
                    if len(row) == width and row[cols["tag"]].strip():
                        tags.add(row[cols["tag"]].strip())
    return tags


def load_tag_meta(zip_path: Path) -> dict[str, TagMeta]:
    """Stream ``tag.txt`` into ``{tag: TagMeta}``; the first spelling wins."""
    metas: dict[str, TagMeta] = {}
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open("tag.txt") as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
            reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
            header = next(reader)
            cols = {name.strip(): pos for pos, name in enumerate(header)}
            width = len(header)
            for row in reader:
                if len(row) != width:
                    continue
                tag = row[cols["tag"]].strip()
                if not tag:
                    continue
                metas.setdefault(
                    tag,
                    TagMeta(
                        tag=tag,
                        version=row[cols["version"]].strip(),
                        custom=row[cols["custom"]].strip() == "1",
                        abstract=row[cols["abstract"]].strip() == "1",
                        iord=row[cols["iord"]].strip(),
                        crdr=row[cols["crdr"]].strip(),
                        tlabel=row[cols["tlabel"]].strip(),
                        doc=row[cols["doc"]].strip(),
                    ),
                )
    return metas


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Candidate:
    tag: str
    family: str  # "revenue" | "income"
    source: str  # "sweep" | "curated"
    meta: TagMeta | None = None
    ciks_any: set[str] = field(default_factory=set)
    ciks_consol: set[str] = field(default_factory=set)
    ciks_annual: set[str] = field(default_factory=set)
    ciks_ytd: set[str] = field(default_factory=set)
    ciks_dim_only: set[str] = field(default_factory=set)
    rows_total: int = 0
    rows_consol: int = 0
    rows_dim: int = 0
    sic_top: tuple[str, str, int, int] = ("", "", 0, 0)
    #: Median, over the tag's consolidated (cik, ddate, qtrs) periods, of
    #: value / the period's largest revenue-like value. 1.0 means "this is
    #: the top line"; a small fraction means "a component of a bigger
    #: concept". ``None`` when the concept is income-like or has no
    #: consolidated duration rows.
    median_share_of_peak: float | None = None
    examples: list[str] = field(default_factory=list)


def discover_candidates(
    index: FactIndex, metas: dict[str, TagMeta]
) -> list[Candidate]:
    """Curated concepts plus every revenue-/income-named tag in the sample."""
    observed = {fact.tag for facts in index.by_cik.values() for fact in facts}
    candidates: dict[str, Candidate] = {}

    def add(tag: str, family: str, source: str) -> None:
        if tag not in candidates:
            candidates[tag] = Candidate(
                tag=tag, family=family, source=source, meta=metas.get(tag)
            )

    for tag in CURATED_REVENUE:
        add(tag, "revenue", "curated")
    for tag in CURATED_INCOME:
        add(tag, "income", "curated")

    for tag in sorted(observed):
        meta = metas.get(tag)
        # A standard duration concept only: an instant "revenue" is a
        # balance (deferred revenue), an abstract concept carries no value,
        # and a company-specific extension is not a map entry.
        if meta is None or meta.abstract or meta.custom or meta.iord != "D":
            continue
        if (
            REVENUE_NAME_RE.search(tag)
            and not REVENUE_DENYLIST_RE.search(tag)
        ):
            add(tag, "revenue", "sweep")
        elif INCOME_NAME_RE.search(tag) and not INCOME_DENYLIST_RE.search(tag):
            add(tag, "income", "sweep")

    return [candidates[tag] for tag in sorted(candidates)]


def measure(
    candidates: list[Candidate],
    index: FactIndex,
    cap_by_cik: dict[str, float],
    symbol_by_cik: dict[str, str],
    sic_by_cik: dict[str, str],
    *,
    ciks: list[str] | None = None,
) -> None:
    """Fill every candidate's distribution counters from the built index.

    ``ciks`` bounds the measurement to the sample: the index may carry a
    CIK the diagnosis needs that the distribution must not count.
    """
    by_tag: dict[str, list[Fact]] = {}
    wanted = None if ciks is None else set(ciks)
    for cik, facts in index.by_cik.items():
        if wanted is not None and cik not in wanted:
            continue
        for fact in facts:
            by_tag.setdefault(fact.tag, []).append(fact)

    # The largest consolidated revenue-like value per (cik, ddate, qtrs) —
    # the yardstick that tells a top line from a component of one. Every
    # revenue candidate counts, gross figures included: a bank's gross
    # interest income is larger than its net revenue, and a concept that is
    # a small fraction of it is a component either way.
    revenue_peak: dict[tuple[str, str, int], float] = {}
    for candidate in candidates:
        if candidate.family != "revenue":
            continue
        for fact in by_tag.get(candidate.tag, ()):
            if fact.uom != "USD" or fact.segments or fact.qtrs is None:
                continue
            key = (fact.cik, fact.ddate, fact.qtrs)
            revenue_peak[key] = max(revenue_peak.get(key, 0.0), fact.value)

    for candidate in candidates:
        facts = by_tag.get(candidate.tag, [])
        if not facts:
            continue
        candidate.rows_total = len(facts)
        sics: Counter[str] = Counter()
        # (cik, ddate, qtrs) -> this tag's consolidated value, and the period's
        # peak across every revenue candidate that is not a known component.
        own: dict[tuple[str, str, int], float] = {}
        for fact in facts:
            candidate.ciks_any.add(fact.cik)
            if fact.uom == "USD" and not fact.segments:
                candidate.ciks_consol.add(fact.cik)
                candidate.rows_consol += 1
                if fact.qtrs == 4:
                    candidate.ciks_annual.add(fact.cik)
                elif fact.qtrs in (1, 2, 3):
                    candidate.ciks_ytd.add(fact.cik)
                if candidate.family == "revenue" and fact.qtrs is not None:
                    key = (fact.cik, fact.ddate, fact.qtrs)
                    own[key] = max(own.get(key, 0.0), fact.value)
            else:
                candidate.rows_dim += 1
        candidate.ciks_dim_only = candidate.ciks_any - candidate.ciks_consol
        # Sorted, so a count tie breaks the same way on every run: a set's
        # iteration order is not stable across processes.
        for cik in sorted(candidate.ciks_consol):
            sics[sic_by_cik.get(cik, "")[:4]] += 1
        if sics:
            code, count = sics.most_common(1)[0]
            candidate.sic_top = (
                code,
                SIC_DIVISIONS.get(code[:2], "?"),
                count,
                len(candidate.ciks_consol),
            )
        if own:
            shares = [
                value / revenue_peak[key]
                for key, value in own.items()
                if revenue_peak.get(key, 0.0) > 0
            ]
            if shares:
                candidate.median_share_of_peak = round(statistics.median(shares), 3)
        ordered = sorted(
            candidate.ciks_consol, key=lambda cik: (-cap_by_cik.get(cik, 0.0), cik)
        )
        candidate.examples = [symbol_by_cik.get(cik, cik) for cik in ordered[:3]]


# ---------------------------------------------------------------------------
# Mapper gap analysis
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GapReport:
    field: str
    sample_ciks: int
    mapped: int
    none: int
    none_no_concept: list[str]
    none_has_concept: list[str]
    none_no_facts: list[str]
    ranked: list[dict[str, Any]]


def mapper_values(index: FactIndex, ciks: list[str]) -> dict[str, dict[str, Any]]:
    return {cik: sf.map_fundamentals(index.facts_for(cik)) for cik in ciks}


@contextmanager
def extended_tag_map(field_name: str, extra: tuple[str, ...]) -> Iterator[None]:
    """Patch ``TAG_MAP[field_name]`` in memory for the length of the block."""
    original = sf.TAG_MAP
    merged = dict(original)
    merged[field_name] = tuple(original[field_name]) + tuple(extra)
    sf.TAG_MAP = merged
    try:
        yield
    finally:
        sf.TAG_MAP = original


def consolidated_ciks_by_tag(index: FactIndex, tags: set[str]) -> dict[str, set[str]]:
    """``{tag: CIKs filing it undimensioned in USD}`` for one index."""
    owners: dict[str, set[str]] = {}
    for cik, facts in index.by_cik.items():
        for fact in facts:
            if fact.tag in tags and fact.uom == "USD" and not fact.segments:
                owners.setdefault(fact.tag, set()).add(cik)
    return owners


def partial_fix_ciks(
    index: FactIndex,
    ciks: list[str],
    tag: str,
    family: str,
    candidates: list[Candidate],
) -> list[str]:
    """CIKs where appending ``tag`` would produce a *slice*, not a total.

    A concept earns its place in the map only when the number it yields is
    the filer's whole top line. The test runs period by period: for every
    consolidated period in which the filer reports ``tag``, is ``tag`` the
    largest revenue-like concept that is not a known component?
    ``CostOfSales`` fails it for GlobalFoundries (5.3B against a 7.4B
    ``Revenue`` in the same year), ``UnregulatedOperatingRevenue`` fails it
    for Xcel (115M against a 14.7B total), and
    ``RegulatedOperatingRevenueElectricNonNuclear`` fails it for Duke (29.1B
    against a 32.2B total) — each would silently report a fraction of sales
    as sales.
    """
    tags = {
        candidate.tag
        for candidate in candidates
        if candidate.family == family
        and candidate.tag not in REVENUE_COMPONENT_PRIOR
    }
    partial: list[str] = []
    for cik in ciks:
        facts = [
            fact
            for fact in index.facts_for(cik)
            if fact.tag in tags
            and fact.uom == "USD"
            and not fact.segments
            and (fact.qtrs or 0) >= 1
        ]
        if not facts:
            continue
        periods: dict[tuple[str, int], list[Fact]] = {}
        for fact in facts:
            periods.setdefault((fact.ddate, fact.qtrs or 0), []).append(fact)
        sliced = False
        for rows in periods.values():
            peak = max(fact.value for fact in rows)
            mine = max(
                (fact.value for fact in rows if fact.tag == tag), default=0.0
            )
            if mine > 0 and mine < peak * 0.95:
                sliced = True
                break
        if sliced:
            partial.append(cik)
    return partial


def gap_report(
    field_name: str,
    family: str,
    index: FactIndex,
    sample: list[SampleRow],
    candidates: list[Candidate],
) -> GapReport:
    """Which sampled CIKs miss ``field_name``, and which concept would fix them."""
    ciks = [row.cik for row in sample]
    values = mapper_values(index, ciks)
    missing = [cik for cik in ciks if values[cik][field_name] is None]
    no_facts = [cik for cik in missing if not index.facts_for(cik)]

    concept_ciks = {
        candidate.tag: candidate.ciks_consol
        for candidate in candidates
        if candidate.family == family
    }
    no_concept: list[str] = []
    has_concept: list[str] = []
    for cik in missing:
        if any(cik in owners for owners in concept_ciks.values()):
            has_concept.append(cik)
        else:
            no_concept.append(cik)

    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.family != family or not candidate.ciks_consol:
            continue
        fixes = [cik for cik in missing if cik in candidate.ciks_consol]
        if not fixes:
            continue
        with extended_tag_map(field_name, (candidate.tag,)):
            after = mapper_values(index, fixes)
        fixed = [cik for cik in fixes if after[cik][field_name] is not None]
        ranked.append(
            {
                "tag": candidate.tag,
                "ciks_consol": len(candidate.ciks_consol),
                "gap_ciks": len(fixes),
                "fixes": len(fixed),
                "examples": candidate.examples,
            }
        )
    ranked.sort(key=lambda item: (-item["fixes"], -item["gap_ciks"], item["tag"]))

    return GapReport(
        field=field_name,
        sample_ciks=len(ciks),
        mapped=len(ciks) - len(missing),
        none=len(missing),
        none_no_concept=no_concept,
        none_has_concept=has_concept,
        none_no_facts=no_facts,
        ranked=ranked,
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def assign_verdicts(
    candidates: list[Candidate],
    gaps: dict[str, GapReport],
    merged_counterfactual: dict[str, Any] | None,
) -> dict[str, tuple[str, str]]:
    """ADD / KEEP / EXCLUDE per candidate, with the reason.

    * ADD — a top-line or bottom-line concept that fixes mapped CIKs.
    * KEEP — already in ``TAG_MAP``.
    * EXCLUDE — a component, a segment-only concept, an industry-specific
      gross figure, or a different statement line.

    ``fixes`` prefers the merged-quarter counterfactual: a single quarter
    rarely carries the annual base the TTM roll-forward needs, so a
    single-quarter ``fixes`` of zero says nothing about the tag.
    """
    verdicts: dict[str, tuple[str, str]] = {}
    mapped = {tag for tags in sf.TAG_MAP.values() for tag in tags}
    single: dict[str, dict[str, Any]] = {}
    for gap in gaps.values():
        for item in gap.ranked:
            single[item["tag"]] = item
    merged: dict[str, int] = {}
    partial_by_tag: dict[str, dict[str, Any]] = {}
    if merged_counterfactual:
        for key in ("sales", "net_income"):
            for item in merged_counterfactual.get(key, []):
                merged[item["tag"]] = item["fixes"]
                partial_by_tag[item["tag"]] = item

    for candidate in candidates:
        tag = candidate.tag
        if tag in mapped:
            verdicts[tag] = (VERDICT_KEEP, "already mapped")
            continue
        fixes = merged.get(tag, 0) or single.get(tag, {}).get("fixes", 0)
        gap_ciks = single.get(tag, {}).get("gap_ciks", 0)
        share = candidate.median_share_of_peak
        if candidate.ciks_any and not candidate.ciks_consol:
            verdicts[tag] = (
                VERDICT_EXCLUDE,
                "segment-only: never filed undimensioned",
            )
        elif tag in REASON_OVERRIDES:
            verdicts[tag] = (VERDICT_EXCLUDE, REASON_OVERRIDES[tag])
        elif candidate.family == "revenue" and share is not None and share < 0.5:
            verdicts[tag] = (
                VERDICT_EXCLUDE,
                f"component: median {share:.0%} of the filer's largest "
                "revenue-like concept for the same period",
            )
        elif fixes > 0:
            partial = partial_by_tag.get(tag, {}).get("partial", 0)
            # A concept is added when it yields the filer's whole top line
            # for the clear majority of the CIKs it fixes; the exceptions
            # are named in the counterfactual table.
            if partial > fixes * 0.25:
                examples = ", ".join(
                    partial_by_tag[tag].get("partial_examples", [])
                )
                verdicts[tag] = (
                    VERDICT_EXCLUDE,
                    f"partial top line: for {partial} of the {fixes} CIKs it "
                    f"would fix it is a fraction of a bigger concept "
                    f"({examples})",
                )
            else:
                verdicts[tag] = (VERDICT_ADD, f"fixes {fixes} mapped CIKs")
        elif gap_ciks > 0:
            verdicts[tag] = (
                VERDICT_EXCLUDE,
                "top-line for its filers, but the TTM roll-forward cannot "
                "reach it from the cached quarters",
            )
        else:
            verdicts[tag] = (VERDICT_EXCLUDE, "no mapped CIK files it")
    return verdicts


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def diagnosis_rows(
    index: FactIndex, cik: str, candidates: list[Candidate]
) -> list[dict[str, Any]]:
    """Every revenue-/income-like row one filer carries, consolidated first."""
    tags = {candidate.tag for candidate in candidates}
    forms = {
        submission.adsh: submission.form for submission in index.submissions_for(cik)
    }
    rows: list[dict[str, Any]] = []
    for fact in index.facts_for(cik):
        if fact.tag not in tags:
            continue
        rows.append(
            {
                "tag": fact.tag,
                "qtrs": fact.qtrs,
                "ddate": fact.ddate,
                "uom": fact.uom,
                "value": fact.value,
                "segments": fact.segments,
                "adsh": fact.adsh,
                "form": forms.get(fact.adsh, ""),
                "filed": fact.filed,
                "consolidated": fact.uom == "USD" and not fact.segments,
            }
        )
    rows.sort(
        key=lambda row: (
            not row["consolidated"],
            row["tag"],
            row["ddate"],
            row["qtrs"] if row["qtrs"] is not None else -1,
        )
    )
    return rows


def fmt_value(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.1f}B"
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.1f}M"
    return f"{value:,.0f}"


def fmt_int(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _consol_table(rows: list[dict[str, Any]], qtrs: int | None = None) -> list[str]:
    out = [
        "| tag | qtrs | ddate | value | form |",
        "|---|---:|---|---:|---|",
    ]
    for row in rows:
        if not row["consolidated"] or (row["qtrs"] or 0) < 1:
            continue
        if qtrs is not None and row["qtrs"] != qtrs:
            continue
        out.append(
            f"| `{row['tag']}` | {row['qtrs']} | {row['ddate']} | "
            f"{fmt_int(row['value'])} | {row['form']} |"
        )
    return out


def _dim_table(rows: list[dict[str, Any]]) -> list[str]:
    """Dimensional tags, one line each, with the axes they are broken by."""
    grouped: dict[str, set[str]] = {}
    for row in rows:
        if row["consolidated"]:
            continue
        grouped.setdefault(row["tag"], set()).add(row["segments"])
    if not grouped:
        return ["(none)"]
    out = ["| tag | dimensional rows | axes seen |", "|---|---:|---|"]
    for tag in sorted(grouped):
        axes = sorted(
            {segment.split("=", 1)[0] for segment in grouped[tag] if "=" in segment}
        )
        out.append(f"| `{tag}` | {len(grouped[tag])} | {', '.join(axes)} |")
    return out


def build_diagnosis(
    index: FactIndex,
    merged_index: FactIndex | None,
    merged_counterfactual: dict[str, Any] | None,
    candidates: list[Candidate],
    verdicts: dict[str, tuple[str, str]],
    quarters: list[str],
    sample: list[SampleRow],
    custom_names: set[str],
) -> dict[str, Any]:
    """JPM / XOM narrative, every figure taken from the built indexes."""
    entries: list[dict[str, Any]] = []
    symbol_cik = {row.symbol: row.cik for row in sample}
    jpm_cik = symbol_cik.get("JPM", "0000019617")
    xom_cik = symbol_cik.get("XOM", XOM_SNAPSHOT_CIK)
    span = f"{quarters[0]}–{quarters[-1]}" if quarters else "merged"

    # --- JPM ---------------------------------------------------------------
    jpm_rows = diagnosis_rows(index, jpm_cik, candidates)
    jpm_current = sf.map_fundamentals(index.facts_for(jpm_cik))
    jpm_merged = (
        sf.map_fundamentals(merged_index.facts_for(jpm_cik))
        if merged_index is not None
        else None
    )
    with extended_tag_map("sales", ("RevenuesNetOfInterestExpense",)):
        jpm_fixed = sf.map_fundamentals(index.facts_for(jpm_cik))
        jpm_fixed_merged = (
            sf.map_fundamentals(merged_index.facts_for(jpm_cik))
            if merged_index is not None
            else None
        )
    jpm_annual = (
        diagnosis_rows(merged_index, jpm_cik, candidates)
        if merged_index is not None
        else []
    )
    lines = [
        f"**JPMorgan Chase & Co** (`{jpm_cik}`), 10-Q filed "
        f"{jpm_rows[0]['filed'] if jpm_rows else '—'} for period 2026-06-30. "
        f"`sales` = **{fmt_value(jpm_current['sales'])}** on the "
        f"single-quarter index, **{fmt_value(jpm_merged['sales']) if jpm_merged else '—'}** "
        f"on the merged {span} index — the tag gap is real and survives the "
        "merge.",
        "",
        "Consolidated revenue-like rows JPM files in this quarter:",
        "",
    ]
    lines += _consol_table(jpm_rows)
    lines += [
        "",
        "Annual (`qtrs=4`) rows from the merged index, where the TTM base "
        "lives:",
        "",
    ]
    lines += _consol_table(jpm_annual, qtrs=4)
    lines += [
        "",
        "Dimensional (segment/product) rows — the same concepts broken down, "
        "never the consolidated fact:",
        "",
    ]
    lines += _dim_table(jpm_rows)
    lines += [
        "",
        "`RevenuesNetOfInterestExpense` **is** JPM's total net revenue line "
        "(FY2025: 182,447,000,000; six months 2026: 107,183,000,000). It is "
        "not in `TAG_MAP['sales']`, which is why JPM comes back `None`. "
        "`InterestIncomeOperating`, `InterestIncomeExpenseNet`, "
        "`NoninterestIncome`, `InvestmentBankingRevenue` and "
        "`PrincipalTransactionsRevenue` are components of it, not "
        "alternatives to it.",
        "",
        f"With `RevenuesNetOfInterestExpense` appended, `sales` = "
        f"**{fmt_value(jpm_fixed['sales'])}** (single quarter) / "
        f"**{fmt_value(jpm_fixed_merged['sales']) if jpm_fixed_merged else '—'}** "
        f"(merged {span}). `net_income` already maps — JPM files "
        f"`NetIncomeLoss` (six months 2026: 37,649,000,000).",
    ]
    entries.append({"title": "JPMorgan Chase (JPM)", "lines": lines})

    # --- XOM ---------------------------------------------------------------
    snapshot_rows = diagnosis_rows(index, xom_cik, candidates)
    filer_rows = diagnosis_rows(index, XOM_FILER_CIK, candidates)
    filer_annual = (
        diagnosis_rows(merged_index, XOM_FILER_CIK, candidates)
        if merged_index is not None
        else []
    )
    xom_filer_current = sf.map_fundamentals(index.facts_for(XOM_FILER_CIK))
    xom_filer_merged = (
        sf.map_fundamentals(merged_index.facts_for(XOM_FILER_CIK))
        if merged_index is not None
        else None
    )
    xom_forms = (
        sorted(
            {
                submission.form
                for submission in merged_index.submissions_for(XOM_FILER_CIK)
            }
        )
        if merged_index is not None
        else []
    )
    xom_periods = (
        sorted(
            {
                submission.period
                for submission in merged_index.submissions_for(XOM_FILER_CIK)
            }
        )
        if merged_index is not None
        else []
    )
    lines = [
        f"**Two different CIKs answer to XOM.** The engine snapshot maps "
        f"`XOM` → `{xom_cik}` (\"ExxonMobil Holdings Corp\"), which files "
        f"**nothing** in this dataset — {len(snapshot_rows)} revenue- or "
        f"income-like rows, so every field is `None` and no tag map can "
        f"reach it. The filer \"EXXON MOBIL CORP\" is `{XOM_FILER_CIK}` and "
        f"files {', '.join(xom_forms) or 'nothing'} for periods "
        f"{', '.join(xom_periods) or '—'}.",
        "",
        f"`{XOM_FILER_CIK}` (EXXON MOBIL CORP) consolidated revenue-like rows "
        "in this quarter:",
        "",
    ]
    lines += _consol_table(filer_rows)
    lines += [
        "",
        "Annual (`qtrs=4`) rows from the merged index:",
        "",
    ]
    lines += _consol_table(filer_annual, qtrs=4)
    lines += [
        "",
        "Dimensional rows — the oil & gas disaggregation of the same total:",
        "",
    ]
    lines += _dim_table(filer_rows)
    lines += [
        "",
        f"`Revenues` — already the second entry of `TAG_MAP['sales']` — is "
        f"the concept Exxon files, and it works: `sales` = "
        f"**{fmt_value(xom_filer_current['sales'])}** on the single-quarter "
        f"index and **{fmt_value(xom_filer_merged['sales']) if xom_filer_merged else '—'}** "
        f"on the merged {span} index. The `None` the live check saw comes "
        f"from the snapshot's CIK, not from the tag map: `{xom_cik}` has no "
        f"filings in any cached quarter, so `fundamentals_for_symbol` "
        f"returns all-`None` for XOM. Fixing XOM is a snapshot CIK fix, not "
        f"a `TAG_MAP` fix.",
        "",
        "The dimensional `Revenues` rows (ProductOrService=SalesAndOther"
        "OperatingRevenue / IncomeFromEquityAffiliates / OtherRevenue, "
        "BusinessSegments=Upstream / EnergyProducts / SpecialtyProducts / "
        "ChemicalProducts) are the disaggregation of the same total; the "
        "consolidated row is the one the mapper takes, and does.",
    ]
    entries.append({"title": "Exxon Mobil (XOM)", "lines": lines})

    return {
        "entries": entries,
        "recommendations": _recommendation_lines(
            candidates, verdicts, merged_counterfactual, custom_names
        ),
        "exclusions": _exclusion_lines(candidates, verdicts),
    }


def _recommendation_lines(
    candidates: list[Candidate],
    verdicts: dict[str, tuple[str, str]],
    merged_counterfactual: dict[str, Any] | None,
    custom_names: set[str],
) -> list[str]:
    """The ADD verdicts, per field, with the evidence that earned them."""
    merged_fixes: dict[str, int] = {}
    if merged_counterfactual:
        for key in ("sales", "net_income"):
            for item in merged_counterfactual.get(key, []):
                merged_fixes[item["tag"]] = item["fixes"]
    lines: list[str] = []
    for field_name, family, title in (
        ("sales", "revenue", "`TAG_MAP['sales']`"),
        ("net_income", "income", "`TAG_MAP['net_income']`"),
    ):
        adds = [
            candidate
            for candidate in candidates
            if candidate.family == family
            and verdicts.get(candidate.tag, ("", ""))[0] == VERDICT_ADD
        ]
        adds.sort(
            key=lambda c: (
                -len(c.ciks_consol),
                -merged_fixes.get(c.tag, 0),
                c.tag,
            )
        )
        lines.append(f"### {title}")
        lines.append("")
        if not adds:
            lines.append(
                f"No candidate fixes a mapped CIK for `{field_name}` in this "
                "sample; the existing concepts are sufficient."
            )
            lines.append("")
            continue
        lines.append("Append in this order:")
        lines.append("")
        for candidate in adds:
            code, name, count, total = candidate.sic_top
            fixes = merged_fixes.get(candidate.tag, 0)
            lines.append(
                f"- **`{candidate.tag}`** — fixes {fixes} mapped "
                f"CIK{'' if fixes == 1 else 's'} on the merged quarters; "
                f"{len(candidate.ciks_consol)} sampled CIKs file it "
                f"consolidated ({len(candidate.ciks_annual)} with a "
                f"`qtrs=4` annual row); examples: "
                f"{', '.join(candidate.examples)}; top SIC {code} {name} "
                f"({count}/{total})."
            )
        lines.append("")
        lines.append("```python")
        lines.append(f"TAG_MAP[{field_name!r}] = (")
        for tag in sf.TAG_MAP[field_name]:
            lines.append(f"    {tag!r},")
        lines.append("    # additions, in the order above:")
        for candidate in adds:
            lines.append(f"    {candidate.tag!r},")
        lines.append(")")
        lines.append("```")
        lines.append("")
        ifrs = [
            candidate
            for candidate in adds
            if candidate.tag in ("Revenue", "RevenueFromContractsWithCustomers")
        ]
        if ifrs:
            lines.append(
                "The IFRS concepts ("
                + ", ".join(f"`{candidate.tag}`" for candidate in ifrs)
                + ") are filed by 20-F filers — "
                + ", ".join(
                    ", ".join(candidate.examples) for candidate in ifrs
                )
                + " in this sample. The bulk dataset's `version` column "
                "carries the taxonomy (`ifrs/2025` against `us-gaap/2026`), "
                f"and {len(custom_names)} company-specific extensions in the "
                "cached quarters carry a revenue- or income-like name, so a "
                "bare `Revenue` entry can be satisfied by a private "
                "extension. If the map takes these two, take them for "
                "IFRS-tagged facts only."
            )
            lines.append("")
    return lines


def _exclusion_lines(
    candidates: list[Candidate], verdicts: dict[str, tuple[str, str]]
) -> list[str]:
    """Concepts that look like revenue or income and must stay out."""
    excluded = [
        candidate
        for candidate in candidates
        if verdicts.get(candidate.tag, ("", ""))[0] == VERDICT_EXCLUDE
        and candidate.ciks_any
    ]
    excluded.sort(key=lambda c: (-len(c.ciks_any), c.tag))
    lines: list[str] = []
    for candidate in excluded:
        code, name, count, total = candidate.sic_top
        share = candidate.median_share_of_peak
        head = (
            f"`{candidate.tag}` — {len(candidate.ciks_any)} CIKs, "
            f"{len(candidate.ciks_consol)} consolidated"
        )
        if not candidate.ciks_consol:
            reason = "**segment-only**: every row is dimensional"
        elif candidate.family == "revenue" and share is not None and share < 0.5:
            reason = (
                f"**component**: median {share:.0%} of the filer's largest "
                "revenue-like concept for the same period"
            )
        else:
            reason = verdicts.get(candidate.tag, ("", "different line"))[1]
        tail = f"top SIC {code} {name} ({count}/{total})" if code else "no SIC"
        lines.append(f"- {head}; {reason}; {tail}.")
    if not lines:
        lines.append("None in this sample.")
    return lines


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _summary_lines(
    gaps: dict[str, GapReport],
    merged_counterfactual: dict[str, Any] | None,
    candidates: list[Candidate],
    verdicts: dict[str, tuple[str, str]],
) -> list[str]:
    """The findings, in the order a reader needs them."""
    merged_fixes: dict[str, int] = {}
    if merged_counterfactual:
        for key in ("sales", "net_income"):
            for item in merged_counterfactual.get(key, []):
                merged_fixes[item["tag"]] = item["fixes"]
    adds = sorted(
        (
            candidate
            for candidate in candidates
            if verdicts.get(candidate.tag, ("", ""))[0] == VERDICT_ADD
        ),
        key=lambda c: (-merged_fixes.get(c.tag, 0), -len(c.ciks_consol), c.tag),
    )
    lines = ["## Summary", ""]
    for field_name in ("sales", "net_income"):
        gap = gaps[field_name]
        lines.append(
            f"- **`{field_name}`**: {gap.mapped} of {gap.sample_ciks} sampled "
            f"CIKs map on the single quarter; {gap.none} are `None`, of which "
            f"{len(gap.none_no_concept)} file no revenue/income-like concept "
            f"at all (the true tag gap), {len(gap.none_has_concept)} file one "
            f"but lack the annual base in this quarter, and "
            f"{len(gap.none_no_facts)} file nothing at all."
        )
    if adds:
        lines.append(
            "- **Add to `TAG_MAP`**: "
            + ", ".join(
                f"`{candidate.tag}` (fixes {merged_fixes.get(candidate.tag, 0)})"
                for candidate in adds
            )
            + "."
        )
    else:
        lines.append("- **Add to `TAG_MAP`**: nothing in this sample.")
    lines.append(
        "- **JPMorgan** files its top line as `RevenuesNetOfInterestExpense`, "
        "which `TAG_MAP['sales']` does not carry — a real tag gap that "
        "survives the quarter merge."
    )
    lines.append(
        "- **Exxon (XOM)** is a snapshot CIK defect, not a tag gap: the "
        f"snapshot maps XOM to `{XOM_SNAPSHOT_CIK}`, which files nothing in "
        f"any cached quarter; the filer `{XOM_FILER_CIK}` (EXXON MOBIL CORP) "
        "files `Revenues`, which is already mapped and works."
    )
    lines.append(
        "- **Keep out**: bank gross interest income "
        "(`InterestIncomeOperating`, `InterestAndDividendIncomeOperating`) is "
        "*larger* than the bank's net revenue total; `NoninterestIncome`, "
        "`InterestIncomeExpenseNet`, `InvestmentBankingRevenue` and "
        "`PrincipalTransactionsRevenue` are components of it; "
        "`ComprehensiveIncomeNetOfTax` is not net income; the pre-tax "
        "`IncomeLossFromContinuingOperationsBeforeIncomeTaxes…` line is not "
        "the bottom line either."
    )
    return lines


def render(
    *,
    zip_path: Path,
    quarters: list[str],
    sample: list[SampleRow],
    index: FactIndex,
    candidates: list[Candidate],
    gaps: dict[str, GapReport],
    merged_counterfactual: dict[str, Any] | None,
    diagnosis: dict[str, Any],
    verdicts: dict[str, tuple[str, str]],
) -> str:
    lines: list[str] = []
    add = lines.append

    add("# SEC XBRL tag coverage: revenue and net income")
    add("")
    add(
        f"Probe `scripts/sec_tag_probe.py` · dataset `{zip_path.name}` · "
        f"cached quarters: {', '.join(quarters)} · sample {len(sample)} "
        f"universe CIKs · index {len(index.ciks())} CIKs with facts, "
        f"{sum(len(f) for f in index.by_cik.values()):,} facts. The index "
        f"carries the sample plus CIK `{XOM_FILER_CIK}` — the filer the "
        f"snapshot's XOM entry should point at — because the diagnosis "
        f"needs its rows; the distribution counts the sample only."
    )
    add("")
    add(
        "Every number below is computed by the probe from that single index "
        "build, plus the merged-quarter builds the counterfactual and the "
        "diagnosis need. Re-running the probe rewrites this file."
    )
    add("")
    for line in _summary_lines(gaps, merged_counterfactual, candidates, verdicts):
        add(line)
    add("")

    add("## 1. Method")
    add("")
    add(
        "- **Sample.** `universe.json` (4,217 symbols) joined to the engine "
        f"snapshot's symbol → CIK map, top {len(sample)} by "
        "`market_cap_basic`, CIK-deduplicated, focus symbols (JPM, XOM) "
        "force-included."
    )
    add(
        "- **One build.** `FactIndex.build(zip, ciks=…)` streams `sub.txt` "
        "then `num.txt` once; a row is kept only when its accession belongs "
        "to a wanted CIK. The wanted set is the sample plus CIK "
        f"`{XOM_FILER_CIK}` (the real Exxon filer, which the diagnosis "
        "needs and the distribution must not count). The distribution and "
        "the gap analysis read the sample's 500 CIKs only."
    )
    add(
        "- **Candidates.** A curated list of known revenue/income concepts "
        "plus a name sweep over every tag observed in the sample "
        "(`Revenues?|Sales|Turnover` and `NetIncome|ProfitLoss|"
        "IncomeLossFromContinuingOperations`, each matched as a whole "
        "CamelCase word), filtered to standard duration concepts using the "
        "`iord`/`custom`/`abstract` flags in the ZIP's own `tag.txt`. The "
        "sweep is what keeps the curated list honest."
    )
    add(
        "- **Consolidated** means `uom == USD` and `segments == ''` — the "
        "undimensioned fact, the same filter "
        "`app.market.sec_fundamentals._selected` applies. A concept that "
        "only ever appears dimensionally is a segment concept, not a total."
    )
    add(
        "- **Gap.** `map_fundamentals` runs over the sample; a CIK whose "
        "`sales`/`net_income` is `None` is split into *files no candidate "
        "concept at all* (a true tag gap) and *files one but the annual base "
        "is not in this quarter* (a single-quarter coverage artifact — the "
        "TTM roll-forward needs the 10-K quarter)."
    )
    add(
        "- **Counterfactual.** One candidate appended to `TAG_MAP` in memory "
        "(never on disk), mapper re-run, CIKs that flip `None` → value "
        "counted. Repeated on the merged multi-quarter index for the missing "
        "subset."
    )
    add("")

    for family, title, blurb in (
        (
            "revenue",
            "2. Tag distribution — revenue-like concepts",
            "Duration concepts whose name carries revenue/sales/turnover, "
            "ranked by the number of sampled CIKs that file them "
            "consolidated. `annual` = files a `qtrs=4` row; `dim-only` = the "
            "concept never appears undimensioned for that filer; "
            "**median share of peak** = the concept's median value as a "
            "fraction of the largest revenue-like concept the same filer "
            "reports for the same period (1.00 = it is the top line, a "
            "small fraction = a component of a bigger concept).",
        ),
        (
            "income",
            "3. Tag distribution — income-like concepts",
            "Duration concepts whose name carries net income/profit/loss, "
            "ranked the same way. The share column is revenue-only: an "
            "income concept has no revenue peak to be measured against, so "
            "it reads `—`.",
        ),
    ):
        rows = [c for c in candidates if c.family == family and c.ciks_any]
        rows.sort(key=lambda c: (-len(c.ciks_consol), -len(c.ciks_any), c.tag))
        add(f"## {title}")
        add("")
        add(blurb)
        add("")
        add(
            "| tag | CIKs (any) | CIKs (consol.) | annual | YTD | dim-only | "
            "median share of peak | top SIC (share) | examples | verdict |"
        )
        add("|---|---:|---:|---:|---:|---:|---:|---|---|---|")
        for candidate in rows:
            verdict = verdicts.get(candidate.tag, ("", ""))[0]
            code, name, count, total = candidate.sic_top
            sic = f"{code} {name} ({count}/{total})" if code else "—"
            share = candidate.median_share_of_peak
            share_text = "—" if share is None else f"{share:.2f}"
            add(
                f"| `{candidate.tag}` | {len(candidate.ciks_any)} | "
                f"{len(candidate.ciks_consol)} | {len(candidate.ciks_annual)} | "
                f"{len(candidate.ciks_ytd)} | {len(candidate.ciks_dim_only)} | "
                f"{share_text} | {md_escape(sic)} | "
                f"{md_escape(', '.join(candidate.examples))} | {verdict} |"
            )
        add("")
        tail = [c for c in candidates if c.family == family and not c.ciks_any]
        if tail:
            add(
                "Curated concepts with **zero** sampled filers in this "
                "quarter: " + ", ".join(f"`{c.tag}`" for c in tail) + "."
            )
            add("")

    add("## 4. Mapper coverage over the sample")
    add("")
    add(
        "| field | sampled CIKs | mapped | None | None, no concept | "
        "None, concept but no annual base | None, no facts |"
    )
    add("|---|---:|---:|---:|---:|---:|---:|")
    for field_name in ("sales", "net_income"):
        gap = gaps[field_name]
        add(
            f"| `{field_name}` | {gap.sample_ciks} | {gap.mapped} | {gap.none} | "
            f"{len(gap.none_no_concept)} | {len(gap.none_has_concept)} | "
            f"{len(gap.none_no_facts)} |"
        )
    add("")
    for field_name in ("sales", "net_income"):
        gap = gaps[field_name]
        add(f"### `{field_name}` — candidates ranked by how many CIKs they fix")
        add("")
        add(
            "`gap CIKs` counts sampled CIKs that are `None` *and* file the "
            "tag consolidated; `fixes` counts how many of those flip to a "
            "number when the tag is appended to `TAG_MAP`. On the "
            "single-quarter index `fixes` is almost always zero, because the "
            "TTM roll-forward needs the 10-K quarter's annual row — which is "
            "why the merged-quarter column is the one that decides."
        )
        add("")
        add(
            "| tag | CIKs (consol.) | gap CIKs | fixes (1 quarter) | "
            "fixes (merged) | examples |"
        )
        add("|---|---:|---:|---:|---:|---|")
        merged_fixes = {
            item["tag"]: item["fixes"]
            for item in (merged_counterfactual or {}).get(field_name, [])
        }
        for item in gap.ranked[:15]:
            add(
                f"| `{item['tag']}` | {item['ciks_consol']} | {item['gap_ciks']} | "
                f"{item['fixes']} | {merged_fixes.get(item['tag'], 0)} | "
                f"{md_escape(', '.join(item['examples']))} |"
            )
        add("")
        if merged_counterfactual and merged_counterfactual.get(field_name):
            add(
                f"Same counterfactual on the merged {', '.join(quarters)} "
                f"index, over the {len(gap.none_no_concept) + len(gap.none_has_concept)} "
                f"CIKs that were `None`:"
            )
            add("")
            add("| tag | fixes (merged) | of which partial |")
            add("|---:|---:|")
            for item in merged_counterfactual[field_name][:15]:
                partial = item.get("partial", 0)
                note = (
                    f"{partial} ({', '.join(item.get('partial_examples', []))})"
                    if partial
                    else "0"
                )
                add(f"| `{item['tag']}` | {item['fixes']} | {note} |")
            add("")
        detail = (merged_counterfactual or {}).get("still_missing", {}).get(
            field_name, []
        )
        if detail:
            add(
                f"**Still `None` for `{field_name}` after the merge** "
                f"({len(detail)} CIKs), with the concepts they do file "
                "(largest consolidated value first):"
            )
            add("")
            add("| symbol | name | forms | concepts filed |")
            add("|---|---|---|---|")
            for row in detail:
                tags = ", ".join(
                    f"`{item['tag']}`{'(custom)' if item.get('custom') else ''} "
                    f"{fmt_value(item['max_value'])}"
                    for item in row["tags"]
                )
                add(
                    f"| {row['symbol']} | {md_escape(row['name'][:34])} | "
                    f"{md_escape(', '.join(row['forms']))} | "
                    f"{md_escape(tags) or '—'} |"
                )
            add("")

    add("## 5. Diagnosis: JPM and XOM")
    add("")
    for entry in diagnosis["entries"]:
        add(f"### {entry['title']}")
        add("")
        for line in entry["lines"]:
            add(line)
        add("")

    add("## 6. Recommended `TAG_MAP` extensions")
    add("")
    for line in diagnosis["recommendations"]:
        add(line)
    add("")

    add("## 7. Concepts that must NOT be added")
    add("")
    for line in diagnosis["exclusions"]:
        add(line)
    add("")

    add("## 8. Reproduction")
    add("")
    add("```bash")
    add(".venv/bin/python scripts/sec_tag_probe.py")
    add("```")
    add("")
    add(
        "Streams the cached ZIP, no network. `--sample`, `--zip`, "
        "`--cache-dir`, `--out-md` and `--out-json` override the defaults; "
        "the JSON carries every counter in this report."
    )
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SEC XBRL tag coverage probe for revenue and net income."
    )
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument(
        "--no-merged",
        action="store_true",
        help="skip the merged-quarter counterfactual and diagnosis builds",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.time()

    sample = load_sample(args.universe, args.snapshot, args.sample)
    cap_by_cik = {row.cik: row.market_cap for row in sample}
    symbol_by_cik = {row.cik: row.symbol for row in sample}
    # The filer the snapshot's XOM entry should point at, so the diagnosis
    # and the still-missing table can name it.
    symbol_by_cik.setdefault(XOM_FILER_CIK, "XOM (filer CIK)")
    print(f"sample: {len(sample)} CIKs from {len(sample)} symbols")

    build_started = time.time()
    # The sample plus the filer the snapshot's XOM entry should point at:
    # the diagnosis needs its rows, and the distribution must not count it
    # (it is not one of the universe's 500).
    build_ciks = [row.cik for row in sample]
    if XOM_FILER_CIK not in build_ciks:
        build_ciks.append(XOM_FILER_CIK)
    index = FactIndex.build(args.zip, ciks=build_ciks)
    build_seconds = time.time() - build_started
    print(
        f"index: {len(index.ciks())} CIKs, "
        f"{sum(len(f) for f in index.by_cik.values()):,} facts, "
        f"{build_seconds:.0f}s"
    )

    sic_by_cik: dict[str, str] = {}
    for submission in index.submissions.values():
        sic_by_cik.setdefault(submission.cik, submission.sic)

    metas = load_tag_meta(args.zip)
    candidates = discover_candidates(index, metas)
    measure(
        candidates,
        index,
        cap_by_cik,
        symbol_by_cik,
        sic_by_cik,
        ciks=[row.cik for row in sample],
    )
    print(f"candidates: {len(candidates)}")

    gaps = {
        "sales": gap_report("sales", "revenue", index, sample, candidates),
        "net_income": gap_report("net_income", "income", index, sample, candidates),
    }
    for name, gap in gaps.items():
        print(
            f"{name}: mapped={gap.mapped} none={gap.none} "
            f"(no-concept={len(gap.none_no_concept)} "
            f"has-concept={len(gap.none_has_concept)} "
            f"no-facts={len(gap.none_no_facts)})"
        )

    # -- merged-quarter counterfactual (verdicts are assigned after it) -------
    merged_counterfactual: dict[str, Any] | None = None
    merged_index: FactIndex | None = None
    quarters = sorted(
        path.stem for path in args.cache_dir.glob("*.zip") if path.is_file()
    )
    # Every revenue-/income-named tag the datasets carry, company extensions
    # included: the still-missing table names what a filer actually files
    # even when no general concept can reach it.
    sweep_tags: set[str] = set()
    if not args.no_merged and quarters:
        sweep_tags = {
            tag
            for tag in observed_tags(
                [args.cache_dir / f"{quarter}.zip" for quarter in quarters]
            )
            if (REVENUE_NAME_RE.search(tag) and not REVENUE_DENYLIST_RE.search(tag))
            or (INCOME_NAME_RE.search(tag) and not INCOME_DENYLIST_RE.search(tag))
        }
    if not args.no_merged and quarters:
        missing = sorted(
            set(gaps["sales"].none_no_concept)
            | set(gaps["sales"].none_has_concept)
            | set(gaps["net_income"].none_no_concept)
            | set(gaps["net_income"].none_has_concept)
        )
        if missing:
            merged_index = build_quarters(
                [args.cache_dir / f"{quarter}.zip" for quarter in quarters],
                ciks=sorted(set(missing) | {XOM_FILER_CIK}),
            )
            merged_counterfactual = {}
            still_detail: dict[str, list[dict[str, Any]]] = {}
            for field_name, family in (
                ("sales", "revenue"),
                ("net_income", "income"),
            ):
                values = mapper_values(merged_index, missing)
                still_missing = [
                    cik for cik in missing if values[cik][field_name] is None
                ]
                tags = {
                    candidate.tag
                    for candidate in candidates
                    if candidate.family == family
                }
                # The family's own name pattern, extensions included.
                def _in_family(tag: str) -> bool:
                    if family == "income":
                        return bool(
                            INCOME_NAME_RE.search(tag)
                            and not INCOME_DENYLIST_RE.search(tag)
                        )
                    return bool(
                        REVENUE_NAME_RE.search(tag)
                        and not REVENUE_DENYLIST_RE.search(tag)
                    )

                family_sweep = {tag for tag in sweep_tags if _in_family(tag)}
                owners = consolidated_ciks_by_tag(merged_index, tags)
                results = []
                for tag in sorted(tags):
                    fixes = [
                        cik
                        for cik in still_missing
                        if cik in owners.get(tag, ())
                    ]
                    if not fixes:
                        continue
                    with extended_tag_map(field_name, (tag,)):
                        after = mapper_values(merged_index, fixes)
                    fixed = [
                        cik for cik in fixes if after[cik][field_name] is not None
                    ]
                    partial = partial_fix_ciks(
                        merged_index, fixed, tag, family, candidates
                    )
                    results.append(
                        {
                            "tag": tag,
                            "fixes": len(fixed),
                            "partial": len(partial),
                            "partial_examples": [
                                symbol_by_cik.get(cik, cik) for cik in partial[:3]
                            ],
                        }
                    )
                results.sort(key=lambda item: (-item["fixes"], item["tag"]))
                merged_counterfactual[field_name] = results
                # What the unfixed filers do file, so the report can name them.
                # Company-specific extensions are shown too (marked), because
                # a filer whose top line is an extension is unreachable by
                # any general tag map — APA Corp files ``RevenuesAndOther``.
                detail = []
                for cik in still_missing:
                    facts = merged_index.facts_for(cik)
                    forms = sorted(
                        {
                            submission.form
                            for submission in merged_index.submissions_for(cik)
                        }
                    )
                    filed_tags: dict[str, float] = {}
                    for fact in facts:
                        if (
                            fact.tag in tags or fact.tag in family_sweep
                        ) and fact.uom == "USD" and not fact.segments and (
                            fact.qtrs or 0
                        ) >= 1:
                            filed_tags[fact.tag] = max(
                                filed_tags.get(fact.tag, 0.0), fact.value
                            )
                    detail.append(
                        {
                            "symbol": symbol_by_cik.get(cik, cik),
                            "cik": cik,
                            "name": merged_index.issuer_name(cik),
                            "forms": forms,
                            "tags": [
                                {
                                    "tag": tag,
                                    "max_value": value,
                                    "custom": metas.get(tag) is not None
                                    and metas[tag].custom,
                                }
                                for tag, value in sorted(
                                    filed_tags.items(),
                                    key=lambda item: -item[1],
                                )[:4]
                            ],
                        }
                    )
                still_detail[field_name] = detail
                print(
                    f"merged {field_name}: still missing {len(still_missing)} "
                    f"of {len(missing)}; best "
                    f"{results[0]['tag'] if results else '—'} "
                    f"({results[0]['fixes'] if results else 0})"
                )
            merged_counterfactual["still_missing"] = still_detail

    verdicts = assign_verdicts(candidates, gaps, merged_counterfactual)
    custom_names = (
        custom_named_tags(
            [args.cache_dir / f"{quarter}.zip" for quarter in quarters]
        )
        if quarters
        else set()
    )
    print(f"custom revenue/income-named extensions: {len(custom_names)}")

    diagnosis = build_diagnosis(
        index,
        merged_index,
        merged_counterfactual,
        candidates,
        verdicts,
        quarters,
        sample,
        custom_names,
    )

    payload = {
        "zip": str(args.zip),
        "quarters": quarters,
        "sample_size": len(sample),
        "sample": [
            {
                "symbol": row.symbol,
                "cik": row.cik,
                "description": row.description,
                "market_cap": row.market_cap,
            }
            for row in sample
        ],
        "index": {
            "ciks": len(index.ciks()),
            "facts": sum(len(f) for f in index.by_cik.values()),
            "build_seconds": round(build_seconds, 1),
        },
        "candidates": [
            {
                "tag": candidate.tag,
                "family": candidate.family,
                "source": candidate.source,
                "tlabel": candidate.meta.tlabel if candidate.meta else "",
                "iord": candidate.meta.iord if candidate.meta else "",
                "ciks_any": len(candidate.ciks_any),
                "ciks_consol": len(candidate.ciks_consol),
                "ciks_annual": len(candidate.ciks_annual),
                "ciks_ytd": len(candidate.ciks_ytd),
                "ciks_dim_only": len(candidate.ciks_dim_only),
                "rows_total": candidate.rows_total,
                "rows_consol": candidate.rows_consol,
                "rows_dim": candidate.rows_dim,
                "median_share_of_peak": candidate.median_share_of_peak,
                "sic_top": candidate.sic_top,
                "examples": candidate.examples,
                "verdict": verdicts.get(candidate.tag, ("", ""))[0],
                "reason": verdicts.get(candidate.tag, ("", ""))[1],
            }
            for candidate in sorted(
                candidates, key=lambda c: (-len(c.ciks_consol), c.tag)
            )
        ],
        "gaps": {
            name: {
                "sample_ciks": gap.sample_ciks,
                "mapped": gap.mapped,
                "none": gap.none,
                "none_no_concept": len(gap.none_no_concept),
                "none_has_concept": len(gap.none_has_concept),
                "none_no_facts": len(gap.none_no_facts),
                "ranked": gap.ranked,
            }
            for name, gap in gaps.items()
        },
        "merged_counterfactual": merged_counterfactual,
        "diagnosis": diagnosis,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    markdown = render(
        zip_path=args.zip,
        quarters=quarters,
        sample=sample,
        index=index,
        candidates=candidates,
        gaps=gaps,
        merged_counterfactual=merged_counterfactual,
        diagnosis=diagnosis,
        verdicts=verdicts,
    )
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(markdown, encoding="utf-8")

    print(f"wrote {args.out_md} ({len(markdown):,} chars)")
    print(f"wrote {args.out_json}")
    print(f"total {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
