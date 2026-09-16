"""
The storage interface for a mailbox.

A `Store` only handles the actual state and mailbox storage.
Protocol logic lives in `rnmmp_server.model` instead;
a storage backend only reads its records and applies one `ChangeSet` at a time.

The `MemoryStore` here is a reference backend, not intended for production use.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from rnmmp_core import INITIAL_STATE_TOKEN

__all__ = [
    "ChangeSet",
    "LogEntry",
    "MemoryStore",
    "StoredMessage",
    "Store",
]


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

    def all_message_ids(self) -> list[bytes]:
        """Every message id in the MAIL_LIST Collection"""
        ...

    def existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids present in the MAIL_LIST Collection"""
        ...

    def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
        """
        The stored messages with their content, in the order requested, `None` for each id not present.
        """
        ...

    def all_tags(self) -> dict[int, str]:
        """The TAG_LIST Collection: every tag id and its name, Server-Defined Tags included"""
        ...

    def message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message; every requested id is a key, empty for untagged and unknown ids"""
        ...

    def messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag; every requested id is a key, empty for unused and unknown ids"""
        ...

    def message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """Each requested message's metadata map; every requested id is a key, bare and unknown ids with an empty map"""
        ...

    def current_token(self, collection: int) -> bytes:
        """The Collection's State Token; the Initial State Token if it has never changed"""
        ...

    def entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """
        The change-log entries after `token`, oldest first.

        Returns `[]` when `token` is the Collection's current token, and `None` when the token
        is not one this store can still answer for — pruned, foreign, or never issued.
        """
        ...

    def apply(self, changes: ChangeSet) -> None:
        """Apply one write's mutations atomically"""
        ...


class MemoryStore:
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

    def all_message_ids(self) -> list[bytes]:
        """Every message id, in insertion order"""
        return list(self._messages)

    def existing_message_ids(self, message_ids: Sequence[bytes]) -> set[bytes]:
        """The subset of the requested ids that exist"""
        return {message_id for message_id in message_ids if message_id in self._messages}

    def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
        """The stored messages, in the order requested"""
        return [self._messages.get(message_id) for message_id in message_ids]

    def all_tags(self) -> dict[int, str]:
        """A copy of the tag table"""
        return dict(self._tags)

    def message_tags(self, message_ids: Sequence[bytes]) -> dict[bytes, set[int]]:
        """The tag ids on each requested message"""
        held: dict[bytes, set[int]] = {message_id: set() for message_id in message_ids}
        for message_id, tag_id in self._tag_pairs:
            if message_id in held:
                held[message_id].add(tag_id)
        return held

    def messages_with_tags(self, tag_ids: Sequence[int]) -> dict[int, set[bytes]]:
        """The message ids carrying each requested tag"""
        carriers: dict[int, set[bytes]] = {tag_id: set() for tag_id in tag_ids}
        for message_id, tag_id in self._tag_pairs:
            if tag_id in carriers:
                carriers[tag_id].add(message_id)
        return carriers

    def message_metadata(self, message_ids: Sequence[bytes]) -> dict[bytes, dict[Any, Any]]:
        """A copy of each requested message's metadata map"""
        return {message_id: dict(self._metadata.get(message_id, {})) for message_id in message_ids}

    def current_token(self, collection: int) -> bytes:
        """The Collection's State Token"""
        return self._tokens.get(collection, INITIAL_STATE_TOKEN)

    def entries_since(self, collection: int, token: bytes) -> list[LogEntry] | None:
        """The change-log entries after `token`, oldest first; `None` when the token is unknown"""
        if token == self.current_token(collection):
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
