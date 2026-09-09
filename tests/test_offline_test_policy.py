import socket

import pytest


def test_accidental_sender_network_is_a_test_failure_not_a_recoverable_outage():
    with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
        try:
            socket.create_connection(("example.invalid", 443))
        except Exception:
            pytest.fail("The network violation was swallowed by ordinary error handling.")


def test_direct_socket_cannot_bypass_the_test_policy():
    with socket.socket() as connection:
        with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
            connection.connect(("127.0.0.1", 443))
