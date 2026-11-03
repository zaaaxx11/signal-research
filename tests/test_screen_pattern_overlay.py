"""Pattern overlay tests — bridge between pattern_detector and Signal.accepts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.screen import pattern_overlay


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


class TestLookup:
    """``lookup`` reads the module-level ``_current`` dict."""

    def test_lookup_true_when_pattern_present(self) -> None:
        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["hammer", "doji"]
        assert pattern_overlay.lookup("AAPL", "hammer") is True
        assert pattern_overlay.lookup("AAPL", "doji") is True

    def test_lookup_false_when_pattern_absent(self) -> None:
        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["hammer"]
        assert pattern_overlay.lookup("AAPL", "doji") is False

    def test_lookup_false_when_symbol_not_in_map(self) -> None:
        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["hammer"]
        assert pattern_overlay.lookup("MSFT", "hammer") is False

    def test_lookup_false_when_map_empty(self) -> None:
        pattern_overlay._current.clear()
        assert pattern_overlay.lookup("AAPL", "hammer") is False

    def test_lookup_case_insensitive_symbol(self) -> None:
        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["hammer"]
        assert pattern_overlay.lookup("aapl", "hammer") is True


# ---------------------------------------------------------------------------
# detect_patterns
# ---------------------------------------------------------------------------


class TestDetectPatterns:
    """``detect_patterns`` runs the real detector and populates ``_current``."""

    def test_detect_populates_current(self) -> None:
        pattern_overlay._current.clear()
        cache_dir = Path("/tmp/fake_cache")

        with (
            patch("app.market.barstore.BarStore") as mock_store_cls,
            patch("app.market.pattern_detector.detect") as mock_detect,
        ):
            mock_store = mock_store_cls.return_value
            mock_store.symbols.return_value = ["AAPL", "MSFT"]
            mock_detect.return_value = {
                "AAPL": ["hammer", "doji"],
                "MSFT": [],
            }

            result = pattern_overlay.detect_patterns(cache_dir)

        mock_store_cls.assert_called_once_with(cache_dir)
        mock_detect.assert_called_once_with(["AAPL", "MSFT"], cache_dir=cache_dir)

        assert result == {"AAPL": ["hammer", "doji"], "MSFT": []}
        assert pattern_overlay._current == {"AAPL": ["hammer", "doji"], "MSFT": []}
        assert pattern_overlay.lookup("AAPL", "hammer") is True
        assert pattern_overlay.lookup("MSFT", "hammer") is False

    def test_detect_overwrites_previous_run(self) -> None:
        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["doji"]
        cache_dir = Path("/tmp/fake_cache")

        with (
            patch("app.market.barstore.BarStore") as mock_store_cls,
            patch("app.market.pattern_detector.detect") as mock_detect,
        ):
            mock_store = mock_store_cls.return_value
            mock_store.symbols.return_value = ["GOOG"]
            mock_detect.return_value = {"GOOG": ["hammer"]}

            pattern_overlay.detect_patterns(cache_dir)

        assert pattern_overlay._current == {"GOOG": ["hammer"]}
        assert pattern_overlay.lookup("AAPL", "doji") is False


# ---------------------------------------------------------------------------
# Signal.accepts integration
# ---------------------------------------------------------------------------


class TestSignalAccepts:
    """A pattern Signal's ``accepts`` callable answers through ``lookup``."""

    def test_accepts_returns_true_when_pattern_detected(self) -> None:
        from app.screen.descriptive import BY_ID

        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["wedgeup", "doji"]

        wedgeup = BY_ID["wedgeup"]
        assert wedgeup.available is True
        assert wedgeup.reason is None
        assert wedgeup.accepts({"symbol": "AAPL"}) is True

    def test_accepts_returns_false_when_pattern_not_detected(self) -> None:
        from app.screen.descriptive import BY_ID

        pattern_overlay._current.clear()
        pattern_overlay._current["AAPL"] = ["hammer"]

        wedgeup = BY_ID["wedgeup"]
        assert wedgeup.accepts({"symbol": "AAPL"}) is False

    def test_accepts_returns_false_when_symbol_not_in_map(self) -> None:
        from app.screen.descriptive import BY_ID

        pattern_overlay._current.clear()

        wedgeup = BY_ID["wedgeup"]
        assert wedgeup.accepts({"symbol": "MSFT"}) is False

    def test_all_twelve_pattern_signals_are_available(self) -> None:
        from app.screen.descriptive import BY_ID

        pattern_ids = [
            "wedgeup", "wedge", "wedgedown",
            "tri-ascending", "tri-descending",
            "channelup", "channel", "channeldown",
            "doubletop", "doublebottom",
            "head-shoulders", "head-shoulders-inverse",
        ]
        for pid in pattern_ids:
            signal = BY_ID[pid]
            assert signal.available is True, f"{pid} is not available"
            assert signal.reason is None, f"{pid} carries a reason"
            assert callable(signal.accepts), f"{pid} accepts is not callable"