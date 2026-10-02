"""
The Reticulum binding: one mailbox served on its `rnmmp.request` Destination.

The service owns the Destination and the Link lifecycle; every arriving payload is decoded,
authorized against the mailbox's device-identity list, answered through `handlers.handle`, and
the Response sent back by the same carriage rules the Exchange arrived under.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import RNS

from rnmmp_core import (
    Collection,
    Exchange,
    ExchangeType,
    MalformedExchangeError,
    Notification,
    NotificationType,
    Request,
    RequestType,
    Response,
    RnmmpError,
    ServerError,
    UnauthenticatedError,
    UnauthorizedError,
    UnknownCollectionError,
    unpack,
)

from .carriage import attach_receiver, send_exchange
from .handlers import handle
from .model import MailboxModel, UpdatedStates

__all__ = ["APP_NAME", "MailboxService"]

APP_NAME = "rnmmp"
"""The Reticulum app name; with the aspect, destinations are named `rnmmp.request`"""


_SUBSCRIPTION_CHANGE_REQUESTS = frozenset({RequestType.SUBSCRIBE, RequestType.UNSUBSCRIBE})

_KNOWN_COLLECTIONS = frozenset(int(collection) for collection in Collection)


class MailboxService:
    """
    One mailbox on the network.

    `authorized` is called with the sender's identity hash on every Request.
    The mailbox's authorized-user list is controlled by the caller.
    """

    def __init__(self, model: MailboxModel, identity: RNS.Identity, check_authorized: Callable[[bytes], bool]) -> None:
        """
        Serve `model` as the mailbox belonging to `identity`.

        Args:
            model: The mailbox to serve.
            identity: The mailbox's own `RNS.Identity`, private key included.
            authorized: Whether the given device identity hash may use this mailbox.
                Called for every Request, so keep it fast — use a cache if there are any expensive lookups.
        """
        self._model = model
        """Mailbox state logic model"""
        self._check_authorized = check_authorized
        """Method that checks if the passed identity hash is authorized to use the mailbox"""
        self._links: dict[bytes, RNS.Link] = {}
        """Link id -> Link mapping"""
        self._link_mode_subscriptions: dict[bytes, set[int]] = {}
        """Link id -> subscribed collection set mapping"""
        self._link_mode_subscriptions_lock = threading.Lock()
        """Lock for modifying link-mode subscriptions"""
        model.add_change_listener(self._collections_changed)
        self._destination = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE, APP_NAME, "request")
        """rnmmp request destination"""
        self._destination.set_link_established_callback(self._link_established)
        # Your ide's type checker might get tripped up here, but this is correct.
        # `Destination.hash` is a function and `Destination(...).hash` is bytes.
        self._destination_hash = bytes(self._destination.hash)
        """rnmmp.request destination hash"""

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
        """Forget a closed Link and clean up its state"""
        link_id = bytes(link.link_id)
        # Remove link
        self._links.pop(link_id, None)
        # Remove link's subscriptions
        with self._link_mode_subscriptions_lock:
            self._link_mode_subscriptions.pop(link_id, None)

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
        elif not self._check_authorized(bytes(identity.hash)):
            response = Response.failure(exchange.request_id, UnauthorizedError(), exchange.request_type)
        else:
            try:
                if exchange.request_type in _SUBSCRIPTION_CHANGE_REQUESTS:
                    response = self._handle_subscription_change_request(link, exchange)
                else:
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

    ############################################################################
    # Subscriptions
    ############################################################################

    def _handle_subscription_change_request(self, link: RNS.Link, request: Request) -> Response:
        """
        Answer SUBSCRIBE and UNSUBSCRIBE requests

        The handlers in `handlers` only handle Single Mode subscriptions;
        this is where Link Mode subscriptions are handled, with delegation to `handlers`
        handled here as well.
        """
        try:
            destination = request.get_optional(1, bytes, name="Destination")
            if destination is not None:
                # Request for a Single Mode subscription
                # We can handle it normally
                return handle(self._model, request)

            collection = request.get_required(0, int, name="Collection")

            link_id = bytes(link.link_id)
            if request.request_type == RequestType.SUBSCRIBE:
                if collection not in _KNOWN_COLLECTIONS:
                    raise UnknownCollectionError()
                with self._link_mode_subscriptions_lock:
                    self._link_mode_subscriptions.setdefault(link_id, set()).add(collection)
            else:
                with self._link_mode_subscriptions_lock:
                    self._link_mode_subscriptions.get(link_id, set()).discard(collection)

            return Response.ok(request.request_id)

        except RnmmpError as error:
            return Response.failure(request.request_id, error, request.request_type)

    def _collections_changed(self, updated: UpdatedStates) -> None:
        """Send COLLECTION_UPDATE to every client subscribed to a Collection that changed"""

        with self._link_mode_subscriptions_lock:
            # create local copy of the subscriber list so it doesn't change on us
            link_mode_subscriptions = {
                link_id: set(collections) for link_id, collections in self._link_mode_subscriptions.items()
            }

        for link_id, collections in link_mode_subscriptions.items():
            link = self._links.get(link_id)
            if link is None:
                continue

            for collection, token_pair in updated.items():
                if collection not in collections:
                    continue

                notification = Notification(
                    NotificationType.COLLECTION_UPDATE, [collection, token_pair.previous, token_pair.new]
                )

                try:
                    send_exchange(link, notification)
                except Exception as error:
                    RNS.log(f"rnmmp COLLECTION_UPDATE not sent: {error!r}", RNS.LOG_DEBUG)
