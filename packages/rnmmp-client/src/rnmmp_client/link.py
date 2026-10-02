"""
A client's connection to a mailbox.

`connect` establishes the link, and `MailboxLink` implements usage.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Final

import RNS

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    ConflictingFiltersError,
    Exchange,
    MalformedExchangeError,
    Notification,
    NotificationType,
    Request,
    RequestType,
    Response,
    SearchTitleParam,
    UnknownStateError,
    UploadParam,
    WriteParam,
)

from .response_types import (
    Capabilities,
    CollectionDelta,
    CollectionSync,
    CollectionUpdate,
    CreatedTags,
    SingleModeSubscription,
    TokenChange,
    UpdatedStates,
    UploadResult,
)
from .transport import ExchangeTransport, LinkTransport

__all__ = [
    "MailboxLink",
    "NoAnswer",
    "Unreachable",
    "UnreachableReason",
    "VoidAnswer",
    "connect",
]

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: Final = 30.0
"""Seconds to wait for a Response before answering `NoAnswer`"""


_KNOWN_NOTIFICATION_TYPES: Final = (NotificationType.COLLECTION_UPDATE,)


class UnreachableReason(Enum):
    """Why a mailbox could not be reached"""

    NO_PATH = auto()
    """No path to the destination found in time"""
    NO_IDENTITY = auto()
    """The destination's identity is not known, so no Link can be requested"""
    LINK_TIMEOUT = auto()
    """The Link was requested but never established"""


@dataclass(frozen=True, slots=True)
class Unreachable:
    """
    The mailbox could not be reached

    NOTE: implemented as a value instead of an exception to encourage better explicit handling
    """

    reason: UnreachableReason


@dataclass(frozen=True, slots=True)
class NoAnswer:
    """No Response arrived in time; the caller decides whether to retry, back off, or give up"""


@dataclass(frozen=True, slots=True)
class VoidAnswer:
    """A contentless response, distinct from NoAnswer since a response *did* come back"""


def connect(
    destination_hash: bytes,
    identity: RNS.Identity,
    *,
    path_timeout: float = 15.0,
    link_timeout: float = 15.0,
    timeout: float = DEFAULT_TIMEOUT,
    on_collection_update: Callable[[CollectionUpdate], None] | None = None,
) -> MailboxLink | Unreachable:
    """
    Establish an identified Link to a mailbox's `rnmmp.request` destination

    Args:
        destination_hash: The mailbox destination to reach.
        identity: The device identity to identify with
        path_timeout: Seconds to wait for a path to be found
        link_timeout: Seconds to wait for the Link to establish
        timeout: The default per-request timeout for the resulting `MailboxLink`
        on_collection_update: Called with each COLLECTION_UPDATE the server sends
    """
    if not RNS.Transport.has_path(destination_hash):
        RNS.Transport.request_path(destination_hash)
        deadline = time.monotonic() + path_timeout
        while not RNS.Transport.has_path(destination_hash):
            if time.monotonic() >= deadline:
                return Unreachable(UnreachableReason.NO_PATH)
            time.sleep(0.1)

    server_identity = RNS.Identity.recall(destination_hash)
    if server_identity is None:
        return Unreachable(UnreachableReason.NO_IDENTITY)

    destination = RNS.Destination(server_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnmmp", "request")
    established = threading.Event()
    link = RNS.Link(destination, established_callback=lambda _link: established.set())
    if not established.wait(link_timeout):
        link.teardown()
        return Unreachable(UnreachableReason.LINK_TIMEOUT)
    link.identify(identity)
    return MailboxLink(LinkTransport(link), default_timeout=timeout, on_collection_update=on_collection_update)


class MailboxLink:
    """A mailbox connected over the transport"""

    def __init__(
        self,
        transport: ExchangeTransport,
        *,
        default_timeout: float = DEFAULT_TIMEOUT,
        on_collection_update: Callable[[CollectionUpdate], None] | None = None,
    ) -> None:
        """Attach to an already-established transport"""
        self._transport = transport
        """Handler for carrying communication to and from the server"""
        self._default_timeout = default_timeout
        """Default time to wait for server response"""
        self.on_collection_update = on_collection_update
        """Called with each COLLECTION_UPDATE the server sends"""
        self._state_lock = threading.Lock()
        """
        Lock for access to shared state

        Also used to suspend a request until a reponse is received or timed out
        """
        self._response_arrived = threading.Condition(self._state_lock)
        """Condition for releasing locks on requests waiting for a response"""
        self._responses: dict[int, Response] = {}
        """
        Request id -> response mapping for responses received,
        awaiting the requesting thread to read
        """
        self._outstanding_request_types: dict[int, int] = {}
        """Request id -> request type mapping for pending requests"""
        self._next_request_id = 0
        """Counter for next request id to use"""
        transport.attach(self._exchange_received, self._transport_closed)

    @property
    def is_open(self) -> bool:
        """Whether the transport is still usable"""
        return self._transport.is_open

    def close(self) -> None:
        """
        Close the transport

        Outstanding requests will resolve to `NoAnswer`
        """
        self._transport.close()

    ############################################################################
    # The generic exchange mechanisms
    ############################################################################

    def send_exchange(self, request: Request, *, timeout: float | None = None) -> Response | NoAnswer:
        """Send a Request and wait for its Response"""

        with self._state_lock:
            self._outstanding_request_types[request.request_id] = int(request.request_type)

        self._transport.send(request)

        deadline = time.monotonic() + (self._default_timeout if timeout is None else timeout)
        with self._state_lock:
            while request.request_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._transport.is_open:
                    # Timed out or closed, clear the wait
                    self._outstanding_request_types.pop(request.request_id, None)
                    return NoAnswer()
                # Release lock and wait for the next response to come in,
                # or the timeout if that comes first
                self._response_arrived.wait(remaining)
            self._outstanding_request_types.pop(request.request_id, None)
            return self._responses.pop(request.request_id)

    def _exchange_received(self, exchange: Exchange) -> None:
        """Match Responses to outstanding Requests and bubble Notifications"""

        if isinstance(exchange, Notification):
            self._notification_received(exchange)
            return

        if isinstance(exchange, Request):
            # Server-initiated Requests are not part of the current spec
            # We could optionally respond with an error
            # But ignoring it is up to spec
            return

        if isinstance(exchange, Response):
            with self._state_lock:
                # If it's not in outstanding, it probably already timed out
                # so we gain nothing by reporting it — just ignore it
                if exchange.request_id in self._outstanding_request_types:
                    self._responses[exchange.request_id] = exchange
                    self._response_arrived.notify_all()

    def _notification_received(self, notification: Notification) -> None:
        """Handle an incoming Notification"""
        if notification.event_type not in _KNOWN_NOTIFICATION_TYPES:
            return

        if notification.event_type == NotificationType.COLLECTION_UPDATE:
            handler = self.on_collection_update
            if handler is None:
                return

            parameters = notification.parameters
            if (
                len(parameters) < 3
                or not isinstance(parameters[0], int)
                or isinstance(parameters[0], bool)
                or not isinstance(parameters[1], bytes)
                or not isinstance(parameters[2], bytes)
            ):
                logger.warning("ignoring a malformed COLLECTION_UPDATE")
                return
            try:
                handler(CollectionUpdate(parameters[0], parameters[1], parameters[2]))
            except Exception:
                logger.exception("the on_collection_update handler failed")

            return

    def _transport_closed(self) -> None:
        with self._state_lock:
            # Notify all waiting threads that their response won't be coming
            self._response_arrived.notify_all()

    def _get_next_request_id(self) -> int:
        """Get an unused request id for the next request"""

        with self._state_lock:
            self._next_request_id += 1
            return self._next_request_id

    def _make_request(
        self,
        request_type: int,
        positional_parameters: list[Any],
        keyed_parameters: dict[Any, Any] | None = None,
        *,
        expected_return_count: int,
        timeout: float | None = None,
    ) -> list[Any] | NoAnswer:
        """
        Make a request and handle a response

        Raises:
            RnmmpError: as the Response reports an error
            MalformedExchangeError: for an OK Response missing its defined Return Parameters.
        """
        request_id = self._get_next_request_id()

        response = self.send_exchange(
            Request(request_id, request_type, keyed_parameters or {}, positional_parameters), timeout=timeout
        )
        if isinstance(response, NoAnswer):
            return response
        error = response.error(request_type)
        if error is not None:
            raise error
        if len(response.parameters) < expected_return_count:
            raise MalformedExchangeError(
                message=f"response returned {len(response.parameters)} of {expected_return_count} returns"
            )
        return response.parameters

    ############################################################################
    # Reads
    ############################################################################

    def noop(self, *, timeout: float | None = None) -> VoidAnswer | NoAnswer:
        """NOOP: keep the link alive"""
        result = self._make_request(RequestType.NOOP, [], expected_return_count=0, timeout=timeout)
        return result if isinstance(result, NoAnswer) else VoidAnswer()

    def capability(self, *, timeout: float | None = None) -> Capabilities | NoAnswer:
        """CAPABILITY: fetch the server's protocol version, and whatever optional features follow it"""
        result = self._make_request(RequestType.CAPABILITY, [], expected_return_count=1, timeout=timeout)
        if isinstance(result, NoAnswer):
            return result
        capabilities = _as_list(result[0])
        if not capabilities or not isinstance(capabilities[0], int) or isinstance(capabilities[0], bool):
            raise MalformedExchangeError(message="the Capability List must lead with a protocol version")
        return Capabilities(capabilities[0], capabilities[1:])

    def subscribe(
        self,
        collection: int,
        *,
        timeout: float | None = None,
    ) -> VoidAnswer | NoAnswer:
        """
        SUBSCRIBE: ask for COLLECTION_UPDATE Notifications for a Collection on this link

        Set `on_collection_update` to receive the notifications
        """
        result = self._make_request(RequestType.SUBSCRIBE, [collection], expected_return_count=0, timeout=timeout)
        return result if isinstance(result, NoAnswer) else VoidAnswer()

    def subscribe_single_mode(
        self,
        collection: int,
        destination: bytes,
        *,
        timeout: float | None = None,
    ) -> VoidAnswer | NoAnswer:
        """
        SUBSCRIBE: ask for COLLECTION_UPDATE Notifications to be sent to an LXMF delivery address
        """
        result = self._make_request(
            RequestType.SUBSCRIBE, [collection, destination], expected_return_count=0, timeout=timeout
        )
        return result if isinstance(result, NoAnswer) else VoidAnswer()

    def unsubscribe(self, collection: int, *, timeout: float | None = None) -> VoidAnswer | NoAnswer:
        """UNSUBSCRIBE: stop this Link's COLLECTION_UPDATE Notifications for a Collection"""
        result = self._make_request(RequestType.UNSUBSCRIBE, [collection], expected_return_count=0, timeout=timeout)
        return result if isinstance(result, NoAnswer) else VoidAnswer()

    def unsubscribe_single_mode(
        self,
        collection: int,
        destination: bytes,
        *,
        timeout: float | None = None,
    ) -> VoidAnswer | NoAnswer:
        """
        UNSUBSCRIBE: stop COLLECTION_UPDATE Notifications for a Collection being delivered to an LXMF delivery address
        """
        result = self._make_request(
            RequestType.UNSUBSCRIBE, [collection, destination], expected_return_count=0, timeout=timeout
        )
        return result if isinstance(result, NoAnswer) else VoidAnswer()

    def list_subscriptions(self, *, timeout: float | None = None) -> list[SingleModeSubscription] | NoAnswer:
        """LIST_SUBSCRIPTIONS: List active subscriptions for Single Mode destinations"""
        result = self._make_request(RequestType.LIST_SUBSCRIPTIONS, [], expected_return_count=1, timeout=timeout)
        if isinstance(result, NoAnswer):
            return result
        listed: list[SingleModeSubscription] = []
        for entry in _as_list(result[0]):
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], int)
                or isinstance(entry[0], bool)
                or not isinstance(entry[1], bytes)
            ):
                raise MalformedExchangeError(message="Subscribers must be [Collection id, Destination] pairs")
            listed.append(SingleModeSubscription(entry[0], entry[1]))
        return listed

    def sync_collection(
        self, collection: int, last_known: bytes, *, timeout: float | None = None
    ) -> CollectionDelta | NoAnswer:
        """SYNC one Collection: the Delta from `last_known`, and the State Token it leads to"""
        result = self._make_request(
            RequestType.SYNC, [int(collection), last_known], expected_return_count=2, timeout=timeout
        )
        if isinstance(result, NoAnswer):
            return result
        delta, state = result[0], result[1]
        if not isinstance(delta, dict) or not isinstance(state, bytes):
            raise MalformedExchangeError(message="SYNC must return a Delta map and a State token")
        return CollectionDelta(delta, state)

    def sync(
        self, tokens: Mapping[int, bytes], *, timeout: float | None = None
    ) -> dict[int, CollectionSync] | NoAnswer:
        """
        SYNC several Collections from their last known tokens.

        Full resyncs are handled automatically as needed.
        """
        outcomes: dict[int, CollectionSync] = {}
        for collection, last_known in tokens.items():
            full_resync = False
            try:
                result = self.sync_collection(collection, last_known, timeout=timeout)
            except UnknownStateError:
                full_resync = True
                result = self.sync_collection(collection, INITIAL_STATE_TOKEN, timeout=timeout)
            if isinstance(result, NoAnswer):
                return result
            delta, state = result
            outcomes[collection] = CollectionSync(delta, state, full_resync)
        return outcomes

    def fetch_full(self, message_ids: list[bytes], *, timeout: float | None = None) -> list[bytes | None] | NoAnswer:
        """FETCH_FULL: raw stored messages, in the order requested, `None` for each id not present"""
        return self._fetch(RequestType.FETCH_FULL, message_ids, timeout, bytes)

    def fetch_head(self, message_ids: list[bytes], *, timeout: float | None = None) -> list[bytes | None] | NoAnswer:
        """FETCH_HEAD: each LXMF message's Destination, Source and Signature portions"""
        return self._fetch(RequestType.FETCH_HEAD, message_ids, timeout, bytes)

    def fetch_payload(self, message_ids: list[bytes], *, timeout: float | None = None) -> list[bytes | None] | NoAnswer:
        """FETCH_PAYLOAD: each LXMF message's packed payload, raw"""
        return self._fetch(RequestType.FETCH_PAYLOAD, message_ids, timeout, bytes)

    def fetch_content(self, message_ids: list[bytes], *, timeout: float | None = None) -> list[bytes | None] | NoAnswer:
        """FETCH_CONTENT: each message's Content — the full bytes for a non-LXMF message"""
        return self._fetch(RequestType.FETCH_CONTENT, message_ids, timeout, bytes)

    def fetch_fields(
        self, message_ids: list[bytes], *, timeout: float | None = None
    ) -> list[dict[Any, Any] | None] | NoAnswer:
        """FETCH_FIELDS: each LXMF message's Fields map"""
        return self._fetch(RequestType.FETCH_FIELDS, message_ids, timeout, dict)

    def fetch_timestamp(
        self, message_ids: list[bytes], *, timeout: float | None = None
    ) -> list[datetime.datetime | None] | NoAnswer:
        """FETCH_TIMESTAMP: each LXMF message's Timestamp, as an aware datetime"""
        return self._fetch(RequestType.FETCH_TIMESTAMP, message_ids, timeout, datetime.datetime)

    def fetch_title(self, message_ids: list[bytes], *, timeout: float | None = None) -> list[bytes | None] | NoAnswer:
        """FETCH_TITLE: each LXMF message's Title"""
        return self._fetch(RequestType.FETCH_TITLE, message_ids, timeout, bytes)

    def fetch_tags(
        self, message_ids: list[bytes], *, timeout: float | None = None
    ) -> list[list[int] | None] | NoAnswer:
        """FETCH_TAGS: each message's current Tag IDs"""
        values = self._fetch(RequestType.FETCH_TAGS, message_ids, timeout, list)
        if isinstance(values, NoAnswer):
            return values
        for tags in values:
            if tags is not None and not all(isinstance(tag, int) and not isinstance(tag, bool) for tag in tags):
                raise MalformedExchangeError(message="FETCH_TAGS must answer lists of Tag IDs")
        return values

    def fetch_metadata(
        self, message_ids: list[bytes], *, timeout: float | None = None
    ) -> list[dict[Any, Any] | None] | NoAnswer:
        """FETCH_METADATA: each message's current Metadata Map"""
        return self._fetch(RequestType.FETCH_METADATA, message_ids, timeout, dict)

    def search_title(
        self,
        query: str,
        *,
        only_tags: Iterable[int] | None = None,
        exclude_tags: Iterable[int] | None = None,
        max_results: int | None = None,
        timeout: float | None = None,
    ) -> list[bytes] | NoAnswer:
        """SEARCH_TITLE: the ids of LXMF messages whose Title matches the query"""
        return self._search(RequestType.SEARCH_TITLE, query, only_tags, exclude_tags, max_results, timeout)

    def search_content(
        self,
        query: str,
        *,
        only_tags: Iterable[int] | None = None,
        exclude_tags: Iterable[int] | None = None,
        max_results: int | None = None,
        timeout: float | None = None,
    ) -> list[bytes] | NoAnswer:
        """SEARCH_CONTENT: the ids of messages whose Content matches the query"""
        return self._search(RequestType.SEARCH_CONTENT, query, only_tags, exclude_tags, max_results, timeout)

    ############################################################################
    # Writes
    ############################################################################

    def upload(
        self,
        raws: list[bytes],
        *,
        tags: Iterable[int] | None = None,
        metadata: Mapping[int | str, Any] | None = None,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UploadResult | NoAnswer:
        """UPLOAD: store raw messages opaquely; the id assigned to each comes back in order"""
        keyed_parameters = _write_keyed(if_in_state)
        if tags is not None:
            keyed_parameters[int(UploadParam.TAGS)] = [int(tag) for tag in tags]
        if metadata is not None:
            keyed_parameters[int(UploadParam.METADATA)] = dict(metadata)
        result = self._make_request(
            RequestType.UPLOAD, [raws], keyed_parameters, expected_return_count=2, timeout=timeout
        )
        if isinstance(result, NoAnswer):
            return result
        return UploadResult(_updated_states(result[0]), _as_list(result[1]))

    def delete(
        self,
        message_ids: list[bytes],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """DELETE: remove messages permanently"""
        return self._write(RequestType.DELETE, [message_ids], if_in_state, timeout)

    def create_tags(
        self,
        names: list[str],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> CreatedTags | NoAnswer:
        """CREATE_TAG: create named tags; the id for each name comes back in order"""
        result = self._make_request(
            RequestType.CREATE_TAG, [names], _write_keyed(if_in_state), expected_return_count=2, timeout=timeout
        )
        if isinstance(result, NoAnswer):
            return result
        return CreatedTags(_updated_states(result[0]), _as_list(result[1]))

    def delete_tags(
        self,
        tag_ids: list[int],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """DELETE_TAG: remove named tags"""
        return self._write(RequestType.DELETE_TAG, [tag_ids], if_in_state, timeout)

    def rename_tags(
        self,
        renames: Mapping[int, str],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """RENAME_TAG: rename tags in place"""
        return self._write(RequestType.RENAME_TAG, [dict(renames)], if_in_state, timeout)

    def add_tags(
        self,
        additions: Mapping[bytes, Iterable[int]],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """ADD_TAG: add tags to messages"""
        payload = {message_id: [int(tag) for tag in tags] for message_id, tags in additions.items()}
        return self._write(RequestType.ADD_TAG, [payload], if_in_state, timeout)

    def remove_tags(
        self,
        removals: Mapping[bytes, Iterable[int]],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """REMOVE_TAG: remove tags from messages"""
        payload = {message_id: [int(tag) for tag in tags] for message_id, tags in removals.items()}
        return self._write(RequestType.REMOVE_TAG, [payload], if_in_state, timeout)

    def set_metadata(
        self,
        entries: Mapping[bytes, Mapping[int | str, Any]],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """SET_METADATA: merge entries into messages' Metadata Maps"""
        payload = {message_id: dict(entry) for message_id, entry in entries.items()}
        return self._write(RequestType.SET_METADATA, [payload], if_in_state, timeout)

    def remove_metadata(
        self,
        removals: Mapping[bytes, Iterable[int | str]],
        *,
        if_in_state: Mapping[int, bytes] | None = None,
        timeout: float | None = None,
    ) -> UpdatedStates | NoAnswer:
        """REMOVE_METADATA: remove entries from messages' Metadata Maps"""
        payload = {message_id: list(keys) for message_id, keys in removals.items()}
        return self._write(RequestType.REMOVE_METADATA, [payload], if_in_state, timeout)

    ############################################################################
    # Shared helpers
    ############################################################################

    def _fetch[T](
        self, request_type: int, message_ids: list[bytes], timeout: float | None, element: type[T]
    ) -> list[T | None] | NoAnswer:
        """Every FETCH_* sends a list of ids and gets back one `element` (or `None`) per id, in order"""
        result = self._make_request(request_type, [message_ids], expected_return_count=1, timeout=timeout)
        if isinstance(result, NoAnswer):
            return result
        values = _as_list(result[0])
        if len(values) != len(message_ids):
            raise MalformedExchangeError(message="a fetch must answer one value per requested id")
        for value in values:
            if value is not None and not isinstance(value, element):
                raise MalformedExchangeError(message=f"a fetch answered {type(value).__name__}, not {element.__name__}")
        return values

    def _search(
        self,
        request_type: int,
        query: str,
        only_tags: Iterable[int] | None,
        exclude_tags: Iterable[int] | None,
        max_results: int | None,
        timeout: float | None,
    ) -> list[bytes] | NoAnswer:
        """Every SEARCH_* takes the same keyed parameters and returns a list of message ids"""
        keyed_parameters: dict[int, Any] = {}
        if max_results is not None:
            keyed_parameters[int(SearchTitleParam.MAX_RESULTS)] = max_results
        if only_tags is not None:
            keyed_parameters[int(SearchTitleParam.ONLY_TAGS)] = [int(tag) for tag in only_tags]
        if exclude_tags is not None:
            keyed_parameters[int(SearchTitleParam.EXCLUDE_TAGS)] = [int(tag) for tag in exclude_tags]

        only: set[int] = set(keyed_parameters.get(int(SearchTitleParam.ONLY_TAGS), []))
        exclude: set[int] = set(keyed_parameters.get(int(SearchTitleParam.EXCLUDE_TAGS), []))
        if only & exclude:
            # Don't bother the server with a request we know will error
            raise ConflictingFiltersError()

        result = self._make_request(request_type, [query], keyed_parameters, expected_return_count=1, timeout=timeout)
        if isinstance(result, NoAnswer):
            return result

        matches = _as_list(result[0])
        if not all(isinstance(match, bytes) for match in matches):
            raise MalformedExchangeError(message="a search must answer Message IDs")
        return matches

    def _write(
        self,
        request_type: int,
        positional: list[Any],
        if_in_state: Mapping[int, bytes] | None,
        timeout: float | None,
    ) -> UpdatedStates | NoAnswer:
        """Every write returns Updated States for the Collections it updated"""
        result = self._make_request(
            request_type, positional, _write_keyed(if_in_state), expected_return_count=1, timeout=timeout
        )
        return result if isinstance(result, NoAnswer) else _updated_states(result[0])


def _write_keyed(if_in_state: Mapping[int, bytes] | None) -> dict[int, Any]:
    """The Keyed Parameters every write shares"""
    return {} if if_in_state is None else {int(WriteParam.IF_IN_STATE): dict(if_in_state)}


def _as_list(value: Any) -> list[Any]:
    """The value as the list the request type defines, or a malformed-response error"""
    if not isinstance(value, list):
        raise MalformedExchangeError(message=f"expected a list return, got {type(value).__name__}")
    return value


def _updated_states(value: Any) -> UpdatedStates:
    """Parse the Updated States map as `UpdatedStates`"""
    if not isinstance(value, dict):
        raise MalformedExchangeError(message="Updated States must be a map")
    updated: UpdatedStates = {}
    for collection, pair in value.items():
        if not isinstance(collection, int) or not isinstance(pair, list) or len(pair) != 2:
            raise MalformedExchangeError(message="Updated States must map Collection ids to [previous, new]")
        previous, new = pair
        if not isinstance(previous, bytes) or not isinstance(new, bytes):
            raise MalformedExchangeError(message="State Tokens must be byte strings")
        updated[collection] = TokenChange(previous, new)
    return updated
