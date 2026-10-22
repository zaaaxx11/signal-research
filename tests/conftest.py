"""Suite-wide guard: the test process may not open an outbound connection.

Phase-0 gate #5 (02 §5): the fixture blocks sockets **and the test fails if a
connection is attempted**. Two layers:

1. ``socket.socket.connect`` / ``connect_ex`` / ``socket.create_connection``
   raise ``RuntimeError`` and record the attempt in a per-test ledger;
2. ``socket.getaddrinfo`` is blocked for non-local hosts, so DNS lookups are
   caught even if a library resolves before connecting.

AF_UNIX and loopback are allowed (SQLite, local IPC). A test that intentionally
proves the guard bites must be marked ``@pytest.mark.egress_probe``; its ledger
is expected to be non-empty and is cleared before teardown.
"""

from __future__ import annotations

import socket
from typing import Iterator

import pytest

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "", None})
_AF_UNIX = getattr(socket, "AF_UNIX", None)


@pytest.fixture(autouse=True)
def block_outbound_network(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[list[str]]:
    """Block egress for every test; fail the test if anything tried to leave."""

    ledger: list[str] = []

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _record(kind: str, target: object) -> None:
        ledger.append(f"{kind}:{target!r}")

    def guarded_connect(self: socket.socket, address: object, *args: object, **kwargs: object) -> object:
        if _AF_UNIX is not None and self.family == _AF_UNIX:
            return real_connect(self, address, *args, **kwargs)  # type: ignore[arg-type]
        _record("connect", address)
        raise RuntimeError(
            f"network access blocked by tests/conftest.py: connect({address!r}). "
            "Phase-0 gate #5 requires a zero-egress suite; use MockTransport or the fake provider."
        )

    def guarded_connect_ex(self: socket.socket, address: object, *args: object, **kwargs: object) -> object:
        if _AF_UNIX is not None and self.family == _AF_UNIX:
            return real_connect_ex(self, address, *args, **kwargs)  # type: ignore[arg-type]
        _record("connect_ex", address)
        raise RuntimeError(
            f"network access blocked by tests/conftest.py: connect_ex({address!r})."
        )

    def guarded_create_connection(address: object, *args: object, **kwargs: object) -> object:
        _record("create_connection", address)
        raise RuntimeError(
            f"network access blocked by tests/conftest.py: create_connection({address!r})."
        )

    def guarded_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> object:
        if host in _LOCAL_HOSTS:
            return real_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]
        _record("getaddrinfo", (host, port))
        raise RuntimeError(
            f"network access blocked by tests/conftest.py: DNS lookup for {host!r}."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex, raising=True)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection, raising=True)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo, raising=True)

    yield ledger

    expected_probe = request.node.get_closest_marker("egress_probe") is not None
    if ledger and not expected_probe:
        pytest.fail(
            "test attempted outbound network access (Phase-0 gate #5): "
            + ", ".join(ledger)
        )
    ledger.clear()
