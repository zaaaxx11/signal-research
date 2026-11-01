"""``scripts/logos-backfill.py``: the batch half of the ``/logos`` ladder.

The script is not importable by module name (the hyphen), so it is loaded from
its path. These tests are network-free: ``http_get`` is the module's one seam
and every test replaces it, which is also how the provider order and the
circuit breaker are counted.

What is pinned here:

* the two gates it mirrors from the route (``normalize_symbol``, ``safe_domain``)
  agree with ``app.api.logos`` on a corpus and on the hostile spellings, so the
  batch cannot write a filename the request half will never look for;
* image validation is by magic bytes and real dimensions — the extension is not
  consulted, an HTML page is not an image, and a 32x32 favicon is not "real";
* resume, provider order, and the per-provider circuit breaker behave as the
  docstring promises.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "logos-backfill.py"
_spec = importlib.util.spec_from_file_location("logos_backfill", SCRIPT)
assert _spec and _spec.loader
lb = importlib.util.module_from_spec(_spec)
# ``@dataclass(slots=True)`` resolves the defining module through sys.modules;
# register the module before executing it or collection dies there.
sys.modules["logos_backfill"] = lb
_spec.loader.exec_module(lb)

from app.api import logos as route_logos  # noqa: E402

sys.modules.setdefault("logos_backfill", lb)


def png(width: int = 100, height: int = 100) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"\x00" * 16
    )


def jpeg(width: int = 100, height: int = 100) -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )


def gif(width: int = 100, height: int = 100) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 16


# ---------------------------------------------------------------------------
# The two gates, against the route's own functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["AAPL", "aapl", "BRK.B", "BRK-B", "JPM/PC", "^GSPC", "BTC-USD", "000660.KS", "A"],
)
def test_normalize_symbol_matches_the_route(symbol: str) -> None:
    assert lb.normalize_symbol(symbol) == route_logos.normalize_symbol(symbol)


@pytest.mark.parametrize(
    "symbol",
    ["../../etc/passwd", "..", "A B", "A" * 200, "AAPL\n", " AAPL", ".hidden", "-AAPL", ""],
)
def test_normalize_symbol_refuses_what_the_route_refuses(symbol: str) -> None:
    with pytest.raises(Exception):
        route_logos.normalize_symbol(symbol)
    with pytest.raises(Exception):
        lb.normalize_symbol(symbol)


@pytest.mark.parametrize(
    "website",
    [
        "https://www.apple.com",
        "http://apple.com/",
        "apple.com",
        "HTTPS://WWW.APPLE.COM/investors",
        "https://user:pass@evil.example/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://printer.local/",
        "file:///etc/passwd",
        "",
        None,
    ],
)
def test_safe_domain_matches_the_route(website: object) -> None:
    assert lb.safe_domain(website) == route_logos.safe_domain(website)


# ---------------------------------------------------------------------------
# Image validation
# ---------------------------------------------------------------------------


def test_image_size_reads_every_raster_the_providers_answer_with() -> None:
    assert lb.image_size(png(100, 100)) == ("png", 100, 100)
    assert lb.image_size(gif(64, 48)) == ("gif", 64, 48)
    assert lb.image_size(jpeg(250, 250)) == ("jpeg", 250, 250)


def test_image_size_refuses_html_and_svg() -> None:
    assert lb.image_size(b"<!doctype html><html>502</html>") is None
    assert lb.image_size(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>') is None


def test_png_dimensions_are_not_taken_from_the_filename(tmp_path: Path) -> None:
    assert lb.existing_file(tmp_path / "missing", "AAPL", 48).real is False
    directory = tmp_path / "logos"
    directory.mkdir()
    (directory / "AAPL.png").write_bytes(png(32, 32))
    assert lb.existing_file(directory, "AAPL", 48).real is False, "32px is not a real logo"
    (directory / "AAPL.png").write_bytes(png(64, 64))
    assert lb.existing_file(directory, "AAPL", 48).real is True


def test_atomic_write_renames_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "logos" / "AAPL.png"
    lb.atomic_write(target, png())
    assert target.read_bytes() == png()
    assert [p.name for p in target.parent.iterdir()] == ["AAPL.png"]


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_stratified_sample_spreads_across_the_cap_range() -> None:
    targets = [lb.Target(symbol=f"S{i:03d}", market_cap=float(i + 1)) for i in range(100)]
    sample = lb.stratified_sample(targets, 20, seed=7)
    assert len(sample) == 20
    assert len({target.symbol for target in sample}) == 20
    caps = [target.market_cap for target in sample if target.market_cap]
    assert min(caps) < 20, "the small end must be represented"
    assert max(caps) > 80, "the large end must be represented"


def test_symbol_variants_fold_a_share_class() -> None:
    assert lb.symbol_variants("BRK.B") == ("BRK.B", "BRK-B")
    assert lb.symbol_variants("BRK-B") == ("BRK-B", "BRK.B")
    assert lb.symbol_variants("AAPL") == ("AAPL",)


# ---------------------------------------------------------------------------
# Resume, provider order, circuit breaker
# ---------------------------------------------------------------------------


def test_a_cached_real_file_is_skipped_without_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "logos"
    directory.mkdir()
    (directory / "AAPL.png").write_bytes(png(100, 100))

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a symbol with a real file must never be fetched")

    monkeypatch.setattr(lb, "http_get", explode)
    backfill = lb.Backfill(
        directory=directory,
        providers=("parqet", "fmp", "google"),
        timeout_s=1.0,
        max_bytes=1024,
        min_px=48,
        max_failures=8,
    )
    outcome = backfill.run_one(lb.Target(symbol="AAPL", website="https://apple.com"))

    assert outcome["provider"] == "cache"
    assert backfill.skipped == 1
    assert backfill.stats["parqet"].tried == 0


def test_the_ladder_hits_parqet_first_and_writes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(url: str, timeout_s: float, max_bytes: int) -> Any:
        calls.append(url)
        return lb.Fetch(status=200, content_type="image/png", body=png(100, 100))

    monkeypatch.setattr(lb, "http_get", fake)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet", "fmp", "google"),
        timeout_s=1.0,
        max_bytes=1024,
        min_px=48,
        max_failures=8,
    )
    outcome = backfill.run_one(lb.Target(symbol="AAPL", website="https://apple.com"))

    assert outcome["provider"] == "parqet"
    assert calls == [lb.PARQET_TEMPLATE.format(symbol="AAPL")]
    assert (tmp_path / "logos" / "AAPL.png").read_bytes() == png(100, 100)


def test_a_parqet_miss_falls_through_to_fmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(url: str, timeout_s: float, max_bytes: int) -> Any:
        calls.append(url)
        if "parqet" in url:
            return lb.Fetch(status=404)
        return lb.Fetch(status=200, content_type="image/png", body=png(100, 100))

    monkeypatch.setattr(lb, "http_get", fake)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet", "fmp", "google"),
        timeout_s=1.0,
        max_bytes=1024,
        min_px=48,
        max_failures=8,
    )
    outcome = backfill.run_one(lb.Target(symbol="AVEX"))  # no website on purpose

    assert outcome["provider"] == "fmp"
    assert calls == [
        lb.PARQET_TEMPLATE.format(symbol="AVEX"),
        lb.FMP_TEMPLATE.format(symbol="AVEX"),
    ]
    assert backfill.stats["parqet"].missing == 1
    assert backfill.stats["fmp"].real == 1


def test_a_32px_answer_is_counted_and_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(url: str, timeout_s: float, max_bytes: int) -> Any:
        return lb.Fetch(status=200, content_type="image/png", body=png(32, 32))

    monkeypatch.setattr(lb, "http_get", fake)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet",),
        timeout_s=1.0,
        max_bytes=1024,
        min_px=48,
        max_failures=8,
    )
    outcome = backfill.run_one(lb.Target(symbol="AAPL"))

    assert outcome["provider"] is None
    assert backfill.stats["parqet"].small == 1
    assert not (tmp_path / "logos" / "AAPL.png").exists()


def test_an_html_answer_is_not_an_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(url: str, timeout_s: float, max_bytes: int) -> Any:
        return lb.Fetch(status=200, content_type="text/html", body=b"<!doctype html>502</html>")

    monkeypatch.setattr(lb, "http_get", fake)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet",),
        timeout_s=1.0,
        max_bytes=1024,
        min_px=48,
        max_failures=8,
    )
    backfill.run_one(lb.Target(symbol="AAPL"))

    assert backfill.stats["parqet"].not_image == 1
    assert not (tmp_path / "logos" / "AAPL.png").exists()


def test_the_circuit_breaker_stops_a_sick_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def down(url: str, timeout_s: float, max_bytes: int) -> Any:
        calls.append(url)
        return lb.Fetch(status=0, transport_error=True, error="URLError: timed out")

    monkeypatch.setattr(lb, "http_get", down)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet",),
        timeout_s=0.1,
        max_bytes=1024,
        min_px=48,
        max_failures=3,
    )
    for index in range(10):
        backfill.run_one(lb.Target(symbol=f"S{index}"))

    assert len(calls) == 3, f"the breaker must stop the provider at 3: {len(calls)} calls"
    assert backfill.stats["parqet"].circuit == 7
    assert len(backfill.failed_symbols) == 10


def test_a_404_is_not_a_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss is an answer; it must never open the breaker."""

    def missing(url: str, timeout_s: float, max_bytes: int) -> Any:
        return lb.Fetch(status=404)

    monkeypatch.setattr(lb, "http_get", missing)
    backfill = lb.Backfill(
        directory=tmp_path / "logos",
        providers=("parqet",),
        timeout_s=0.1,
        max_bytes=1024,
        min_px=48,
        max_failures=2,
    )
    for index in range(6):
        backfill.run_one(lb.Target(symbol=f"S{index}"))

    assert backfill.stats["parqet"].tried == 6
    assert backfill.stats["parqet"].circuit == 0
    assert backfill.circuits["parqet"].is_open() is False


def test_the_universe_reader_accepts_the_depth_store_shape(tmp_path: Path) -> None:
    depth = tmp_path / "depth.json"
    depth.write_text(
        json.dumps(
            {
                "symbols": {
                    "AAPL": {"market_cap": 3e12, "website": "https://apple.com"},
                    "OTC1": {"market_cap": 1e6, "exchange": "OTC"},
                    "0700.HK": {"market_cap": 4e11},
                }
            }
        ),
        encoding="utf-8",
    )
    targets = {target.symbol: target for target in lb.universe_from_stores(depth, None)}
    assert set(targets) == {"AAPL", "0700.HK"}, "OTC is excluded, global is not"
    assert targets["AAPL"].market_cap == 3e12
    assert targets["AAPL"].website == "https://apple.com"
