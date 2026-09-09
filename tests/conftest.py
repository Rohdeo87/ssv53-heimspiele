"""Offline by default: a forgotten fake must never reach a real device/API."""
import socket

import pytest


@pytest.fixture(autouse=True)
def forbid_test_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        # pytest's failure outcome is not an ordinary Exception, so a safety
        # controller cannot swallow an accidental real call as a normal outage.
        pytest.fail("Network access is disabled in tests; inject a fake client or sender.", pytrace=False)

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
