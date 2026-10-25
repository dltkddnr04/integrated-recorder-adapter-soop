# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 source.soop contributors
from tools.live_fault_inject import ParentOnlyFaultNetwork
from soop_grid.protocol import parent_packet


class FakeSocket:
    def __init__(self):
        self.closed = False
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, _timeout):
        pass

    def shutdown(self, _how):
        pass

    def close(self):
        self.closed = True

    def fileno(self):
        return -1 if self.closed else 42


def test_fault_injector_tracks_and_closes_only_parent_handshake_socket():
    events = []
    network = ParentOnlyFaultNetwork(lambda name, **fields: events.append((name, fields)))
    gateway = FakeSocket()
    parent = FakeSocket()
    network._sockets.update((gateway, parent))
    network._addresses[gateway] = network._identify(("192.0.2.1", 80))
    network._addresses[parent] = network._identify(("198.51.100.2", 443))

    network.send(gateway, b"\0" * 16)
    assert network.parent_socket is None
    network.send(parent, parent_packet(0xCB2E, b"synthetic"))
    identity = network.active_parent_id

    assert network.close_active_parent() == identity
    assert parent.closed
    assert not gateway.closed
    assert events == [("local_parent_socket_closed", {"parent_endpoint_id": identity})]
    network.close()
    assert gateway.closed
