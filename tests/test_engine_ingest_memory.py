"""The issuer memo must be a window, not a warehouse.

``_IssuerReader`` exists so two classes of one issuer (Berkshire's ``BRK-A`` and
``BRK-B``) share a single ``companyfacts`` read. It did that by holding every
payload it had ever seen for the whole load: a 100-symbol bootstrap is fine, a
4,217-symbol one reached **4.18 GB** and the cgroup OOM killer ended it.

The memo does not have to be unbounded to do its job. A repeat within a small
window is a hit; a repeat outside it falls through to ``fetch_companyfacts``,
which reads the on-disk cache and never touches the network. So the window can
be tiny and the memory ceiling is bounded by construction.

A failure is remembered as a string, which is cheap, so failures keep their
full memo — retrying a document that just failed buys nothing.
"""

from __future__ import annotations

import pytest

from app.engine import ingest


def _reader(monkeypatch: pytest.MonkeyPatch, calls: list[str], *, fail: set[str] | None = None):
    fail = fail or set()

    async def _facts(cik: str, *, client=None, cache_dir=None):  # noqa: ANN001
        calls.append(cik)
        if cik in fail:
            raise RuntimeError(f"boom {cik}")
        return {"cik": cik, "facts": {}}

    monkeypatch.setattr(ingest, "_issuer_facts", _facts)
    return ingest._IssuerReader(client=None, cache_dir=None)


def test_the_reader_does_not_keep_every_document(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    reader = _reader(monkeypatch, calls)

    for index in range(50):
        import asyncio

        asyncio.run(reader.read(f"CIK{index:010d}"))

    assert len(reader._documents) <= 8, (
        "an unbounded memo is what reached 4.18 GB on a 4,217-symbol pass; "
        f"it held {len(reader._documents)} documents"
    )


def test_a_repeat_within_the_window_is_a_memo_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls: list[str] = []
    reader = _reader(monkeypatch, calls)

    first = asyncio.run(reader.read("CIK0000320193"))
    second = asyncio.run(reader.read("CIK0000320193"))

    assert first is second
    assert calls == ["CIK0000320193"], "the second read must not hit the source"


def test_a_repeat_outside_the_window_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eviction costs a cache read, never a wrong answer."""

    import asyncio

    calls: list[str] = []
    reader = _reader(monkeypatch, calls)

    asyncio.run(reader.read("CIK0000320193"))
    for index in range(20):
        asyncio.run(reader.read(f"CIK9{index:09d}"))
    again = asyncio.run(reader.read("CIK0000320193"))

    assert again == {"cik": "CIK0000320193", "facts": {}}
    assert calls.count("CIK0000320193") == 2


def test_a_failure_is_remembered_without_holding_a_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    calls: list[str] = []
    reader = _reader(monkeypatch, calls, fail={"CIK0000000001"})

    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(reader.read("CIK0000000001"))

    assert calls == ["CIK0000000001"], "a failed document is not retried in one load"
    assert reader._documents == {}
    assert "CIK0000000001" in reader._failures
