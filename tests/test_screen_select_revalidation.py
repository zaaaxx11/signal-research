"""``POST /screens/select`` revalidates *before* it builds — the route's contract.

The route used to answer ``304`` only after it had built the whole answer: the
tag was the answer's own digest, so the comparison could not happen earlier.
Phase 7 measured the cost (``research/phase7-perf.md`` §3): **744 ms p50** on a
revalidation against a **150 ms** budget, with ``build_universe`` (235–318 ms),
the fundamentals join (295–315 ms), the facts join (104–110 ms) and
``select_rows`` (93 ms) all paid before the tag was even looked at. The ``304``
saved the body, never the work.

A revalidation is now decided from a **revision of the inputs**, computed before
:func:`app.screen.universe.build_universe`:

* ``depth=`` — the depth store file's identity: resolved path, ``mtime_ns``,
  size. Not its ``as_of``: a collector that rewrites the file in place moves
  ``mtime_ns`` even when the cycle stamp is unchanged, and a rewrite that keeps
  the file the same size must still move the tag (adversarial case (a)).
* ``snapshot=`` — the stored snapshot's id and its own ``as_of``; symbols mode
  resolves tickers through the snapshot, so its stamp is an input.
* ``scan=`` — the **generation** of the exact scan page the answer is built
  from. The cache hands the generation out with the page, so a tag can never
  name a page it did not use (adversarial case (d)).
* ``fund=`` / ``facts=`` — one row-count and newest ``updated_at`` per store.
  Both ``upsert`` implementations write ``updated_at`` on every write and both
  ``purge_expired`` delete rows, so ``(count, max(updated_at))`` moves on every
  write the stores make. A store that cannot describe itself answers a unique
  token instead of a shared one: that costs a ``200``, never a stale ``304``.
* ``logo=`` — the vendored brand-asset directory's ``mtime_ns`` and size. The
  row's ``logo`` is a URL into that directory, and adding an asset changes the
  URL a row carries.
* the folded request digest — mode, columns, criteria, sort, page (
  :func:`app.screen.selection.canonical_request`).

``as_of`` is deliberately **not** in the tag: it is the read moment, it moves on
every call, and a tag that moved with it would never match. The tag is ``W/``
for exactly that reason. What carries the *freshness* decision instead is the
scan generation: once the scan cache expires, no generation is available, the
route must build, and the new page gets a new generation — so a revalidation
after the TTL is a ``200`` (it must re-scan) and never a ``304`` over data the
server would no longer serve.

**The generation is a sequence number, not a content digest, and that is a
measurement.** A digest of the parsed page would let a re-scan that returns the
same rows keep the client's tag, which is what the old answer-digest behaviour
did. It costs 193–200 ms per scan here (5,715 rows × 68 fields, 11.4 MB of JSON
to encode and hash), which is the same order as ``build_universe`` (235–318 ms):
it would move the cost this change removes rather than delete it. A monotone
counter costs nothing and over-invalidates in exactly one situation — a client
revalidating after the five-second window gets a ``200`` it might not have
needed. ``tests/test_screen_select_revalidation.py`` measures the fast path so
that trade stays visible.

**No build coalescing, on purpose.** Two concurrent revalidations cost zero
builds (both are answered from the revision — ``test_two_concurrent…``), and
concurrent cold requests still share one *scan* through ``ScanCache`` while each
builds its own document, exactly as before. Coalescing the build would mean
sharing one mutable document between responses; the 304 path is where the
duplicate work actually was, and it is gone.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests import _screen as S

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def criteria(
    *filters: dict[str, Any],
    limit: int | None = None,
    block: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    criteria_block: dict[str, Any] = {"filters": list(filters)}
    if limit is not None:
        criteria_block["limit"] = limit
    criteria_block.update(block or {})
    return {"mode": "criteria", "region": "us", "criteria": criteria_block, **extra}


def symbols(snapshot_id: str, tickers: list[str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "symbols",
        "symbols": tickers,
        "source": "tradingview",
        "snapshot_id": snapshot_id,
    }
    body.update(overrides)
    return body


def depth_file(**symbols_: Any) -> str:
    """A depth store document in the collector's own shape."""

    return json.dumps({"as_of": "2026-10-31T09:00:00Z", "symbols": symbols_})


def use_depth_store(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Point both the reader and the revision at one file.

    The route stats the store for its revision and :func:`current_depth` reads
    it for the overlay; a test that patched only one of the two would prove
    nothing about the pair.
    """

    from app.api import screens as screens_api
    from app.screen import depth_source

    monkeypatch.setattr(screens_api, "resolve_store_path", lambda *a, **k: path)
    monkeypatch.setattr(depth_source, "resolve_store_path", lambda *a, **k: path)


class Probes:
    """Counts every step of the row build, so a ``304`` can prove it skipped them."""

    NAMES = ("build_universe", "select_rows", "enrich_rows", "enrich_facts", "overlay")

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import screens as screens_api

        self.calls = {name: 0 for name in self.NAMES}
        for name in self.NAMES:
            real = getattr(screens_api, name)

            def spy(*args: Any, _name: str = name, _real: Any = real, **kwargs: Any) -> Any:
                self.calls[_name] += 1
                return _real(*args, **kwargs)

            monkeypatch.setattr(screens_api, name, spy)

    def snapshot(self) -> dict[str, int]:
        return dict(self.calls)

    @property
    def total(self) -> int:
        return sum(self.calls.values())


# ---------------------------------------------------------------------------
# the budget: a warm revalidation answers 304 without building
# ---------------------------------------------------------------------------


async def test_a_warm_revalidation_answers_304_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 150 ms budget is met by not doing the work, not by not sending it."""

    probes = Probes(monkeypatch)
    body = criteria(limit=20)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]
        built_for_the_first_answer = probes.snapshot()
        assert sum(built_for_the_first_answer.values()) > 0, (
            "the first answer must build; a probe that never fires would make the "
            "assertion below vacuous"
        )

        started = time.perf_counter()
        warm = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        after_the_revalidation = probes.snapshot()

    assert warm.status_code == 304, warm.text
    assert warm.content == b"", "a 304 carries no body"
    delta = {
        name: after_the_revalidation[name] - built_for_the_first_answer[name]
        for name in after_the_revalidation
    }
    assert not any(delta.values()), (
        "a revalidation must not run the row build; each of these ran again: "
        f"{ {k: v for k, v in delta.items() if v} }"
    )
    assert elapsed_ms < 150.0, (
        f"a warm 304 took {elapsed_ms:.1f} ms against a 150 ms budget; the revision "
        "is supposed to cost a stat and a few store stamps, not a build"
    )
    print(
        f"\nroute: warm 304 = {elapsed_ms:.1f} ms; build calls on the 304 path = "
        f"{sum(delta.values())} {delta}"
    )


async def test_two_concurrent_revalidations_do_not_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both callers already hold the answer; neither of them builds it again."""

    probes = Probes(monkeypatch)
    body = criteria(limit=20)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        tag = first.headers["etag"]
        built = probes.total

        responses = await asyncio.gather(
            *(
                harness.client.post(
                    "/screens/select", json=body, headers={"if-none-match": tag}
                )
                for _ in range(2)
            )
        )

    assert [response.status_code for response in responses] == [304, 304], [
        response.text for response in responses
    ]
    assert probes.total == built, (
        "neither concurrent revalidation may build; there is no single-flight "
        "question to answer when the work is zero"
    )


# ---------------------------------------------------------------------------
# the six mutations: each moves the tag and answers 200
# ---------------------------------------------------------------------------


async def test_the_depth_store_file_is_a_revision_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial (a): a same-size rewrite and a bare ``touch`` both move the tag.

    ``mtime_ns`` is the point. A revision built from the collector's ``as_of``
    alone would keep answering ``304`` for a file that was rewritten in place
    with a new cycle that happens to carry the old stamp, and a revision built
    from the file's *size* would never notice a same-shape rewrite at all.
    """

    store = tmp_path / "depth.json"
    whole = depth_file(AAPL={"pe": 7.7}, NVDA={"pe": 8.8})
    store.write_text(whole)
    use_depth_store(monkeypatch, store)

    body = criteria(limit=20, columns=["symbol", "pe_ttm"])
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        assert any(row["pe_ttm"] == 7.7 for row in first.json()["rows"]), (
            "the store's cell must be in the body, or the tag below covers nothing"
        )
        tag = first.headers["etag"]

        # Same length, one digit different: a size-only revision cannot see it.
        rewritten = depth_file(AAPL={"pe": 9.9}, NVDA={"pe": 8.8})
        assert len(rewritten.encode()) == len(whole.encode()), "the sizes must match"
        store.write_text(rewritten)
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

        # Same bytes, new stamp: the collector republishing a cycle.
        stat = store.stat()
        os.utime(store, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        third = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": second.headers["etag"]}
        )

    assert second.status_code == 200, (
        "the depth store was rewritten in place at the same size; a 304 here is "
        "yesterday's rows served forever after the first warm request"
    )
    assert second.headers["etag"] != tag
    assert any(row["pe_ttm"] == 9.9 for row in second.json()["rows"])
    assert third.status_code == 200, "a republished cycle is a new store version"
    assert third.headers["etag"] not in {tag, second.headers["etag"]}


async def test_the_snapshot_stamp_is_a_revision_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symbols answer names the snapshot it resolved against, so it is an input."""

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        screen = await harness.create_screen()
        body = symbols(screen["screen_id"], ["NVDA", "AAPL", "MSFT"])
        first = await harness.client.post("/screens/select", json=body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]

        # The snapshot behind the same id is rebuilt: a new ``as_of`` under an
        # unchanged id is the case a tag over the id alone cannot see.
        with closing(S.connect_db(harness.db_path)) as connection:
            connection.execute(
                "UPDATE screens SET as_of = ? WHERE id = ?",
                ("2026-10-31T12:34:56.000000+00:00", screen["screen_id"]),
            )
            connection.commit()

        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert second.status_code == 200, (
        "the snapshot the tickers were resolved in was rebuilt; the answer dates "
        "itself from that snapshot's stamp, so the validator must move with it"
    )
    assert second.headers["etag"] != tag
    assert second.json()["snapshot_id"] == screen["screen_id"]


async def test_every_request_knob_moves_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Column, criterion, sort and page: one question, one validator."""

    variants: dict[str, dict[str, Any]] = {
        "base": criteria(limit=20),
        "columns": criteria(limit=20, columns=["symbol", "price"]),
        "criterion": criteria({"key": "price", "op": "gt", "value": 1}, limit=20),
        "sort": criteria(limit=20, block={"sort": "gainers"}),
        "page": criteria(limit=20, block={"offset": 5}),
    }

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        tags: dict[str, str] = {}
        for name, body in variants.items():
            response = await harness.client.post("/screens/select", json=body)
            assert response.status_code == 200, f"{name}: {response.text}"
            tags[name] = response.headers["etag"]

        # Every variant revalidated against the base tag: a knob that the tag
        # forgot would answer 304 with a body the client never had.
        replays = {
            name: await harness.client.post(
                "/screens/select", json=body, headers={"if-none-match": tags["base"]}
            )
            for name, body in variants.items()
            if name != "base"
        }

    assert len(set(tags.values())) == len(tags), (
        f"five questions cannot share a validator: {json.dumps(tags, indent=1)}"
    )
    for name, response in replays.items():
        assert response.status_code == 200, (
            f"`{name}` changed the answer but revalidated against the base tag "
            f"with a {response.status_code}"
        )


# ---------------------------------------------------------------------------
# the 200 path is untouched
# ---------------------------------------------------------------------------


async def test_a_request_without_a_validator_still_builds_and_ships_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes = Probes(monkeypatch)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        response = await harness.client.post("/screens/select", json=criteria(limit=5))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"] and body["returned"] == len(body["rows"])
    assert body["budget"]["bytes"] == len(response.content)
    assert response.headers["etag"].startswith('W/"')
    assert "max-age=0" in response.headers["cache-control"]
    assert response.headers["x-scan-cache"] in {"miss", "hit", "shared"}
    assert sorted(response.headers) == [
        "cache-control",
        "content-length",
        "content-type",
        "etag",
        "x-scan-cache",
    ], "a 200 ships exactly the headers it shipped before the revision moved"
    assert probes.total > 0, "no validator, no shortcut: the answer is built"


async def test_a_stale_validator_is_answered_with_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        body = criteria(limit=5)
        first = await harness.client.post("/screens/select", json=body)
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": '"select-nonsense"'}
        )
        other = await harness.client.post(
            "/screens/select",
            json=criteria(limit=5, block={"sort": "turnover"}),
            headers={"if-none-match": first.headers["etag"]},
        )

    assert second.status_code == 200 and second.json()["rows"]
    assert other.status_code == 200 and other.json()["rows"]
    assert other.headers["etag"] != first.headers["etag"]


# ---------------------------------------------------------------------------
# adversarial (d): a failure never poisons the generation
# ---------------------------------------------------------------------------


async def test_a_failed_scan_does_not_poison_the_next_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that errors stores nothing, so the warm tag still revalidates.

    The generation is handed out **with** the page it belongs to; a fetch that
    raises never reaches that hand-off. A caller whose scan failed must not be
    able to move another caller's validator.
    """

    from app.api import screens as screens_api

    async with S.screen_client(tmp_path, monkeypatch) as harness:
        warm_body = criteria(limit=5)
        first = await harness.client.post("/screens/select", json=warm_body)
        assert first.status_code == 200, first.text
        tag = first.headers["etag"]

        real_fetch = screens_api.fetch_scan

        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise screens_api.ScanError("provider refused")

        monkeypatch.setattr(screens_api, "fetch_scan", refuse)
        failed = await harness.client.post(
            "/screens/select",
            json={**criteria(limit=5), "region": "global"},
        )
        monkeypatch.setattr(screens_api, "fetch_scan", real_fetch)
        assert failed.status_code == 503, failed.text

        warm = await harness.client.post(
            "/screens/select", json=warm_body, headers={"if-none-match": tag}
        )

    assert warm.status_code == 304, (
        "a failure in another request must not invalidate a warm validator; "
        f"got {warm.status_code} — the generation moved without a page"
    )
    assert warm.headers["etag"] == tag


# ---------------------------------------------------------------------------
# the brand-asset lane is a revision input, and it is the same directory
# ---------------------------------------------------------------------------


def test_the_brand_directory_is_the_one_logo_for_reads() -> None:
    """One directory, two readers: a copy of the path is a drift waiting to happen."""

    from app.api import screens as screens_api
    from app.branding.logos import _UI_LOGO_DIR

    assert screens_api.BRAND_DIR == _UI_LOGO_DIR, (
        "the revision stats a directory; if that is not the directory `logo_for` "
        "resolves against, adding a brand asset keeps answering 304 with the old URL"
    )


async def test_adding_a_brand_asset_moves_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import screens as screens_api

    brand_dir = tmp_path / "logos"
    brand_dir.mkdir()
    (brand_dir / "AAPL.png").write_bytes(b"png")
    monkeypatch.setattr(screens_api, "BRAND_DIR", brand_dir)

    body = criteria(limit=3)
    async with S.screen_client(tmp_path, monkeypatch) as harness:
        first = await harness.client.post("/screens/select", json=body)
        tag = first.headers["etag"]
        (brand_dir / "NVDA.png").write_bytes(b"png")
        second = await harness.client.post(
            "/screens/select", json=body, headers={"if-none-match": tag}
        )

    assert second.status_code == 200, (
        "the row's `logo` is a URL into this directory; a new asset changes the URL "
        "it resolves to"
    )
    assert second.headers["etag"] != tag
