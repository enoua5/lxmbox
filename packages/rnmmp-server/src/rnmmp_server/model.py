"""
The storage-agnostic mailbox model: implements mailbox state rules, over any implemention of `Store`.

Writes are serialized per mailbox with a lock so a `Store` never sees two concurrent writes.

Everything raised here is an `RnmmpError`, ready for `Response.failure(request_id, error, request_type)`.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import Any, Final

import LXMF

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
    ConflictingFiltersError,
    DuplicateTagNameError,
    InvalidMetadataKeyError,
    InvalidTagNameError,
    MailListDeltaKey,
    MetadataKey,
    ReservedMetadataKeyError,
    ServerDefinedTagError,
    ServerTag,
    StateMismatchDetail,
    StateMismatchError,
    UnknownCollectionError,
    UnknownMessageError,
    UnknownStateError,
    UnknownTagError,
)

from .deltas import compose_deltas, is_empty_delta, make_empty_delta, pack_fragment, unpack_fragment
from .store import ChangeSet, LogEntry, MessageIndex, Store, StoredMessage

__all__ = [
    "DEFAULT_INITIAL_TAGS",
    "DEFAULT_MANAGED_METADATA_KEYS",
    "SERVER_DEFINED_TAG_NAMES",
    "TOKEN_LENGTH",
    "MailboxModel",
    "TokenPair",
    "UpdatedStates",
    "default_ingest_tags",
    "default_managed_metadata",
]

logger = logging.getLogger(__name__)

SERVER_DEFINED_TAG_NAMES: Final[dict[int, str]] = {tag.value: tag.name for tag in ServerTag}
"""The Server-Defined Tags every mailbox holds, named as the specification names them"""

DEFAULT_INITIAL_TAGS: Final[frozenset[int]] = frozenset({ServerTag.UNREAD})
"""The tags the default `ingest_tags` policy puts on every ingested message"""

DEFAULT_MANAGED_METADATA_KEYS: Final[frozenset[int]] = frozenset({MetadataKey.RECEIVE_TIME})
"""The metadata keys a mailbox manages — and refuses from clients — unless configured otherwise"""


def default_ingest_tags(message: StoredMessage) -> Iterable[int]:
    """Default handler for tagging newly ingested messages"""
    return DEFAULT_INITIAL_TAGS


def default_managed_metadata(message: StoredMessage) -> Mapping[Any, Any]:
    """Default handler for adding metadata to newly ingested messaged"""
    return {MetadataKey.RECEIVE_TIME: datetime.datetime.now(datetime.UTC)}


TOKEN_LENGTH: Final = 8
"""State Token length in bytes; tokens are opaque and random"""


@dataclass(frozen=True, slots=True)
class TokenPair:
    """One Collection's State Token change; the returned `[previous, new]` pair"""

    previous: bytes
    new: bytes


type UpdatedStates = dict[int, TokenPair]
"""Collection id → token change for the Collections a write changed"""


def _valid_tag_name(name: object) -> bool:
    """Whether the server accepts `name` for a tag"""
    return isinstance(name, str) and bool(name.strip())


def _valid_metadata_key(key: object) -> bool:
    """Metadata Map keys are integers or strings"""
    return (isinstance(key, int) and not isinstance(key, bool)) or isinstance(key, str)


class MailboxModel:
    """One mailbox's state, with the operations the requests need"""

    def __init__(
        self,
        store: Store,
        *,
        get_initial_tags: Callable[[StoredMessage], Iterable[int]] = default_ingest_tags,
        get_initial_metadata: Callable[[StoredMessage], Mapping[Any, Any]] = default_managed_metadata,
        managed_metadata_keys: Iterable[Any] = DEFAULT_MANAGED_METADATA_KEYS,
    ) -> None:
        """
        Initialize a mailbox with data stored using `store`,
        seeding the Server-Defined Tags if they are not present.

        Seeding changes no State Token: the Initial State of the TAG_LIST Collection already
        includes the Server-Defined Tags.

        Args:
            store: The interface backing data storage for the mailbox
            get_initial_tags: Function taking a message being ingested
                and returning the tags it should be initialized with
            get_initial_metadata: Function taking a message being ingested
                and returning the metadata it should be initialized with
            managed_metadata_keys: The metadata keys the server forbids
                the client from modifying on messages
        """
        self._store = store
        self._get_initial_tags = get_initial_tags
        self._get_initial_metadata = get_initial_metadata
        self._managed_metadata_keys = frozenset(managed_metadata_keys)
        self._lock = threading.Lock()
        missing = {
            tag_id: name for tag_id, name in SERVER_DEFINED_TAG_NAMES.items() if tag_id not in store.get_all_tags()
        }
        if missing:
            store.apply(ChangeSet(tags_created=missing))

    ############################################################################
    # Reads
    ############################################################################

    def message_ids(self) -> list[bytes]:
        """The MAIL_LIST Collection"""
        with self._lock:
            return self._store.get_all_message_ids()

    def get_messages(self, message_ids: Iterable[bytes]) -> list[StoredMessage | None]:
        """The stored messages, in the order requested, `None` for each id not present"""
        with self._lock:
            return self._store.get_messages(list(message_ids))

    def index_of(self, message_ids: Iterable[bytes]) -> list[MessageIndex | None]:
        """Each message's index record (non-content fields) in the order requested, `None` for missing"""
        with self._lock:
            return self._store.get_message_indexes(list(message_ids))

    def tags(self) -> dict[int, str]:
        """The TAG_LIST Collection"""
        with self._lock:
            return self._store.get_all_tags()

    def tags_of(self, message_ids: Iterable[bytes]) -> list[list[int] | None]:
        """Each message's tag ids, in the order requested: `None` for a missing message, `[]` for an untagged one"""
        with self._lock:
            requested = list(message_ids)
            present = self._store.get_existing_message_ids(requested)
            held = self._store.get_message_tags(requested)
            return [sorted(held[message_id]) if message_id in present else None for message_id in requested]

    def metadata_of(self, message_ids: Iterable[bytes]) -> list[dict[Any, Any] | None]:
        """Each message's metadata map, in the order requested: `None` for a missing message, `{}` for a bare one"""
        with self._lock:
            requested = list(message_ids)
            present = self._store.get_existing_message_ids(requested)
            metadata = self._store.get_message_metadata(requested)
            return [metadata[message_id] if message_id in present else None for message_id in requested]

    def search_title(
        self,
        query: str,
        *,
        only_tags: Iterable[int] | None = None,
        exclude_tags: Iterable[int] | None = None,
        max_results: int | None = None,
    ) -> list[bytes]:
        """
        The ids of LXMF messages whose Title matches `query`, filtered and limited.

        Raises:
            ConflictingFiltersError: when a tag appears in both ONLY_TAGS and EXCLUDE_TAGS.
        """
        with self._lock:
            only, exclude = self._get_search_filters(only_tags, exclude_tags)
            matches = self._store.search_title(query, only_tags=only, exclude_tags=exclude)
            return self._filter_matches(matches, only, exclude, max_results)

    def search_content(
        self,
        query: str,
        *,
        only_tags: Iterable[int] | None = None,
        exclude_tags: Iterable[int] | None = None,
        max_results: int | None = None,
    ) -> list[bytes]:
        """
        The ids of messages whose Content matches `query`, filtered and limited.

        Raises:
            ConflictingFiltersError: when a tag appears in both ONLY_TAGS and EXCLUDE_TAGS.
        """
        with self._lock:
            only, exclude = self._get_search_filters(only_tags, exclude_tags)
            matches = self._store.search_content(query, only_tags=only, exclude_tags=exclude)
            return self._filter_matches(matches, only, exclude, max_results)

    @staticmethod
    def _get_search_filters(
        only_tags: Iterable[int] | None, exclude_tags: Iterable[int] | None
    ) -> tuple[set[int], set[int]]:
        """The two tag filters as sets, refused when they overlap"""
        only = {int(tag_id) for tag_id in only_tags or ()}
        exclude = {int(tag_id) for tag_id in exclude_tags or ()}
        if only & exclude:
            raise ConflictingFiltersError()
        return only, exclude

    def _filter_matches(
        self, candidates: Iterable[bytes], only: set[int], exclude: set[int], max_results: int | None
    ) -> list[bytes]:
        """
        A match must carry all of ONLY_TAGS and none of EXCLUDE_TAGS; after which the limit applies.

        Candidates are consumed in batches and only until the limit is filled, so a lazy store
        is asked to produce no more than the answer needs.
        """
        if max_results is not None and max_results <= 0:
            return []
        results: list[bytes] = []
        candidate_iterator = iter(candidates)
        while True:
            # Pull no more than the limit still needs.
            # If filters drop candidates the loop simply pulls again.
            take = 64 if max_results is None else min(64, max_results - len(results))
            chunk = list(islice(candidate_iterator, take))
            if not chunk:
                return results
            if only or exclude:
                held = self._store.get_message_tags(chunk)
                chunk = [mid for mid in chunk if only <= held[mid] and not (exclude & held[mid])]
            results.extend(chunk)
            if max_results is not None and len(results) >= max_results:
                return results[:max_results]

    def current_states(self) -> dict[int, bytes]:
        """Every Collection's current State Token"""
        with self._lock:
            return {int(collection): self._store.get_current_token(collection) for collection in Collection}

    def sync(self, collection: int, last_known: bytes) -> tuple[dict[Any, Any], bytes]:
        """
        The Delta from `last_known` to now, and the State Token the Delta brings the client to.

        Raises:
            UnknownCollectionError: for a Collection id this model does not serve.
            UnknownStateError: when no delta can be generated from `last_known`; the client
                resyncs from the Initial State.
        """
        with self._lock:
            if collection not in Collection:
                raise UnknownCollectionError()
            token = self._store.get_current_token(collection)
            if last_known == INITIAL_STATE_TOKEN:
                return self._full_delta(collection), token
            if last_known == token:
                return make_empty_delta(collection), token
            entries = self._store.get_entries_since(collection, last_known)
            if entries is None:
                raise UnknownStateError()
            try:
                fragments = [unpack_fragment(entry.fragment) for entry in entries]
            except ValueError:
                logger.warning("Change-log for Collection %s is unreadable, forcing a full resync", collection)
                raise UnknownStateError() from None
            return compose_deltas(collection, fragments), token

    ############################################################################
    # Writes
    ############################################################################

    def ingest_raw(
        self,
        message: bytes,
    ) -> UpdatedStates:
        """Ingest a raw non-LXMF message"""

        # TODO we're already requiring a Store to not re-derive LXMF fields
        # Maybe `ingest_raw` should take `title`, `timestamp`, `metadata`, etc
        # in case an incoming message is, say, MIME and has close approximates to those fields
        # Will take a spec update though, I think

        return self.ingest(
            uuid.uuid4().bytes,
            message,
            lxmf=False,
        )

    def ingest_lxmf(
        self,
        message: LXMF.LXMessage,
    ) -> UpdatedStates:
        """Ingest a delivered LXMF message into the mailbox"""

        if not message.packed:
            # Delivered messages always carry their packed bytes
            # `message.pack()` is for outgoing mail
            raise ValueError("only a packed LXMessage can be ingested")

        return self.ingest(
            message.message_id,
            message.packed,
            lxmf=True,
        )

    def ingest(
        self,
        message_id: bytes,
        raw: bytes,
        *,
        lxmf: bool,
        tags: Iterable[int] = (),
        metadata: Mapping[Any, Any] | None = None,
    ) -> UpdatedStates:
        """
        Store a message the server itself received or sent — LXMF delivery, or an outbox copy.

        The mailbox's server-side ingest-tag and managed-metadata policies are applied on top of
        the client-provided `tags` and `metadata`.

        A message id already present is the same message redelivered: nothing changes and nothing is returned.

        Raises:
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
        """
        with self._lock:
            if self._store.get_existing_message_ids([message_id]):
                return {}
            message = StoredMessage.from_raw(message_id, raw, lxmf=lxmf)
            tag_ids = {int(tag_id) for tag_id in tags}
            tag_ids |= {int(tag_id) for tag_id in self._get_initial_tags(message)}
            self._require_tags(tag_ids)
            initial_metadata = self._get_initial_metadata(message)
            combined_metadata = {**dict(initial_metadata), **dict(metadata or {})}
            return self._add_message(message, tag_ids, combined_metadata, if_in_state=None)

    def upload(
        self,
        raws: Iterable[bytes],
        *,
        tags: Iterable[int] = (),
        metadata: Mapping[Any, Any] | None = None,
        if_in_state: Mapping[int, bytes] | None = None,
    ) -> tuple[UpdatedStates, list[bytes]]:
        """
        Store client-supplied messages opaquely, without parsing them.

        Each upload is given a fresh UUID and is stored as its own copy. `metadata` is
        client-supplied and checked; the managed-metadata policy is recorded on top of it.
        The ingest-tag policy does not apply as uploads are the client's own memos rather than mail.

        Returns:
            The updated states, and the id stored for each message in the order supplied.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
            ReservedMetadataKeyError: for a client key the server manages itself.
            InvalidMetadataKeyError: for a client key that is neither an integer nor a string.
        """
        with self._lock:
            self._check_state(if_in_state)
            tag_ids = {int(tag_id) for tag_id in tags}
            self._require_tags(tag_ids)
            client_metadata = dict(metadata or {})
            self._require_client_keys(client_metadata)

            updated: UpdatedStates = {}
            message_ids: list[bytes] = []
            message_tag_step: dict[bytes, list[int]] = {}
            metadata_step: dict[bytes, dict[Any, Any]] = {}
            changes = ChangeSet()
            for raw in raws:
                message_id = uuid.uuid4().bytes
                message_ids.append(message_id)
                message = StoredMessage.from_raw(message_id, raw, lxmf=False)
                changes.messages_added.append(message)
                if tag_ids:
                    changes.tag_pairs_added.extend((message_id, tag_id) for tag_id in tag_ids)
                    message_tag_step[message_id] = sorted(tag_ids)
                initial_metadata = self._get_initial_metadata(message)
                combined_metadata = {**dict(initial_metadata), **client_metadata}
                if combined_metadata:
                    changes.metadata_set[message_id] = dict(combined_metadata)
                    metadata_step[message_id] = dict(combined_metadata)
            if message_ids:
                steps = {int(Collection.MAIL_LIST): self._mail_list_delta(added=message_ids, deleted=[])}
                if message_tag_step:
                    steps[int(Collection.MESSAGE_TAG)] = message_tag_step
                if metadata_step:
                    steps[int(Collection.METADATA)] = metadata_step
                updated = self._commit(changes, steps)
            return updated, message_ids

    def delete(self, message_ids: Iterable[bytes], *, if_in_state: Mapping[int, bytes] | None = None) -> UpdatedStates:
        """
        Delete messages permanently, removing their MESSAGE_TAG and METADATA entries with them.

        Deleting an absent id is not an error and changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
        """
        with self._lock:
            self._check_state(if_in_state)
            requested = list(dict.fromkeys(message_ids))
            existing = self._store.get_existing_message_ids(requested)
            present = [message_id for message_id in requested if message_id in existing]
            if not present:
                return {}

            changes = ChangeSet(message_ids_deleted=list(present))
            steps: dict[int, dict[Any, Any]] = {
                int(Collection.MAIL_LIST): self._mail_list_delta(added=[], deleted=present)
            }

            # Cascade to metadata and tags
            deleted_message_tags = self._store.get_message_tags(present)
            deleted_metadata = self._store.get_message_metadata(present)

            message_tag_step: dict[Any, Any] = {mid: None for mid in present if deleted_message_tags.get(mid)}
            metadata_step: dict[Any, Any] = {mid: None for mid in present if deleted_metadata.get(mid)}
            if message_tag_step:
                steps[int(Collection.MESSAGE_TAG)] = message_tag_step
            if metadata_step:
                steps[int(Collection.METADATA)] = metadata_step
            return self._commit(changes, steps)

    def create_tags(
        self, names: Iterable[str], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> tuple[UpdatedStates, list[int]]:
        """
        Create user tags, assigning fresh positive ids.

        Tag names are unique per mailbox, compared case-insensitively: a name already in use —
        Server-Defined Tags included — returns the existing tag's id and changes nothing.

        Returns:
            The updated states, and the tag id for each name in the order supplied.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            InvalidTagNameError: for a name that is not a non-blank string.
        """
        with self._lock:
            self._check_state(if_in_state)
            requested = list(names)
            for name in requested:
                if not _valid_tag_name(name):
                    raise InvalidTagNameError()
            existing = self._store.get_all_tags()
            by_folded = {name.casefold(): tag_id for tag_id, name in existing.items()}
            next_id = max((tag_id for tag_id in existing if tag_id > 0), default=0) + 1

            created: dict[int, str] = {}
            assigned: list[int] = []
            for name in requested:
                folded = name.casefold()
                if folded in by_folded:
                    assigned.append(by_folded[folded])
                    continue
                created[next_id] = name
                by_folded[folded] = next_id
                assigned.append(next_id)
                next_id += 1

            if not created:
                return {}, assigned
            changes = ChangeSet(tags_created=created)
            return self._commit(changes, {int(Collection.TAG_LIST): dict(created)}), assigned

    def delete_tags(self, tag_ids: Iterable[int], *, if_in_state: Mapping[int, bytes] | None = None) -> UpdatedStates:
        """
        Delete user tags, removing every MESSAGE_TAG entry that references them.

        Deleting an absent id is not an error and changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            ServerDefinedTagError: for any negative tag id.
        """
        with self._lock:
            self._check_state(if_in_state)
            requested = list(dict.fromkeys(int(tag_id) for tag_id in tag_ids))
            if any(tag_id < 0 for tag_id in requested):
                raise ServerDefinedTagError()
            existing = self._store.get_all_tags()
            present = [tag_id for tag_id in requested if tag_id in existing]
            if not present:
                return {}

            changes = ChangeSet(tag_ids_deleted=present)
            steps: dict[int, dict[Any, Any]] = {int(Collection.TAG_LIST): dict.fromkeys(present, None)}
            carriers = self._store.get_messages_with_tags(present)
            affected = list({message_id for message_ids in carriers.values() for message_id in message_ids})
            held = self._store.get_message_tags(affected)
            removed = set(present)
            message_tag_step: dict[Any, Any] = {
                message_id: sorted(held[message_id] - removed) for message_id in affected
            }
            if message_tag_step:
                steps[int(Collection.MESSAGE_TAG)] = message_tag_step
            return self._commit(changes, steps)

    def rename_tags(
        self, renames: Mapping[int, str], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> UpdatedStates:
        """
        Rename user tags. Ids do not change, so the MESSAGE_TAG Collection is untouched.

        Renaming a tag to the name it already has is not an error and changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            ServerDefinedTagError: for any negative tag id.
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
            InvalidTagNameError: for a name that is not a non-blank string.
            DuplicateTagNameError: when a new name is already in use by another tag.
        """
        with self._lock:
            self._check_state(if_in_state)
            existing = self._store.get_all_tags()
            for tag_id, name in renames.items():
                if tag_id < 0:
                    raise ServerDefinedTagError()
                if tag_id not in existing:
                    raise UnknownTagError()
                if not _valid_tag_name(name):
                    raise InvalidTagNameError()
            final = {tag_id: renames.get(tag_id, name) for tag_id, name in existing.items()}
            folded_names = [name.casefold() for name in final.values()]
            if len(folded_names) != len(set(folded_names)):
                raise DuplicateTagNameError()

            effective = {tag_id: name for tag_id, name in renames.items() if existing[tag_id] != name}
            if not effective:
                return {}
            changes = ChangeSet(tags_renamed=effective)
            return self._commit(changes, {int(Collection.TAG_LIST): dict(effective)})

    def add_tags(
        self, additions: Mapping[bytes, Iterable[int]], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> UpdatedStates:
        """
        Add tags to messages. Adding a tag a message already has is not an error and changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            UnknownMessageError: for a message id not in the MAIL_LIST Collection.
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
        """
        return self._change_pairs(additions, if_in_state, adding=True)

    def remove_tags(
        self, removals: Mapping[bytes, Iterable[int]], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> UpdatedStates:
        """
        Remove tags from messages. Removing a tag a message does not have is not an error and changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            UnknownMessageError: for a message id not in the MAIL_LIST Collection.
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
        """
        return self._change_pairs(removals, if_in_state, adding=False)

    def set_metadata(
        self, entries: Mapping[bytes, Mapping[Any, Any]], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> UpdatedStates:
        """
        Merge entries into messages' Metadata Maps. Setting a key to its current value changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            UnknownMessageError: for a message id not in the MAIL_LIST Collection.
            ReservedMetadataKeyError: for a key the server manages itself.
            InvalidMetadataKeyError: for a key that is neither an integer nor a string.
        """
        with self._lock:
            self._check_state(if_in_state)
            self._require_messages(list(entries))
            for entry in entries.values():
                self._require_client_keys(entry)

            changes = ChangeSet()
            step: dict[Any, Any] = {}
            current_metadata = self._store.get_message_metadata(list(entries))
            for message_id, entry in entries.items():
                current = current_metadata[message_id]
                effective = {key: value for key, value in entry.items() if key not in current or current[key] != value}
                if effective:
                    changes.metadata_set[message_id] = effective
                    step[message_id] = {**current, **effective}
            if not step:
                return {}
            return self._commit(changes, {int(Collection.METADATA): step})

    def remove_metadata(
        self, removals: Mapping[bytes, Iterable[Any]], *, if_in_state: Mapping[int, bytes] | None = None
    ) -> UpdatedStates:
        """
        Remove entries from messages' Metadata Maps. Removing an absent key changes nothing.

        Raises:
            StateMismatchError: when `if_in_state` does not match.
            UnknownMessageError: for a message id not in the MAIL_LIST Collection.
            ReservedMetadataKeyError: for a key the server manages itself.
        """
        with self._lock:
            self._check_state(if_in_state)
            materialized = {message_id: list(keys) for message_id, keys in removals.items()}
            self._require_messages(list(materialized))
            for keys in materialized.values():
                for key in keys:
                    if key in self._managed_metadata_keys:
                        raise ReservedMetadataKeyError()

            changes = ChangeSet()
            step: dict[Any, Any] = {}
            current_metadata = self._store.get_message_metadata(list(materialized))
            for message_id, keys in materialized.items():
                current = current_metadata[message_id]
                effective = [key for key in keys if key in current]
                if effective:
                    changes.metadata_keys_removed[message_id] = effective
                    dropped = set(effective)
                    step[message_id] = {key: value for key, value in current.items() if key not in dropped}
            if not step:
                return {}
            return self._commit(changes, {int(Collection.METADATA): step})

    ############################################################################
    # Internals
    ############################################################################

    def _check_state(self, if_in_state: Mapping[int, bytes] | None) -> None:
        """Compare a supplied `IF_IN_STATE` map before any change, per the "Requests that mutate state" rules"""
        if not if_in_state:
            return
        stale: dict[int, bytes] = {}
        for collection, token in if_in_state.items():
            current = self._store.get_current_token(collection)
            if current != token:
                stale[collection] = current
        if stale:
            raise StateMismatchError(details={int(StateMismatchDetail.UPDATED_STATES): stale})

    def _require_messages(self, message_ids: Sequence[bytes]) -> None:
        """Raise unless every message exists"""
        if len(self._store.get_existing_message_ids(message_ids)) != len(set(message_ids)):
            raise UnknownMessageError()

    def _require_tags(self, tag_ids: Iterable[int]) -> None:
        """Raise unless every tag id exists"""
        existing = self._store.get_all_tags()
        for tag_id in tag_ids:
            if tag_id not in existing:
                raise UnknownTagError()

    def _require_client_keys(self, entry: Mapping[Any, Any]) -> None:
        """Raise for a client-supplied metadata key the server refuses"""
        for key in entry:
            if key in self._managed_metadata_keys:
                raise ReservedMetadataKeyError()
            if not _valid_metadata_key(key):
                raise InvalidMetadataKeyError()

    def _add_message(
        self,
        message: StoredMessage,
        tag_ids: set[int],
        metadata: dict[Any, Any],
        if_in_state: Mapping[int, bytes] | None,
    ) -> UpdatedStates:
        """Commit one new message with its initial tags and metadata. Lock held"""
        self._check_state(if_in_state)
        changes = ChangeSet(messages_added=[message])
        steps: dict[int, dict[Any, Any]] = {
            int(Collection.MAIL_LIST): self._mail_list_delta(added=[message.message_id], deleted=[])
        }
        if tag_ids:
            changes.tag_pairs_added.extend((message.message_id, tag_id) for tag_id in tag_ids)
            steps[int(Collection.MESSAGE_TAG)] = {message.message_id: sorted(tag_ids)}
        if metadata:
            changes.metadata_set[message.message_id] = metadata
            steps[int(Collection.METADATA)] = {message.message_id: dict(metadata)}
        return self._commit(changes, steps)

    def _change_pairs(
        self,
        requested: Mapping[bytes, Iterable[int]],
        if_in_state: Mapping[int, bytes] | None,
        *,
        adding: bool,
    ) -> UpdatedStates:
        """The shared shape of ADD_TAG and REMOVE_TAG"""
        with self._lock:
            self._check_state(if_in_state)
            materialized = {
                message_id: {int(tag_id) for tag_id in tag_ids} for message_id, tag_ids in requested.items()
            }
            self._require_messages(list(materialized))
            for tag_ids in materialized.values():
                self._require_tags(tag_ids)

            changes = ChangeSet()
            step: dict[Any, Any] = {}
            all_held = self._store.get_message_tags(list(materialized))
            for message_id, tag_ids in materialized.items():
                held = all_held[message_id]
                effective = (tag_ids - held) if adding else (tag_ids & held)
                if effective:
                    pairs = [(message_id, tag_id) for tag_id in effective]
                    (changes.tag_pairs_added if adding else changes.tag_pairs_removed).extend(pairs)
                    step[message_id] = sorted((held | effective) if adding else (held - effective))
            if not step:
                return {}
            return self._commit(changes, {int(Collection.MESSAGE_TAG): step})

    def _commit(self, changes: ChangeSet, steps: dict[int, dict[Any, Any]]) -> UpdatedStates:
        """Mint a token per changed Collection, record each one's step Delta, and apply atomically"""
        updated: UpdatedStates = {}
        for collection, step in steps.items():
            if is_empty_delta(collection, step):
                continue
            previous = self._store.get_current_token(collection)
            token = self._mint(previous)
            changes.new_tokens[collection] = token
            changes.log_entries[collection] = LogEntry(token_before=previous, fragment=pack_fragment(step))
            updated[collection] = TokenPair(previous, token)
        if updated:
            self._store.apply(changes)
        return updated

    @staticmethod
    def _mint(previous: bytes) -> bytes:
        """A fresh random State Token"""
        while (token := os.urandom(TOKEN_LENGTH)) == previous:
            logger.info("Either your os urandom is broken or you should buy a lottery ticket :)")
        return token

    ############################################################################
    # Deltas
    ############################################################################

    @staticmethod
    def _mail_list_delta(*, added: Sequence[bytes], deleted: Sequence[bytes]) -> dict[Any, Any]:
        """A MAIL_LIST Delta reporting the given ids"""
        return {int(MailListDeltaKey.ADDED): list(added), int(MailListDeltaKey.DELETED): list(deleted)}

    def _full_delta(self, collection: int) -> dict[Any, Any]:
        """The delta from the Initial State: the full current state in delta shape"""
        if collection == Collection.MAIL_LIST:
            return self._mail_list_delta(added=self._store.get_all_message_ids(), deleted=[])
        if collection == Collection.TAG_LIST:
            return dict(self._store.get_all_tags())
        if collection == Collection.MESSAGE_TAG:
            held = self._store.get_message_tags(self._store.get_all_message_ids())
            return {message_id: sorted(tag_ids) for message_id, tag_ids in held.items() if tag_ids}
        metadata = self._store.get_message_metadata(self._store.get_all_message_ids())
        return {message_id: entries for message_id, entries in metadata.items() if entries}
