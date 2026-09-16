"""
The storage-agnostic mailbox model: implements mailbox state rules, over any implemention of `Store`.

Writes are serialized per mailbox with a lock so a `Store` never sees two concurrent writes.

Everything raised here is an `RnmmpError`, ready for `Response.failure(request_id, error, request_type)`.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
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

from .store import ChangeSet, LogEntry, Store, StoredMessage

__all__ = [
    "RESERVED_METADATA_KEYS",
    "SERVER_DEFINED_TAG_NAMES",
    "TOKEN_LENGTH",
    "MailboxModel",
    "TokenPair",
    "UpdatedStates",
]

logger = logging.getLogger(__name__)

SERVER_DEFINED_TAG_NAMES: Final[dict[int, str]] = {tag.value: tag.name for tag in ServerTag}
"""The Server-Defined Tags every mailbox holds, named as the specification names them"""

RESERVED_METADATA_KEYS: Final[frozenset[Any]] = frozenset({int(MetadataKey.RECEIVE_TIME)})
"""Metadata keys the server manages itself and refuses from clients"""

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

    def __init__(self, store: Store) -> None:
        """
        Initialize a mailbox with data stored using `store`,
        seeding the Server-Defined Tags if they are not present.

        Seeding changes no State Token: the Initial State of the TAG_LIST Collection already
        includes the Server-Defined Tags.
        """
        self._store = store
        self._lock = threading.Lock()
        missing = {tag_id: name for tag_id, name in SERVER_DEFINED_TAG_NAMES.items() if tag_id not in store.all_tags()}
        if missing:
            store.apply(ChangeSet(tags_created=missing))

    ############################################################################
    # Reads
    ############################################################################

    def message_ids(self) -> list[bytes]:
        """The MAIL_LIST Collection"""
        with self._lock:
            return self._store.all_message_ids()

    def get_messages(self, message_ids: Iterable[bytes]) -> list[StoredMessage | None]:
        """The stored messages, in the order requested, `None` for each id not present"""
        with self._lock:
            return self._store.get_messages(list(message_ids))

    def tags(self) -> dict[int, str]:
        """The TAG_LIST Collection"""
        with self._lock:
            return self._store.all_tags()

    def tags_of(self, message_ids: Iterable[bytes]) -> list[list[int] | None]:
        """Each message's tag ids, in the order requested: `None` for a missing message, `[]` for an untagged one"""
        with self._lock:
            requested = list(message_ids)
            present = self._store.existing_message_ids(requested)
            held = self._store.message_tags(requested)
            return [sorted(held[message_id]) if message_id in present else None for message_id in requested]

    def metadata_of(self, message_ids: Iterable[bytes]) -> list[dict[Any, Any] | None]:
        """Each message's metadata map, in the order requested: `None` for a missing message, `{}` for a bare one"""
        with self._lock:
            requested = list(message_ids)
            present = self._store.existing_message_ids(requested)
            metadata = self._store.message_metadata(requested)
            return [metadata[message_id] if message_id in present else None for message_id in requested]

    def current_states(self) -> dict[int, bytes]:
        """Every Collection's current State Token"""
        with self._lock:
            return {int(collection): self._store.current_token(collection) for collection in Collection}

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
            token = self._store.current_token(collection)
            if last_known == INITIAL_STATE_TOKEN:
                return self._full_delta(collection), token
            if last_known == token:
                return self._empty_delta(collection), token
            entries = self._store.entries_since(collection, last_known)
            if entries is None:
                raise UnknownStateError()
            return self._folded_delta(collection, entries), token

    ############################################################################
    # Writes
    ############################################################################

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

        Server-side, so `metadata` is trusted (this is how `RECEIVE_TIME` gets set). A message id
        already present is the same message redelivered: nothing changes and nothing is returned.

        Raises:
            UnknownTagError: for a tag id not in the TAG_LIST Collection.
        """
        with self._lock:
            if self._store.existing_message_ids([message_id]):
                return {}
            tag_ids = {int(tag_id) for tag_id in tags}
            self._require_tags(tag_ids)
            return self._add_message(
                StoredMessage(message_id, raw, lxmf=lxmf), tag_ids, dict(metadata or {}), if_in_state=None
            )

    def upload(
        self,
        raws: Iterable[bytes],
        *,
        tags: Iterable[int] = (),
        metadata: Mapping[Any, Any] | None = None,
        server_metadata: Mapping[Any, Any] | None = None,
        if_in_state: Mapping[int, bytes] | None = None,
    ) -> tuple[UpdatedStates, list[bytes]]:
        """
        Store client-supplied messages opaquely, without parsing them.

        Each upload takes a fresh UUID id and is stored as its own copy.
        `metadata` is client-supplied and checked;
        `server_metadata` is the server's own additions, unchecked.

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
            combined = {**client_metadata, **dict(server_metadata or {})}

            updated: UpdatedStates = {}
            message_ids: list[bytes] = []
            mail_priors: dict[Any, Any] = {}
            pair_priors: dict[Any, Any] = {}
            metadata_priors: dict[Any, Any] = {}
            changes = ChangeSet()
            for raw in raws:
                message_id = uuid.uuid4().bytes
                message_ids.append(message_id)
                changes.messages_added.append(StoredMessage(message_id, raw, lxmf=False))
                mail_priors[message_id] = False
                if tag_ids:
                    changes.tag_pairs_added.extend((message_id, tag_id) for tag_id in tag_ids)
                    pair_priors[message_id] = frozenset()
                if combined:
                    changes.metadata_set[message_id] = dict(combined)
                    metadata_priors[message_id] = {}
            if message_ids:
                priors = {int(Collection.MAIL_LIST): mail_priors}
                if pair_priors:
                    priors[int(Collection.MESSAGE_TAG)] = pair_priors
                if metadata_priors:
                    priors[int(Collection.METADATA)] = metadata_priors
                updated = self._commit(changes, priors)
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
            existing = self._store.existing_message_ids(requested)
            present = [message_id for message_id in requested if message_id in existing]
            changes = ChangeSet(message_ids_deleted=list(present))
            priors: dict[int, dict[Any, Any]] = {int(Collection.MAIL_LIST): dict.fromkeys(present, True)}
            held = self._store.message_tags(present)
            held_metadata = self._store.message_metadata(present)
            pair_priors: dict[Any, Any] = {mid: frozenset(held[mid]) for mid in present if held[mid]}
            metadata_priors: dict[Any, Any] = {mid: held_metadata[mid] for mid in present if held_metadata[mid]}
            if pair_priors:
                priors[int(Collection.MESSAGE_TAG)] = pair_priors
            if metadata_priors:
                priors[int(Collection.METADATA)] = metadata_priors
            if not present:
                return {}
            return self._commit(changes, priors)

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
            existing = self._store.all_tags()
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
            priors = {int(Collection.TAG_LIST): dict.fromkeys(created, None)}
            return self._commit(changes, priors), assigned

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
            existing = self._store.all_tags()
            present = [tag_id for tag_id in requested if tag_id in existing]
            if not present:
                return {}

            changes = ChangeSet(tag_ids_deleted=present)
            priors: dict[int, dict[Any, Any]] = {
                int(Collection.TAG_LIST): {tag_id: existing[tag_id] for tag_id in present}
            }
            carriers = self._store.messages_with_tags(present)
            affected = list({message_id for message_ids in carriers.values() for message_id in message_ids})
            held = self._store.message_tags(affected)
            pair_priors: dict[Any, Any] = {message_id: frozenset(held[message_id]) for message_id in affected}
            if pair_priors:
                priors[int(Collection.MESSAGE_TAG)] = pair_priors
            return self._commit(changes, priors)

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
            existing = self._store.all_tags()
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
            priors = {int(Collection.TAG_LIST): {tag_id: existing[tag_id] for tag_id in effective}}
            return self._commit(changes, priors)

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
            priors: dict[Any, Any] = {}
            current_metadata = self._store.message_metadata(list(entries))
            for message_id, entry in entries.items():
                current = current_metadata[message_id]
                effective = {key: value for key, value in entry.items() if key not in current or current[key] != value}
                if effective:
                    changes.metadata_set[message_id] = effective
                    priors[message_id] = current
            if not priors:
                return {}
            return self._commit(changes, {int(Collection.METADATA): priors})

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
                    if key in RESERVED_METADATA_KEYS:
                        raise ReservedMetadataKeyError()

            changes = ChangeSet()
            priors: dict[Any, Any] = {}
            current_metadata = self._store.message_metadata(list(materialized))
            for message_id, keys in materialized.items():
                current = current_metadata[message_id]
                effective = [key for key in keys if key in current]
                if effective:
                    changes.metadata_keys_removed[message_id] = effective
                    priors[message_id] = current
            if not priors:
                return {}
            return self._commit(changes, {int(Collection.METADATA): priors})

    ############################################################################
    # Internals
    ############################################################################

    def _check_state(self, if_in_state: Mapping[int, bytes] | None) -> None:
        """Compare a supplied `IF_IN_STATE` map before any change, per the "Requests that mutate state" rules"""
        if not if_in_state:
            return
        stale: dict[int, bytes] = {}
        for collection, token in if_in_state.items():
            current = self._store.current_token(collection)
            if current != token:
                stale[collection] = current
        if stale:
            raise StateMismatchError(details={int(StateMismatchDetail.UPDATED_STATES): stale})

    def _require_messages(self, message_ids: Sequence[bytes]) -> None:
        """Raise unless every message exists"""
        if len(self._store.existing_message_ids(message_ids)) != len(set(message_ids)):
            raise UnknownMessageError()

    def _require_tags(self, tag_ids: Iterable[int]) -> None:
        """Raise unless every tag id exists"""
        existing = self._store.all_tags()
        for tag_id in tag_ids:
            if tag_id not in existing:
                raise UnknownTagError()

    def _require_client_keys(self, entry: Mapping[Any, Any]) -> None:
        """Raise for a client-supplied metadata key the server refuses"""
        for key in entry:
            if key in RESERVED_METADATA_KEYS:
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
        priors: dict[int, dict[Any, Any]] = {int(Collection.MAIL_LIST): {message.message_id: False}}
        if tag_ids:
            changes.tag_pairs_added.extend((message.message_id, tag_id) for tag_id in tag_ids)
            priors[int(Collection.MESSAGE_TAG)] = {message.message_id: frozenset()}
        if metadata:
            changes.metadata_set[message.message_id] = metadata
            priors[int(Collection.METADATA)] = {message.message_id: {}}
        return self._commit(changes, priors)

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
            priors: dict[Any, Any] = {}
            all_held = self._store.message_tags(list(materialized))
            for message_id, tag_ids in materialized.items():
                held = all_held[message_id]
                effective = (tag_ids - held) if adding else (tag_ids & held)
                if effective:
                    pairs = [(message_id, tag_id) for tag_id in effective]
                    (changes.tag_pairs_added if adding else changes.tag_pairs_removed).extend(pairs)
                    priors[message_id] = frozenset(held)
            if not priors:
                return {}
            return self._commit(changes, {int(Collection.MESSAGE_TAG): priors})

    def _commit(self, changes: ChangeSet, priors: dict[int, dict[Any, Any]]) -> UpdatedStates:
        """Mint a token per changed Collection, record the log entries, and apply atomically"""
        updated: UpdatedStates = {}
        for collection, changed in priors.items():
            if not changed:
                continue
            previous = self._store.current_token(collection)
            token = self._mint(previous)
            changes.new_tokens[collection] = token
            changes.log_entries[collection] = LogEntry(token_before=previous, priors=changed)
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
    def _empty_delta(collection: int) -> dict[Any, Any]:
        """The delta between a state and itself"""
        if collection == Collection.MAIL_LIST:
            return {int(MailListDeltaKey.ADDED): [], int(MailListDeltaKey.DELETED): []}
        return {}

    def _full_delta(self, collection: int) -> dict[Any, Any]:
        """The delta from the Initial State: the full current state in delta shape"""
        if collection == Collection.MAIL_LIST:
            return {int(MailListDeltaKey.ADDED): self._store.all_message_ids(), int(MailListDeltaKey.DELETED): []}
        if collection == Collection.TAG_LIST:
            return dict(self._store.all_tags())
        if collection == Collection.MESSAGE_TAG:
            held = self._store.message_tags(self._store.all_message_ids())
            return {message_id: sorted(tag_ids) for message_id, tag_ids in held.items() if tag_ids}
        metadata = self._store.message_metadata(self._store.all_message_ids())
        return {message_id: entries for message_id, entries in metadata.items() if entries}

    def _folded_delta(self, collection: int, entries: list[LogEntry]) -> dict[Any, Any]:
        """
        Fold log entries into one delta: each key's value *then* is its earliest recorded prior,
        compared against its value *now* — so intermediary states never appear, and a key whose
        value came back around is omitted.
        """
        at_then: dict[Any, Any] = {}
        for entry in entries:
            for key, prior in entry.priors.items():
                at_then.setdefault(key, prior)

        touched = list(at_then)

        if collection == Collection.MAIL_LIST:
            present = self._store.existing_message_ids(touched)
            added = [mid for mid, existed in at_then.items() if not existed and mid in present]
            deleted = [mid for mid, existed in at_then.items() if existed and mid not in present]
            return {int(MailListDeltaKey.ADDED): added, int(MailListDeltaKey.DELETED): deleted}

        if collection == Collection.TAG_LIST:
            names = self._store.all_tags()
            return {
                tag_id: names.get(tag_id) for tag_id, name_then in at_then.items() if names.get(tag_id) != name_then
            }

        present = self._store.existing_message_ids(touched)

        if collection == Collection.MESSAGE_TAG:
            all_held = self._store.message_tags(touched)
            delta: dict[Any, Any] = {}
            for message_id, tags_then in at_then.items():
                exists = message_id in present
                tags_now = frozenset(all_held[message_id]) if exists else frozenset()
                if tags_now != tags_then:
                    delta[message_id] = sorted(tags_now) if exists else None
            return delta

        all_metadata = self._store.message_metadata(touched)
        delta = {}
        for message_id, metadata_then in at_then.items():
            exists = message_id in present
            metadata_now = all_metadata[message_id] if exists else {}
            if metadata_now != metadata_then:
                delta[message_id] = metadata_now if exists else None
        return delta
