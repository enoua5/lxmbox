"""
The Reticulum binding: one mailbox served on its `rnmmp.request` Destination.

The service owns the Destination and the Link lifecycle; every arriving payload is decoded,
authorized against the mailbox's device-identity list, answered through `handlers.handle`, and
the Response sent back by the same carriage rules the Exchange arrived under.
"""

from __future__ import annotations

from collections.abc import Callable

import RNS

from rnmmp_core import (
    Exchange,
    ExchangeType,
    MalformedExchangeError,
    Request,
    Response,
    ServerError,
    UnauthenticatedError,
    UnauthorizedError,
    unpack,
)

from .carriage import attach_receiver, send_exchange
from .handlers import handle
from .model import MailboxModel

__all__ = ["APP_NAME", "MailboxService"]

APP_NAME = "rnmmp"
"""The Reticulum app name; with the aspect, destinations are named `rnmmp.request`"""


class MailboxService:
    """
    One mailbox on the network.

    `authorized` is called with the sender's identity hash on every Request.
    The mailbox's authorized-user list is controlled by the caller.
    """

    def __init__(self, model: MailboxModel, identity: RNS.Identity, authorized: Callable[[bytes], bool]) -> None:
        """
        Serve `model` as the mailbox belonging to `identity`.

        Args:
            model: The mailbox to serve.
            identity: The mailbox's own `RNS.Identity`, private key included.
            authorized: Whether the given device identity hash may use this mailbox.
                Called for every Request, so keep it fast — use a cache if there are any expensive lookups.
        """
        self._model = model
        self._authorized = authorized
        self._links: dict[bytes, RNS.Link] = {}
        self._destination = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE, APP_NAME, "request")
        self._destination.set_link_established_callback(self._link_established)
        # Your ide's type checker might get tripped up here, but this is correct.
        # `Destination.hash` is a function and `Destination(...).hash` is bytes.
        self._destination_hash = bytes(self._destination.hash)

    @property
    def destination_hash(self) -> bytes:
        """The hash clients establish Links to"""
        return self._destination_hash

    def announce(self) -> None:
        """Announce the destination"""
        self._destination.announce()

    def close(self) -> None:
        """Tear down every open Link"""
        for link in list(self._links.values()):
            link.teardown()
        self._links.clear()

    ############################################################################
    # Link lifecycle
    ############################################################################

    def _link_established(self, link: RNS.Link) -> None:
        """Accept a new Link and attaches its receiver"""
        self._links[bytes(link.link_id)] = link
        link.set_link_closed_callback(self._link_closed)
        attach_receiver(link, lambda payload: self._payload_received(link, payload))

    def _link_closed(self, link: RNS.Link) -> None:
        """Forget a closed Link."""
        self._links.pop(bytes(link.link_id), None)

    ############################################################################
    # Exchange handling
    ############################################################################

    def _payload_received(self, link: RNS.Link, payload: bytes) -> None:
        """Decode, authorize and answer one arriving Exchange"""

        try:
            exchange = Exchange.decode(payload)
        except MalformedExchangeError as error:
            self._answer_malformed(link, payload, error)
            return

        if not isinstance(exchange, Request):
            # The protocol leaves open the possibility for client-initiated notifications
            # and for server-initiated requests (thus responses from clients) but neither
            # of these mechanisms are used in the current spec, so: ignore
            return

        identity = link.get_remote_identity()
        if identity is None or not identity.hash:
            response = Response.failure(exchange.request_id, UnauthenticatedError(), exchange.request_type)
        elif not self._authorized(bytes(identity.hash)):
            response = Response.failure(exchange.request_id, UnauthorizedError(), exchange.request_type)
        else:
            try:
                response = handle(self._model, exchange)
            except Exception as error:
                RNS.log(f"rnmmp request failed in handler: {error!r}", RNS.LOG_ERROR)
                response = Response.failure(exchange.request_id, ServerError(), exchange.request_type)
        send_exchange(link, response)

    def _answer_malformed(self, link: RNS.Link, payload: bytes, error: MalformedExchangeError) -> None:
        """
        Answer `MALFORMED` only when the payload is identifiable as a Request with a readable
        Request id; otherwise discard silently, as the spec requires for Link mode.
        """
        try:
            array = unpack(payload)
        except MalformedExchangeError:
            return
        if (
            isinstance(array, list)
            and len(array) >= 2
            and array[0] == ExchangeType.REQUEST
            and isinstance(array[1], int)
            and not isinstance(array[1], bool)
        ):
            send_exchange(link, Response.failure(array[1], error))
