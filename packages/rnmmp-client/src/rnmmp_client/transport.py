"""
The client's transport seam: something that carries Exchanges to and from a mailbox.

`LinkTransport` is the Reticulum implementation, carrying the spec's Link-mode rules — one
packet or one Resource per Exchange, both accepted inbound. The protocol exists so everything
above it can run against a fake with no RNS at all.

This is the client's own copy of the carriage rules: `rnmmp-client` and `rnmmp-server` may not
depend on each other, and `rnmmp-core` stays free of RNS, so each end carries its ~forty lines
and the integration tests exercising both ends against each other guard the drift.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import RNS

from rnmmp_core import Exchange, MalformedExchangeError

__all__ = ["ExchangeTransport", "LinkTransport"]


class ExchangeTransport(Protocol):
    """What `MailboxLink` needs from a connection"""

    def send(self, exchange: Exchange) -> None:
        """Send one Exchange to the mailbox"""
        ...

    def attach(self, on_exchange: Callable[[Exchange], None], on_closed: Callable[[], None]) -> None:
        """
        Register callbacks to report arriving echanges and transport closure.
        """
        ...

    def close(self) -> None:
        """
        Close the connection.

        Must handle being called more than once
        """
        ...

    @property
    def is_open(self) -> bool:
        """Whether the connection is still usable"""
        ...


class LinkTransport:
    """An established Reticulum Link as an `ExchangeTransport`"""

    def __init__(self, link: RNS.Link) -> None:
        """Wrap an established and identified Link"""
        self._link = link
        self._open = True
        self._emit_exchange: Callable[[Exchange], None] | None = None
        self._emit_closed: Callable[[], None] | None = None

    def send(self, exchange: Exchange) -> None:
        """Send a packet/resource Exchange to the linked server"""
        data = exchange.encode()
        if len(data) <= RNS.Link.MDU:
            RNS.Packet(self._link, data).send()
        else:
            # Constructing the Resource advertises the send automatically
            RNS.Resource(data, self._link)

    def attach(self, on_exchange: Callable[[Exchange], None], on_closed: Callable[[], None]) -> None:
        """Wire up the Link's callbacks"""
        self._emit_exchange = on_exchange
        self._emit_closed = on_closed
        self._link.set_resource_strategy(RNS.Link.ACCEPT_ALL)
        self._link.set_packet_callback(self._handle_packet_received)
        self._link.set_resource_concluded_callback(self._handle_resource_concluded)
        self._link.set_link_closed_callback(self._handle_link_closed)

    def close(self) -> None:
        """Tear the Link down"""
        if self._open:
            self._link.teardown()

    @property
    def is_open(self) -> bool:
        """Whether the Link is still up"""
        return self._open

    def _handle_packet_received(self, message: bytes, packet: RNS.Packet) -> None:
        # Packet message is the encoded Exchange
        self._handle_exchange_received(message)

    def _handle_resource_concluded(self, resource: RNS.Resource) -> None:
        if resource.status == RNS.Resource.COMPLETE and resource.data is not None:
            data = resource.data
            self._handle_exchange_received(data if isinstance(data, bytes) else data.read())

    def _handle_exchange_received(self, payload: bytes) -> None:
        """Decode and emit an Exchange"""
        try:
            exchange = Exchange.decode(payload)
        except MalformedExchangeError:
            # Tolerate invalid data from the server
            return
        if self._emit_exchange is not None:
            self._emit_exchange(exchange)

    def _handle_link_closed(self, link: RNS.Link) -> None:
        self._open = False
        if self._emit_closed is not None:
            self._emit_closed()
