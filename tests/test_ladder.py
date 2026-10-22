"""Phase-2 RED: capability-scoped ladder, skip semantics, secrets in logs.

Gate 2 (02 §5 Fase 2): ``int(L<n>) == index``; no paid rung before a free one;
a rung without a key is ``skipped`` with ``attempts=0`` **and zero requests**
(asserted on the ``MockTransport`` request ledger, not on a mock call count).

Gate 7: ``api_key`` and credential-bearing URLs never reach the log.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.contracts import Capability
from app.providers.base import Verdict
from app.providers.registry import REGISTRY, ladder_for
from tests._phase2 import (
    ATTEMPT_KEYS,
    FixtureAdapter,
    capture_mee_logs,
    clear_provider_keys,
    finnhub_quote,
    first_quote_spec,
    instrument,
    keyed_quote_specs,
    live_client,
    make_budget,
    make_ladder,
    mock_web,
    spec,
)


def _blocked() -> Verdict:
    return Verdict(ok=False, kind="blocked", detail="200 HTML challenge")


def _adapters_for(specs: Any) -> dict[str, FixtureAdapter]:
    return {entry.name: FixtureAdapter(entry.name) for entry in specs}


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    """Everything the logging framework captured, including ``extra`` fields."""

    parts = [caplog.text]
    for record in caplog.records:
        parts.append(str(record.__dict__))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Ordering: labels, indexes, cost tiers
# ---------------------------------------------------------------------------


def test_rung_labels_match_their_index_and_the_registry_order() -> None:
    ladder = make_ladder(specs=REGISTRY, adapters=_adapters_for(REGISTRY))

    for capability in Capability:
        rungs = ladder.rungs(capability)
        expected = ladder_for(capability)
        assert [rung.spec.name for rung in rungs] == [entry.name for entry in expected], (
            f"{capability.value}: the ladder reorders the registry"
        )
        # 02 §3.4: L0 is the cache/store and is NOT a provider rung, so the
        # provider ladder is labelled L1..L<n> and ``int(L<n>) == index``.
        assert [rung.index for rung in rungs] == list(range(1, len(rungs) + 1)), (
            f"{capability.value}: rung.index must be 1..n (L0 is the cache)"
        )
        assert [rung.label for rung in rungs] == [
            f"L{index}" for index in range(1, len(rungs) + 1)
        ], (
            f"{capability.value}: labels must be L1..L<n>, got "
            f"{[rung.label for rung in rungs]}"
        )
        assert [int(rung.label[1:]) for rung in rungs] == [rung.index for rung in rungs]


def test_no_paid_rung_precedes_a_free_one() -> None:
    ladder = make_ladder(specs=REGISTRY, adapters=_adapters_for(REGISTRY))
    order = {"T0": 0, "T1": 1, "T2": 2}

    for capability in Capability:
        rungs = ladder.rungs(capability)
        assert rungs, f"{capability.value}: no rung"
        tiers = [order[rung.spec.cost_tier] for rung in rungs]
        assert tiers == sorted(tiers), (
            f"{capability.value}: a paid rung precedes a cheaper one: "
            + " -> ".join(f"{r.spec.name}({r.spec.cost_tier})" for r in rungs)
        )
        assert rungs[0].spec.cost_tier == "T0", (
            f"{capability.value}: the first rung must be free, got "
            f"{rungs[0].spec.name}({rungs[0].spec.cost_tier})"
        )


# ---------------------------------------------------------------------------
# is_available: key, support and budget are skips, never attempts
# ---------------------------------------------------------------------------


async def test_keyless_rung_is_skipped_with_zero_requests(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    for needle in ("yahoo", "stooq", "nasdaq"):
        mock_web.html(needle)
    clear_provider_keys(monkeypatch)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        ladder = harness.ladder()
        result = await ladder.fetch(Capability.QUOTE, instrument("AAPL"), {"last_price"})

    rungs = ladder_for(Capability.QUOTE)
    assert len(rungs) >= 2, f"QUOTE ladder has {len(rungs)} rung(s)"
    assert {attempt["provider"] for attempt in result.attempts} == {entry.name for entry in rungs}, (
        "the ladder must record every rung it considered; recorded "
        f"{[attempt['provider'] for attempt in result.attempts]}"
    )
    assert all(ATTEMPT_KEYS <= set(attempt) for attempt in result.attempts), (
        f"attempt records must carry {sorted(ATTEMPT_KEYS)}"
    )

    keyed = [entry for entry in rungs if entry.auth is not None]
    assert keyed, "no keyed QUOTE rung is declared: gate 2 has nothing to skip"
    for entry in keyed:
        attempt = next(item for item in result.attempts if item["provider"] == entry.name)
        if entry.cost_tier != "T1":
            # A T2 rung may be skipped by the default credit budget as well;
            # both are skips and neither may reach the transport.
            assert attempt["attempts"] == 0 and attempt["requests"] == 0
            assert mock_web.count(entry.name) == 0
            continue
        assert attempt["verdict"] == "skipped", (
            f"{entry.name} has no {entry.auth} in this process and must be skipped, "
            f"got {attempt['verdict']!r}"
        )
        assert attempt["attempts"] == 0, f"{entry.name}: a skipped rung is not an attempt"
        assert attempt["requests"] == 0, f"{entry.name}: a skipped rung sends no request"
        assert mock_web.count(entry.name) == 0, (
            f"{entry.name} was skipped but the transport saw {mock_web.count(entry.name)} "
            "request(s); is_available() must run before fetch()"
        )


async def test_unsupported_instrument_is_skipped_without_a_request() -> None:
    alpha = FixtureAdapter("alpha")
    ladder = make_ladder(
        specs=(spec("alpha", "T0", supports=lambda _asset, mic: mic == "XNAS"),),
        adapters={"alpha": alpha},
        budget=make_budget(requests=10, credits=0, seconds=60),
    )

    result = await ladder.fetch(Capability.QUOTE, instrument("BBCA", mic="XIDX"), {"last_price"})

    assert alpha.calls == [], "supports(asset_class, mic) must gate fetch()"
    assert result.attempts[0]["verdict"] == "skipped"
    assert result.attempts[0]["attempts"] == 0
    assert result.attempts[0]["requests"] == 0
    assert result.observations == []


async def test_anonymous_rungs_send_no_authorization_header(
    tmp_path: Any, monkeypatch: Any, mock_web: Any
) -> None:
    from tests._phase2 import yahoo_route

    yahoo_route(mock_web)
    clear_provider_keys(monkeypatch)

    async with live_client(tmp_path, monkeypatch, mock_web) as harness:
        ladder = harness.ladder()
        result = await ladder.fetch(Capability.QUOTE, instrument("AAPL"), {"last_price"})

    assert result.verdict.ok, f"the first rung should have served AAPL: {result.attempts}"
    assert mock_web.requests, "no provider request was made"
    for record in mock_web.requests:
        assert "authorization" not in {key.lower() for key in record.headers}, (
            f"anonymous rung sent Authorization to {record.host}"
        )
        assert "x-api-key" not in {key.lower() for key in record.headers}, (
            "the client's API key must never be forwarded to a provider"
        )


# ---------------------------------------------------------------------------
# Budget: exhaustion is a skip, never a stop
# ---------------------------------------------------------------------------


async def test_exhausted_credit_budget_skips_paid_rung_and_falls_through() -> None:
    alpha = FixtureAdapter(
        "alpha", default=_blocked(), verdicts={"CCC": [Verdict(ok=True, kind="ok")]}
    )
    beta = FixtureAdapter("beta", default=_blocked())
    gamma = FixtureAdapter("gamma")  # T2, "paid": one credit per request
    ladder = make_ladder(
        specs=(spec("alpha", "T0"), spec("beta", "T0"), spec("gamma", "T2")),
        adapters={"alpha": alpha, "beta": beta, "gamma": gamma},
        budget=make_budget(requests=10, credits=1, seconds=60),
    )

    first = await ladder.fetch(Capability.QUOTE, instrument("AAA"), {"last_price"})
    assert first.verdict.ok, f"the paid rung should have served AAA: {first.attempts}"
    assert [attempt["rung"] for attempt in first.attempts] == ["L1", "L2", "L3"]
    assert [attempt["verdict"] for attempt in first.attempts] == ["blocked", "blocked", "ok"]
    assert gamma.calls == ["AAA"]

    second = await ladder.fetch(Capability.QUOTE, instrument("BBB"), {"last_price"})
    assert [attempt["verdict"] for attempt in second.attempts] == [
        "blocked",
        "blocked",
        "skipped_by_budget",
    ], "an exhausted credit budget must skip the T2 rung, not abort the walk"
    skipped = second.attempts[-1]
    assert (skipped["attempts"], skipped["requests"]) == (0, 0)
    assert second.skipped_by_budget == 1
    assert gamma.calls == ["AAA"], (
        "the paid adapter was called after the credit budget was exhausted: "
        f"{gamma.calls}"
    )

    # A later instrument whose first free rung works is still served: the skip
    # belongs to one rung, not to the ladder.
    third = await ladder.fetch(Capability.QUOTE, instrument("CCC"), {"last_price"})
    assert third.verdict.ok, f"budget exhaustion poisoned later fetches: {third.attempts}"
    assert gamma.calls == ["AAA"]


async def test_zero_request_budget_skips_every_rung_without_a_call() -> None:
    alpha = FixtureAdapter("alpha")
    beta = FixtureAdapter("beta")
    ladder = make_ladder(
        specs=(spec("alpha", "T0"), spec("beta", "T0")),
        adapters={"alpha": alpha, "beta": beta},
        budget=make_budget(requests=0, credits=0, seconds=60),
    )

    result = await ladder.fetch(Capability.QUOTE, instrument("AAA"), {"last_price"})

    assert [attempt["verdict"] for attempt in result.attempts] == [
        "skipped_by_budget",
        "skipped_by_budget",
    ]
    assert all(attempt["attempts"] == 0 for attempt in result.attempts)
    assert all(attempt["requests"] == 0 for attempt in result.attempts)
    assert result.skipped_by_budget == 2
    assert alpha.calls == [] and beta.calls == []
    assert result.verdict.ok is False


# ---------------------------------------------------------------------------
# Gate 7: credentials never reach a log line
# ---------------------------------------------------------------------------


async def test_provider_key_never_appears_in_logs(
    tmp_path: Any, monkeypatch: Any, mock_web: Any, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "fh-key-9182-do-not-log"
    keyed = keyed_quote_specs()
    assert keyed, "no keyed QUOTE rung: gate 7 has no credential to protect"
    subject = keyed[0]

    for needle in ("yahoo", "stooq", "nasdaq"):
        mock_web.html(needle)
    mock_web.json(subject.name.split("_", 1)[0], finnhub_quote())

    caplog.set_level(logging.DEBUG)
    with capture_mee_logs() as logs:
        async with live_client(
            tmp_path, monkeypatch, mock_web, keys={subject.auth: secret}
        ) as harness:
            ladder = harness.ladder()
            await ladder.fetch(Capability.QUOTE, instrument("AAPL"), {"last_price"})

    assert mock_web.requests_for(subject.name), (
        f"{subject.name} was never called although {subject.auth} was set; the "
        "logging assertion below would be vacuous"
    )
    blob = _log_blob(caplog) + "\n" + logs.text
    assert secret not in blob, f"provider credential leaked into the log: {subject.name}"
    for record in mock_web.requests_for(subject.name):
        if secret in record.url:
            assert record.url not in blob, "a credential-bearing provider URL was logged"
        for value in record.headers.values():
            if secret in value:
                assert value not in blob, "a credential-bearing header was logged"

    assert logs.records, (
        "no structured log line was emitted for the provider walk (02 §3.10); "
        "gate 7's redaction assertion is only meaningful when something is logged"
    )
    assert all(record.name.startswith("mee") for record in logs.records), (
        f"provider logs must use the `mee.*` namespace, got {[r.name for r in logs.records]}"
    )


async def test_client_api_key_never_appears_in_logs(
    tmp_path: Any, monkeypatch: Any, mock_web: Any, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "client-key-5510-do-not-log"
    from tests._phase2 import yahoo_route

    yahoo_route(mock_web)
    caplog.set_level(logging.DEBUG)

    with capture_mee_logs() as logs:
        async with live_client(tmp_path, monkeypatch, mock_web, api_key=secret) as harness:
            job = await harness.run_job(["AAPL"])

    assert mock_web.count("yahoo") >= 1, "the live ladder did not run: nothing to redact"
    assert job["status"] == "Success", job
    assert logs.records, "the job made provider requests but emitted no `mee.*` log line"
    assert secret not in _log_blob(caplog) + "\n" + logs.text, (
        "the client API key leaked into the log"
    )
