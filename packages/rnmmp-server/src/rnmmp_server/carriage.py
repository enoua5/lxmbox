"""
Link-mode carriage, per the spec's "Link mode transport": one link packet or one Reticulum
Resource carries exactly one encoded Exchange, and a receiver accepts both.
"""

from __future__ import annotations

from collections.abc import Callable

import RNS

from rnmmp_core import Exchange

__all__ = ["attach_receiver", "send_exchange"]


def send_exchange(link: RNS.Link, exchange: Exchange) -> None:
    """Send one Exchange over the Link: a single packet when it fits, a Resource when it does not."""
    data = exchange.encode()
    if len(data) <= RNS.Link.MDU:
        RNS.Packet(link, data).send()
    else:
        # Constructing the Resource advertizes it automatically by default
        RNS.Resource(data, link)


def attach_receiver(link: RNS.Link, on_payload: Callable[[bytes], None]) -> None:
    """
    Wire a Link so every arriving packet payload and completed Resource is handed to `on_payload`.

    A failed Resource carries no Exchange and is dropped.
    """

    def packet_received(message: bytes, packet: RNS.Packet) -> None:
        on_payload(message)

    def resource_concluded(resource: RNS.Resource) -> None:
        if resource.status != RNS.Resource.COMPLETE or resource.data is None:
            return
        data = resource.data
        # `data` *shouldn't* be bytes here in practice,
        # but `RNS.Resource` uses `bytes` clientside, so it's good to check
        on_payload(data if isinstance(data, bytes) else data.read())

    link.set_resource_strategy(RNS.Link.ACCEPT_ALL)
    link.set_packet_callback(packet_received)
    link.set_resource_concluded_callback(resource_concluded)
