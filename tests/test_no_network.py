"""The no-network guard must actually bite (02 §5 gate #5)."""

from __future__ import annotations

import socket

import pytest


@pytest.mark.egress_probe
def test_outbound_tcp_connect_is_blocked(block_outbound_network: list[str]) -> None:
    with pytest.raises(RuntimeError, match="network access blocked"):
        socket.create_connection(("example.com", 80), timeout=0.1)

    assert block_outbound_network, "the guard must record the attempt"
    assert block_outbound_network[0].startswith("create_connection:")


@pytest.mark.egress_probe
def test_dns_lookup_for_a_remote_host_is_blocked(block_outbound_network: list[str]) -> None:
    with pytest.raises(RuntimeError, match="DNS lookup"):
        socket.getaddrinfo("example.com", 443)

    assert any(entry.startswith("getaddrinfo:") for entry in block_outbound_network)


def test_no_egress_was_attempted_by_the_preceding_imports() -> None:
    # The autouse fixture fails the test at teardown if anything tried to leave;
    # this test simply documents that importing the app is a network-free act.
    import app.main  # noqa: F401
