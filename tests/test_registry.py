"""Phase-2 RED: provider registry and the ``GET /providers`` projection.

Gate 1 (02 §5 Fase 2): the registry declares **≥2 providers for QUOTE and ≥1 for
every other capability**, and the ``/providers`` matrix is compared against the
registry object — two sources, never a hardcoded list in the test.

Gate 7 (02 §5 / §3.11) starts here: the matrix may describe a credential
(``requires_key``) but may never contain its value.
"""

from __future__ import annotations

from typing import Any, Iterator

from app.contracts import Capability
from app.providers.registry import REGISTRY, ladder_for
from tests._support import api_client

#: Cost tiers, cheapest first. The number is the ordering rule, not a label.
TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2}

#: Keys ``GET /providers`` promises (02 §3.2). Extra keys are allowed; missing
#: ones mean a client cannot decide which rung to expect.
PROVIDER_KEYS = {
    "provider",
    "capabilities",
    "cost_tier",
    "trust",
    "delay",
    "requires_key",
    "schema_version",
    "available",
    "health",
}

#: Anything that would be a credential leak if it appeared in the response.
SECRET_KEYS = {"api_key", "apikey", "auth", "token", "secret", "password", "authorization"}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _assert_no_secret(payload: Any, secret: str) -> None:
    assert secret not in "\n".join(_strings(payload)), (
        "the /providers response contains a provider credential value"
    )


def test_registry_declares_two_quote_providers_and_one_per_capability() -> None:
    quote = [entry for entry in REGISTRY if Capability.QUOTE in entry.capabilities]
    assert len(quote) >= 2, (
        "02 §5 Fase 2 gate 1 requires at least two QUOTE providers, got "
        f"{[entry.name for entry in quote]!r}"
    )

    empty = {capability.value: [] for capability in Capability if not ladder_for(capability)}
    assert empty == {}, (
        "every capability needs at least one rung (02 §5 Fase 2 gate 1); empty: "
        f"{sorted(empty)}"
    )


def test_registry_specs_are_well_formed() -> None:
    names = [entry.name for entry in REGISTRY]
    assert len(names) == len(set(names)), f"duplicate provider names: {names}"

    for entry in REGISTRY:
        assert entry.cost_tier in TIER_ORDER, f"{entry.name}: unknown tier {entry.cost_tier!r}"
        assert 1 <= entry.trust <= 4, f"{entry.name}: trust must be 1..4"
        assert entry.capabilities, f"{entry.name}: declares no capability"
        assert set(entry.capabilities) <= set(Capability), f"{entry.name}: unknown capability"
        assert entry.schema_version, f"{entry.name}: empty schema_version"
        assert entry.delay in {"realtime", "delayed15", "eod", "delayed"}, (
            f"{entry.name}: unexpected delay {entry.delay!r}"
        )
        if entry.auth is not None:
            # ``auth`` is the NAME of an env var, never a value (02 §3.4/§3.11).
            assert entry.auth.isupper() and entry.auth.replace("_", "").isalnum(), (
                f"{entry.name}: auth must be an env-var name, got {entry.auth!r}"
            )


def test_quote_ladder_is_ordered_by_cost_tier() -> None:
    rungs = ladder_for(Capability.QUOTE)
    assert len(rungs) >= 2, f"QUOTE ladder has {len(rungs)} rung(s): {[r.name for r in rungs]}"

    tiers = [TIER_ORDER[entry.cost_tier] for entry in rungs]
    assert tiers == sorted(tiers), (
        "a paid rung precedes a free one: "
        + " -> ".join(f"{entry.name}({entry.cost_tier})" for entry in rungs)
    )
    assert rungs[0].cost_tier == "T0", (
        "the first QUOTE rung must be free (cache is L0 and is not a provider): "
        f"{rungs[0].name}({rungs[0].cost_tier})"
    )


def test_quote_ladder_follows_the_documented_order() -> None:
    """02 §3.4 fixes the QUOTE ladder: L1 yahoo, L2 stooq, L3 nasdaq, L4 finnhub, L5 polygon.

    Cost tier decides the class; the intra-tier order is the documented one,
    not alphabetical (``nasdaq`` < ``stooq`` < ``yahoo`` would put the
    "delayed15" rung first and invert the fixture the verdict tests drive).
    """

    names = [entry.name for entry in ladder_for(Capability.QUOTE)]
    assert names[:3] == ["yahoo", "stooq", "nasdaq"], (
        "02 §3.4 fixes L1 yahoo, L2 stooq, L3 nasdaq; got " + ", ".join(names)
    )


async def test_providers_endpoint_is_the_registry_projection(
    tmp_path: Any, monkeypatch: Any
) -> None:
    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/providers")
        assert response.status_code == 200, response.text
        rows = response.json()

    assert rows, "GET /providers returned an empty matrix"
    registry = {entry.name: entry for entry in REGISTRY}
    assert {row["provider"] for row in rows} == set(registry), (
        "the endpoint and REGISTRY disagree about which providers exist: "
        f"endpoint-only={sorted({row['provider'] for row in rows} - set(registry))}, "
        f"registry-only={sorted(set(registry) - {row['provider'] for row in rows})}"
    )

    for row in rows:
        missing = PROVIDER_KEYS - set(row)
        assert missing == set(), f"{row['provider']}: /providers is missing {sorted(missing)}"
        entry = registry[row["provider"]]
        assert set(row["capabilities"]) == {cap.value for cap in entry.capabilities}, (
            f"{row['provider']}: capabilities differ from the registry"
        )
        assert row["cost_tier"] == entry.cost_tier
        assert row["trust"] == entry.trust
        assert row["delay"] == entry.delay
        assert row["requires_key"] is (entry.auth is not None)
        assert row["schema_version"] == entry.schema_version
        assert row["health"]["state"] in {"closed", "open", "half_open", "unknown"}

    endpoint_quote = {
        row["provider"] for row in rows if Capability.QUOTE.value in row["capabilities"]
    }
    assert endpoint_quote == {entry.name for entry in REGISTRY if Capability.QUOTE in entry.capabilities}
    assert len(endpoint_quote) >= 2, (
        "GET /providers advertises fewer than two QUOTE providers: "
        f"{sorted(endpoint_quote)}"
    )


async def test_providers_marks_keyed_providers_unavailable_without_the_key(
    tmp_path: Any, monkeypatch: Any
) -> None:
    for name in (
        "FINNHUB_API_KEY",
        "POLYGON_API_KEY",
        "EODHD_API_KEY",
        "TIINGO_API_KEY",
        "TWELVE_DATA_API_KEY",
        "OPENFIGI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/providers")
        assert response.status_code == 200, response.text
        rows = response.json()

    keyed = [row for row in rows if row["requires_key"]]
    assert keyed, (
        "no keyed provider is declared; gate 2 ('provider tanpa key di-skip') has "
        "nothing to skip and the T1 rung of 02 §3.4 is missing"
    )
    assert all(row["available"] is False for row in keyed), (
        "a keyed provider with no credential in this process must report "
        f"available=false: {[row['provider'] for row in keyed if row['available']]}"
    )


async def test_providers_never_exposes_a_credential_value(
    tmp_path: Any, monkeypatch: Any
) -> None:
    secret = "sk-live-do-not-print-4c1f"
    for name in (
        "FINNHUB_API_KEY",
        "POLYGON_API_KEY",
        "EODHD_API_KEY",
        "TIINGO_API_KEY",
        "TWELVE_DATA_API_KEY",
        "OPENFIGI_API_KEY",
    ):
        monkeypatch.setenv(name, secret)

    async with api_client(tmp_path, monkeypatch) as client:
        response = await client.get("/providers")
        assert response.status_code == 200, response.text
        text, rows = response.text, response.json()
    assert secret not in text, "GET /providers echoed a provider API key"
    _assert_no_secret(rows, secret)

    for row in rows:
        leaked = SECRET_KEYS & {key.lower() for key in row}
        assert leaked == set(), f"{row['provider']}: /providers exposes {sorted(leaked)}"
