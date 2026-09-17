"""
One handler per request type, from a decoded `Request` to the `Response` that answers it.

Handlers are pure protocol-to-model glue with no RNS in them: `handle` is the single entry the
transport calls, and it is equally the entry Single mode will call later. Request types that a
later chunk implements — the subscription family and the SEND family — answer `UNSUPPORTED`,
which the spec permits for any request.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from typing import Any

from rnmmp_core import (
    PROTOCOL_VERSION,
    MetadataKey,
    Request,
    Response,
    RnmmpError,
    SearchTitleParam,
    UnsupportedError,
    UploadParam,
    WriteParam,
    WrongTypeError,
)
from rnmmp_core.codes import RequestType

from . import lxmf_portions
from .model import MailboxModel, UpdatedStates
from .store import MessageIndex, StoredMessage

__all__ = ["handle"]

Handler = Callable[[MailboxModel, Request], Response]


def handle(model: MailboxModel, request: Request) -> Response:
    """
    Process a request for a mailbox.

    Anything raised as an `RnmmpError` is returned as an error response.
    Other exceptions are uncaught: the transport decides how to report a server error.
    """
    handler = _HANDLERS.get(request.request_type)
    try:
        if handler is None:
            raise UnsupportedError()
        return handler(model, request)
    except RnmmpError as error:
        return Response.failure(request.request_id, error, request.request_type)


############################################################################
# Parameter helpers
############################################################################


def _bytes_items(values: Iterable[Any], name: str) -> list[bytes]:
    """The values as a list, each required to be Bytes"""
    items = list(values)
    if not all(isinstance(item, bytes) for item in items):
        raise WrongTypeError(message=f"every item of {name} must be bytes")
    return items


def _int_items(values: Iterable[Any], name: str) -> list[int]:
    """The values as a list, each required to be an integer"""
    items = list(values)
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
        raise WrongTypeError(message=f"every item of {name} must be an integer")
    return items


def _if_in_state(request: Request) -> dict[int, bytes] | None:
    """The shared IF_IN_STATE Keyed Parameter, present on every write request"""
    supplied = request.get_keyed(int(WriteParam.IF_IN_STATE), dict, name="IF_IN_STATE")
    if supplied is None:
        return None
    if not all(isinstance(key, int) and isinstance(value, bytes) for key, value in supplied.items()):
        raise WrongTypeError(message="IF_IN_STATE must map Collection ids to State Tokens")
    return supplied


def _package_updated_states(updated: UpdatedStates) -> dict[int, list[bytes]]:
    """Package `updated` to their API format: A map from Collection id to the [previous, new] pair"""
    return {collection: [pair.previous, pair.new] for collection, pair in updated.items()}


def _receive_time() -> dict[Any, Any]:
    """The server-managed metadata to add to stored messages"""
    return {int(MetadataKey.RECEIVE_TIME): datetime.datetime.now(datetime.UTC)}


############################################################################
# Reads
############################################################################


def _noop(model: MailboxModel, request: Request) -> Response:
    """NOOP: no action performed"""
    return Response.ok(request.request_id)


def _capability(model: MailboxModel, request: Request) -> Response:
    """CAPABILITY: the protocol version and optional features"""
    return Response.ok(request.request_id, [PROTOCOL_VERSION])


def _sync(model: MailboxModel, request: Request) -> Response:
    """SYNC: the delta for a Collection from a given State Token, and the token it leads to"""
    collection = request.get_required(0, int, name="Collection id")
    last_known = request.get_required(1, bytes, name="Last Known State")
    delta, state = model.sync(collection, last_known)
    return Response.ok(request.request_id, delta, state)


def _fetcher(extract: Callable[[StoredMessage], Any]) -> Handler:
    """
    Create a FETCH_* handler that relies on message content.

    Args:
        extract: Function to convert a stored message to the requested message portion,
            or returning `None` when that portion does not exist
    """

    def fetch(model: MailboxModel, request: Request) -> Response:
        """Fetch one portion of the requested messages, in the order requested"""
        message_ids = _bytes_items(request.get_required(0, list, name="Message IDs"), "Message IDs")
        records = model.get_messages(message_ids)
        return Response.ok(request.request_id, [extract(record) if record else None for record in records])

    return fetch


def _index_fetcher(extract: Callable[[MessageIndex], Any]) -> Handler:
    """
    Create a FETCH_* handler that doesn't rely on message content.

        Args:
            extract: Function to convert a stored message index to the requested message portion,
                or returning `None` when that portion does not exist
    """

    def fetch(model: MailboxModel, request: Request) -> Response:
        """Fetch one indexed portion of the requested messages, in the order requested"""
        message_ids = _bytes_items(request.get_required(0, list, name="Message IDs"), "Message IDs")
        records = model.index_of(message_ids)
        return Response.ok(request.request_id, [extract(record) if record else None for record in records])

    return fetch


def _index_timestamp(index: MessageIndex) -> datetime.datetime | None:
    """Load the timestamp as a datetime"""
    if index.timestamp is None:
        return None
    return datetime.datetime.fromtimestamp(index.timestamp, tz=datetime.UTC)


def _lxmf_only(extract: Callable[[bytes], Any]) -> Callable[[StoredMessage], Any]:
    """Create a `_fetcher` `extract` function that operates on the raw content of an LXMF message"""
    return lambda record: extract(record.raw) if record.lxmf else None


def _fetch_tags(model: MailboxModel, request: Request) -> Response:
    """FETCH_TAGS: each message's current Tag IDs, an empty list for an untagged message."""
    message_ids = _bytes_items(request.get_required(0, list, name="Message IDs"), "Message IDs")
    return Response.ok(request.request_id, model.tags_of(message_ids))


def _fetch_metadata(model: MailboxModel, request: Request) -> Response:
    """FETCH_METADATA: each message's current Metadata Map, an empty map for a bare message."""
    message_ids = _bytes_items(request.get_required(0, list, name="Message IDs"), "Message IDs")
    return Response.ok(request.request_id, model.metadata_of(message_ids))


def _search_parameters(request: Request) -> tuple[str, list[int], list[int], int | None]:
    """The parameters both SEARCH requests share; the two define identical keys"""
    query = request.get_required(0, str, name="Query")
    max_results = request.get_keyed(int(SearchTitleParam.MAX_RESULTS), int, name="MAX_RESULTS")
    if max_results is not None and max_results < 0:
        raise WrongTypeError(message="MAX_RESULTS must not be negative")
    only = _int_items(
        request.get_keyed(int(SearchTitleParam.ONLY_TAGS), list, name="ONLY_TAGS", default=[]), "ONLY_TAGS"
    )
    exclude = _int_items(
        request.get_keyed(int(SearchTitleParam.EXCLUDE_TAGS), list, name="EXCLUDE_TAGS", default=[]), "EXCLUDE_TAGS"
    )
    return query, only, exclude, max_results


def _search_title(model: MailboxModel, request: Request) -> Response:
    """SEARCH_TITLE: the ids of LXMF messages whose Title matches the Query"""
    query, only, exclude, max_results = _search_parameters(request)
    matches = model.search_title(query, only_tags=only, exclude_tags=exclude, max_results=max_results)
    return Response.ok(request.request_id, matches)


def _search_content(model: MailboxModel, request: Request) -> Response:
    """SEARCH_CONTENT: the ids of messages whose Content matches the Query"""
    query, only, exclude, max_results = _search_parameters(request)
    matches = model.search_content(query, only_tags=only, exclude_tags=exclude, max_results=max_results)
    return Response.ok(request.request_id, matches)


############################################################################
# Writes
############################################################################


def _upload(model: MailboxModel, request: Request) -> Response:
    """UPLOAD: store the raw messages opaquely, with the server recording the receive time"""
    raws = _bytes_items(request.get_required(0, list, name="Messages"), "Messages")
    tags = _int_items(request.get_keyed(int(UploadParam.TAGS), list, name="TAGS", default=[]), "TAGS")
    metadata = request.get_keyed(int(UploadParam.METADATA), dict, name="METADATA", default={})
    updated, message_ids = model.upload(
        raws, tags=tags, metadata=metadata, server_metadata=_receive_time(), if_in_state=_if_in_state(request)
    )
    return Response.ok(request.request_id, _package_updated_states(updated), message_ids)


def _delete(model: MailboxModel, request: Request) -> Response:
    """DELETE: remove messages permanently."""
    message_ids = _bytes_items(request.get_required(0, list, name="Message IDs"), "Message IDs")
    updated = model.delete(message_ids, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated))


def _create_tags(model: MailboxModel, request: Request) -> Response:
    """CREATE_TAG: create named tags, returning the id for each name."""
    names = request.get_required(0, list, name="Names")
    updated, tag_ids = model.create_tags(names, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated), tag_ids)


def _delete_tags(model: MailboxModel, request: Request) -> Response:
    """DELETE_TAG: remove named tags."""
    tag_ids = _int_items(request.get_required(0, list, name="Tag IDs"), "Tag IDs")
    updated = model.delete_tags(tag_ids, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated))


def _rename_tags(model: MailboxModel, request: Request) -> Response:
    """RENAME_TAG: rename tags in place."""
    renames = request.get_required(0, dict, name="Renames")
    if not all(isinstance(key, int) and not isinstance(key, bool) for key in renames):
        raise WrongTypeError(message="Renames must be keyed by Tag ID")
    updated = model.rename_tags(renames, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated))


def _pair_writer(adding: bool) -> Handler:
    """ADD_TAG and REMOVE_TAG share their shape: a map of Message ID to Tag IDs."""

    def write(model: MailboxModel, request: Request) -> Response:
        """Apply the tag additions or removals."""
        name = "Additions" if adding else "Removals"
        supplied = request.get_required(0, dict, name=name)
        if not all(isinstance(key, bytes) for key in supplied):
            raise WrongTypeError(message=f"{name} must be keyed by Message ID")
        changes = {key: _int_items(value, name) for key, value in supplied.items()}
        if_in_state = _if_in_state(request)
        if adding:
            updated = model.add_tags(changes, if_in_state=if_in_state)
        else:
            updated = model.remove_tags(changes, if_in_state=if_in_state)
        return Response.ok(request.request_id, _package_updated_states(updated))

    return write


def _set_metadata(model: MailboxModel, request: Request) -> Response:
    """SET_METADATA: merge entries into messages' Metadata Maps."""
    entries = request.get_required(0, dict, name="Entries")
    if not all(isinstance(key, bytes) and isinstance(value, dict) for key, value in entries.items()):
        raise WrongTypeError(message="Entries must map Message IDs to Maps")
    updated = model.set_metadata(entries, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated))


def _remove_metadata(model: MailboxModel, request: Request) -> Response:
    """REMOVE_METADATA: remove entries from messages' Metadata Maps."""
    removals = request.get_required(0, dict, name="Removals")
    if not all(isinstance(key, bytes) and isinstance(value, list) for key, value in removals.items()):
        raise WrongTypeError(message="Removals must map Message IDs to key lists")
    updated = model.remove_metadata(removals, if_in_state=_if_in_state(request))
    return Response.ok(request.request_id, _package_updated_states(updated))


_HANDLERS: dict[int, Handler] = {
    RequestType.NOOP: _noop,
    RequestType.CAPABILITY: _capability,
    RequestType.SYNC: _sync,
    RequestType.FETCH_FULL: _fetcher(lambda record: record.raw),
    RequestType.FETCH_HEAD: _index_fetcher(lambda index: index.head),
    RequestType.FETCH_PAYLOAD: _fetcher(_lxmf_only(lxmf_portions.payload)),
    RequestType.FETCH_CONTENT: _fetcher(
        lambda record: lxmf_portions.content(record.raw) if record.lxmf else record.raw
    ),
    RequestType.FETCH_FIELDS: _fetcher(_lxmf_only(lxmf_portions.fields)),
    RequestType.FETCH_TIMESTAMP: _index_fetcher(_index_timestamp),
    RequestType.FETCH_TITLE: _index_fetcher(lambda index: index.title),
    RequestType.FETCH_TAGS: _fetch_tags,
    RequestType.FETCH_METADATA: _fetch_metadata,
    RequestType.SEARCH_TITLE: _search_title,
    RequestType.SEARCH_CONTENT: _search_content,
    RequestType.UPLOAD: _upload,
    RequestType.DELETE: _delete,
    RequestType.CREATE_TAG: _create_tags,
    RequestType.DELETE_TAG: _delete_tags,
    RequestType.RENAME_TAG: _rename_tags,
    RequestType.ADD_TAG: _pair_writer(adding=True),
    RequestType.REMOVE_TAG: _pair_writer(adding=False),
    RequestType.SET_METADATA: _set_metadata,
    RequestType.REMOVE_METADATA: _remove_metadata,
}
