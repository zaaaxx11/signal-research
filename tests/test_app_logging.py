"""The application must actually let its own diagnostics out.

Every operational message in this codebase — a source that failed, a cycle that
overran, the per-source duration that makes "cycle <= 60 minutes" observable —
is written with `logger.info` on a `mee.*` logger. Under uvicorn nothing
configured that tree, so the root logger stayed at WARNING and every one of
them was dropped. A deployment could be degraded for days and the journal would
show nothing but HTTP access lines: exactly the failure mode that hid a stale
snapshot digest.

`_configure_logging` sets the level from `MEE_LOG_LEVEL` (default INFO), gives
the tree one handler with a stable format, and stays idempotent so a test that
builds several apps does not stack handlers.
"""

from __future__ import annotations

import logging

import pytest

from app import main as app_main


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    logger = logging.getLogger("mee")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    saved_flag = getattr(app_main, "_LOG_CONFIGURED", False)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    app_main._LOG_CONFIGURED = False
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    for handler in saved_handlers:
        logger.addHandler(handler)
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate
    app_main._LOG_CONFIGURED = saved_flag


def test_the_mee_tree_is_set_to_info_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEE_LOG_LEVEL", raising=False)
    app_main._configure_logging()
    assert logging.getLogger("mee").level == logging.INFO


def test_the_level_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEE_LOG_LEVEL", "debug")
    app_main._configure_logging()
    assert logging.getLogger("mee").level == logging.DEBUG


def test_a_bogus_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEE_LOG_LEVEL", "shouty")
    app_main._configure_logging()
    assert logging.getLogger("mee").level == logging.INFO


def test_configuring_twice_does_not_stack_handlers() -> None:
    app_main._configure_logging()
    app_main._configure_logging()
    assert len(logging.getLogger("mee").handlers) == 1


def test_an_info_message_reaches_the_handler() -> None:
    """The regression this exists for: INFO must not vanish."""

    app_main._configure_logging()

    captured: list[logging.LogRecord] = []
    sink = logging.Handler()
    sink.emit = captured.append  # type: ignore[method-assign]
    logger = logging.getLogger("mee")
    logger.addHandler(sink)
    try:
        logging.getLogger("mee.depth").info("depth source sec done in 1.0s")
    finally:
        logger.removeHandler(sink)

    assert any(
        "depth source sec done in" in record.getMessage() for record in captured
    )
