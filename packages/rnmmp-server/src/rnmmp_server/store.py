"""
The storage interface for a mailbox.

A `Store` only handles the actual state and mailbox storage.
Protocol logic lives in `rnmmp_server.model` instead;
a storage backend only reads its records and applies one `ChangeSet` at a time.

The `MemoryStore` here is a reference backend, not intended for production use.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from itertools import batched
from typing import Any, Final, Protocol

from LXMF import LXMessage

from rnmmp_core import INITIAL_STATE_TOKEN, MalformedExchangeError, unpack

from . import lxmf_portions

HEAD_LENGTH: Final[int] = 2 * LXMessage.DESTINATION_LENGTH + LXMessage.SIGNATURE_LENGTH
"""Destination hash, source hash and signature: the LXMF head"""

__all__ = [
    "ChangeSet",
    "LogEntry",
    "MemoryStore",
    "MessageIndex",
    "ScanSearch",
    "StoredMessage",
    "Store",
]


@dataclass(frozen=True, slots=True)
class MessageIndex:
    """
    What the store indexes about a message.

    Storage backends can store these fields in a faster and
    better-indexed store to provide quicker fetching and searching
    """

    message_id: bytes
    lxmf: bool
    head: bytes | None
    """The Destination, Source and Signature portions, as FETCH_HEAD returns them"""
    timestamp: float | None
    """The LXMF Timestamp, in seconds since the Unix epoch"""
    title: bytes | None
    """The LXMF Title"""

    def as_stored_message(self, raw: bytes) -> StoredMessage:
        """Bundle with message body"""
        return StoredMessage(
            message_id=self.message_id,
            lxmf=self.lxmf,
            head=self.head,
            timestamp=self.timestamp,
            title=self.title,
            raw=raw,
        )


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """One mail item, stored as delivered/uploaded"""

    message_id: bytes
    """The 32-byte LXMF message hash, or a 16-byte UUID for a non-LXMF upload"""

    raw: bytes
    """The message bytes, byte-for-byte as delivered or uploaded"""

    lxmf: bool
    """
    Whether the message is LXMF.

    Uploads are stored opaquely and are not considered LXMF even if they parse.
    """

    # NOTE `head`, `timestamp`, and `title` are set at write-time.
    # Stores should trust the values submitted by the model, and not try to derive them
    head: bytes | None = None
    """The Destination, Source and Signature portions, as FETCH_HEAD returns them"""
    timestamp: float | None = None
    title: bytes | None = None

    @classmethod
    def from_raw(cls, message_id: bytes, raw: bytes, *, lxmf: bool) -> StoredMessage:
        """
        Build a record, extracting the index portions if the message is LXMF.

        A message entered with `lxmf=False` or that does not slice or decode as LXMF gets `None` portions
        """
        if not lxmf:
            return cls(message_id, raw, lxmf=False)
        head = raw[:HEAD_LENGTH] if len(raw) > HEAD_LENGTH else None
        timestamp: float | None = None
        title: bytes | None = None
        if head is not None:
            try:
                parts = unpack(raw[HEAD_LENGTH:])
            except MalformedExchangeError:
                parts = None
            if isinstance(parts, list) and len(parts) >= 4:
                if isinstance(parts[0], int | float) and not isinstance(parts[0], bool):
                    timestamp = float(parts[0])
                if isinstance(parts[1], bytes):
                    title = parts[1]
        return cls(message_id, raw, lxmf=True, head=head, timestamp=timestamp, title=title)

    def index(self) -> MessageIndex:
        """The record's indexed fields"""
        return MessageIndex(self.message_id, self.lxmf, self.head, self.timestamp, self.title)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """
    One write's change-log record for one Collection: the keys the write changed, each with
    the value it held before the write.

    A delta compares the value a client last saw against the value now. The log supplies the "last saw" history.
    Walking the entries made since the client's token, the first entry that mentions some key reveals the value that
    the client still holds for it, and a key no entry mentions is unchanged.

    What key and value mean depends on the Collection:

    * MAIL_LIST — key: message id; value: whether the message existed (`bool`)
    * TAG_LIST — key: tag id; value: its name, `None` for a tag that did not exist
    * MESSAGE_TAG — key: message id; value: its tag ids as a `frozenset`, empty when the
      message did not exist or was untagged
    * METADATA — key: message id; value: its metadata map, empty when the message did not
      exist or had none
    """

    token_before: bytes
    """The Collection's State Token before this write"""

    priors: dict[Any, Any]
    """The changed keys, each mapped to the value it held before the write"""


@dataclass(slots=True)
class ChangeSet:
    """
    The atomic unit a `Store` applies; every mutation of one write request.

    A backend MUST apply a ChangeSet completely or not at all. Deleting a message removes its
    MESSAGE_TAG and METADATA rows with it, and deleting a tag removes its MESSAGE_TAG rows —
    the cascades a relational backend gets from its foreign keys.
    """

    # MAIL_LIST
    messages_added: list[StoredMessage] = field(default_factory=list)
    """Messages to add to the MAIL_LIST collection, with their content to store"""
    message_ids_deleted: list[bytes] = field(default_factory=list)
    """Messages to remove from the MAIL_LIST collection"""

    # TAG_LIST
    tags_created: dict[int, str] = field(default_factory=dict)
    """Tags to add to the TAG_LIST collection; tag id -> name mapping"""
    tag_ids_deleted: list[int] = field(default_factory=list)
    """Tags to remove from the TAG_LIST collection"""
    tags_renamed: dict[int, str] = field(default_factory=dict)
    """Tags to rename in the TAG_LIST collection; tag id -> name mapping"""

    # MESSAGE_TAG
    tag_pairs_added: list[tuple[bytes, int]] = field(default_factory=list)
    """(message id, tag id) rows to add to the MESSAGE_TAG Collection"""
    tag_pairs_removed: list[tuple[bytes, int]] = field(default_factory=list)
    """(message id, tag id) rows to remove from the MESSAGE_TAG Collection"""

    # METADATA
    metadata_set: dict[bytes, dict[Any, Any]] = field(default_factory=dict)
    """Metadata entries to merge into each message's map"""
    metadata_keys_removed: dict[bytes, list[Any]] = field(default_factory=dict)
    """Metadata entries to remove from each message's map"""

    # State
    new_tokens: dict[int, bytes] = field(default_factory=dict)
    """Collection id → the State Token the Collection holds after this write"""
    log_entries: dict[int, LogEntry] = field(default_factory=dict)
    """Collection id → the change-log entry recording this write"""


class Store(Protocol):
    """
    Interface for a mailbox storage backend.

    Reads describe committed state only. `apply` commits one `ChangeSet` atomically;
    the model serializes writes, so a backend never sees two concurrent `apply` calls for one mailbox.
    """

    def get_all_message_ids(self) -> list[bytes]:
        """Every message id in the MAIL_LIST Collection"""
        ...

    def get_existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids present in the MAIL_LIST Collection"""
        ...

    def get_message_indexes(self, message_ids: Sequence[bytes]) -> list[MessageIndex | None]:
        """The index records, in the order requested, `None` for each id not present; content untouched"""
        ...

    def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
        """
        The stored messages with their content, in the order requested, `None` for each id not present.
        """
        ...

    def get_all_tags(self) -> dict[int, str]:
        """The TAG_LIST Collection: every tag id and its name, Server-Defined Tags included"""
        ...

    def get_message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message; every requested id is a key, empty for untagged and unknown ids"""
        ...

    def get_messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag; every requested id is a key, empty for unused and unknown ids"""
        ...

    def get_message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """Each requested message's metadata map; every requested id is a key, bare and unknown ids with an empty map"""
        ...

    def search_title(
        self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
    ) -> Iterable[bytes]:
        """
        The ids of LXMF messages whose Title matches `query`, in any order.

        Matching semantics are the backend's choice, though note:
        - Lazy production is encouraged: the model stops consuming once it has enough filtered
            matches, so a lazy backend does no more work than what was asked for.
        - `ScanSearch` is both a reference implementation and can be used directly
            if no better alternative exists for the backend.
        - The tag filters are narrowing hints; a backend may use them to search an indexed subset or may ignore them.
        - Over-returning is fine, since the model re-applies the filters
        - The hints alone shouldn't filter any further than the reference;
            the specification technically allows for it, but it makes for a bad implementation
        """
        ...

    def search_content(
        self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
    ) -> Iterable[bytes]:
        """
        The ids of messages whose Content matches `query`

        Non-LXMF messages are searched by their full content.

        The tag filters are the same narrowing hints `search_title` takes, and lazy production
        is encouraged the same way.
        """
        ...

    def get_current_token(self, collection: int) -> bytes:
        """The Collection's State Token; the Initial State Token if it has never changed"""
        ...

    def get_entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """
        The change-log entries after `token`, oldest first.

        Returns `[]` when `token` is the Collection's current token, and `None` when the token
        is not one this store can still answer for — pruned, foreign, or never issued.
        """
        ...

    def apply(self, changes: ChangeSet) -> None:
        """Apply one write's mutations atomically"""
        ...


_CONTENT_SCAN_BATCH: Final = 16
"""Bodies loaded per step of the content scan"""


def _fold(text: bytes) -> str:
    """Bytes as casefolded text, undecodable sequences replaced"""
    return text.decode("utf-8", errors="replace").casefold()


class ScanSearch(Store):
    """
    The default search: an in-process scan built only on the store's own reads.

    A backend can inherits this for compliant searching, or use overrides with something better-indexed.

    The spec leaves matching semantics implementation-defined, requiring at minimum a
    case-insensitive substring match — this implementation does only that.

    Title search reads only the message index; content search uses a full content read.

    WARNING: Subclassing does not runtime-check the rest of the `Store` surface,
    but mypy enforces completeness wherever the store is used as a `Store`.
    """

    def search_title(
        self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
    ) -> Iterable[bytes]:
        """Scan the index of the messages the filters allow"""
        search_substring = query.casefold()
        for index in self.get_message_indexes(self._filter_candidates(only_tags, exclude_tags)):
            if index is not None and index.title is not None and search_substring in _fold(index.title):
                yield index.message_id

    def search_content(
        self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
    ) -> Iterable[bytes]:
        """
        Scan content: the Content portion for LXMF, the full bytes otherwise.

        The filters are applied first, so bodies the filters rule out are never loaded — and
        bodies load in small batches, so a consumer that stops early loads little more than it
        consumed.
        """
        search_substring = query.casefold()
        for chunk in batched(self._filter_candidates(only_tags, exclude_tags), _CONTENT_SCAN_BATCH, strict=False):
            for record in self.get_messages(list(chunk)):
                if record is None:
                    continue
                content = lxmf_portions.content(record.raw) if record.lxmf else record.raw
                if content is not None and search_substring in _fold(content):
                    yield record.message_id

    def _filter_candidates(self, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]) -> list[bytes]:
        """The ids that can satisfy the tag filters."""
        message_ids = self.get_all_message_ids()
        if not only_tags and not exclude_tags:
            return message_ids
        held = self.get_message_tags(message_ids)
        return [mid for mid in message_ids if only_tags <= held[mid] and not (exclude_tags & held[mid])]


class MemoryStore(ScanSearch):
    """
    An in-memory `Store` as a reference backend

    NOT INTENDED FOR PRODUCTION USE
    """

    def __init__(self, *, log_limit: int = 512) -> None:
        """
        Args:
            log_limit: Change-log entries retained per Collection
        """
        self._messages: dict[bytes, StoredMessage] = {}
        self._tags: dict[int, str] = {}
        self._tag_pairs: set[tuple[bytes, int]] = set()
        self._metadata: dict[bytes, dict[Any, Any]] = {}
        self._tokens: dict[int, bytes] = {}
        self._logs: dict[int, deque[LogEntry]] = {}
        self._log_limit = log_limit

    def get_all_message_ids(self) -> list[bytes]:
        """Every message id, in insertion order"""
        return list(self._messages)

    def get_existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids that exist"""
        return {message_id for message_id in message_ids if message_id in self._messages}

    def get_message_indexes(self, message_ids: Sequence[bytes]) -> list[MessageIndex | None]:
        """The index projections, in the order requested"""
        return [record.index() if (record := self._messages.get(message_id)) else None for message_id in message_ids]

    def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
        """The stored messages, in the order requested"""
        return [self._messages.get(message_id) for message_id in message_ids]

    def get_all_tags(self) -> dict[int, str]:
        """A copy of the tag table"""
        return dict(self._tags)

    def get_message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message"""
        message_tags: dict[bytes, set[int]] = {message_id: set() for message_id in message_ids}
        for message_id, tag_id in self._tag_pairs:
            if message_id in message_tags:
                message_tags[message_id].add(tag_id)
        return message_tags

    def get_messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag"""
        tag_messages: dict[int, set[bytes]] = {tag_id: set() for tag_id in tag_ids}
        for message_id, tag_id in self._tag_pairs:
            if tag_id in tag_messages:
                tag_messages[tag_id].add(message_id)
        return tag_messages

    def get_message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """A copy of each requested message's metadata map"""
        return {message_id: dict(self._metadata.get(message_id, {})) for message_id in message_ids}

    def get_current_token(self, collection: int) -> bytes:
        """The Collection's State Token"""
        return self._tokens.get(collection, INITIAL_STATE_TOKEN)

    def get_entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """The change-log entries after `token`, oldest first; `None` when the token is unknown"""
        if token == self.get_current_token(collection):
            return []
        log = self._logs.get(collection, ())
        for index, entry in enumerate(log):
            if entry.token_before == token:
                return list(log)[index:]
        return None

    def apply(self, changes: ChangeSet) -> None:
        """Apply one write's mutations"""
        for message in changes.messages_added:
            self._messages[message.message_id] = message
        for message_id in changes.message_ids_deleted:
            self._messages.pop(message_id, None)
            self._metadata.pop(message_id, None)
            self._tag_pairs = {pair for pair in self._tag_pairs if pair[0] != message_id}

        self._tags.update(changes.tags_created)
        self._tags.update(changes.tags_renamed)
        for tag_id in changes.tag_ids_deleted:
            self._tags.pop(tag_id, None)
            self._tag_pairs = {pair for pair in self._tag_pairs if pair[1] != tag_id}

        self._tag_pairs.update(changes.tag_pairs_added)
        self._tag_pairs.difference_update(changes.tag_pairs_removed)

        for message_id, entries in changes.metadata_set.items():
            self._metadata.setdefault(message_id, {}).update(entries)
        for message_id, keys in changes.metadata_keys_removed.items():
            metadata = self._metadata.get(message_id, {})
            for key in keys:
                metadata.pop(key, None)

        self._tokens.update(changes.new_tokens)
        for collection, entry in changes.log_entries.items():
            self._logs.setdefault(collection, deque(maxlen=self._log_limit)).append(entry)
