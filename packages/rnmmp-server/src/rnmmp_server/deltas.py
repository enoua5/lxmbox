"""
Utils for handling deltas:
- pack and unpack for storage
- compose for sync
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rnmmp_core import Collection, MailListDeltaKey, MalformedExchangeError, pack, unpack

__all__ = ["compose_deltas", "make_empty_delta", "is_empty_delta", "pack_fragment", "unpack_fragment"]


def pack_fragment(delta: Mapping[Any, Any]) -> bytes:
    """A Delta packed for a `Store` to hold as opaque bytes"""
    return pack(dict(delta))


def unpack_fragment(fragment: bytes) -> dict[Any, Any]:
    """
    The Delta a fragment holds.

    Raises:
        ValueError: when the fragment is not a packed Delta
    """
    try:
        delta = unpack(fragment)
    except MalformedExchangeError as error:
        raise ValueError(f"change-log fragment is not msgpack: {error}") from error
    if not isinstance(delta, dict):
        raise ValueError(f"change-log fragment is not a Delta, but {type(delta).__name__}")
    return delta


def make_empty_delta(collection: int) -> dict[Any, Any]:
    """Create a Delta representing no change"""
    if collection == Collection.MAIL_LIST:
        return {int(MailListDeltaKey.ADDED): [], int(MailListDeltaKey.DELETED): []}
    return {}


def is_empty_delta(collection: int, delta: Mapping[Any, Any]) -> bool:
    """Whether a Delta carries no change"""
    if collection == Collection.MAIL_LIST:
        return not delta.get(int(MailListDeltaKey.ADDED)) and not delta.get(int(MailListDeltaKey.DELETED))
    return not delta


def compose_deltas(collection: int, deltas: Iterable[Mapping[Any, Any]]) -> dict[Any, Any]:
    """Create a Delta representing the combined effect of `deltas`, which are applied in order"""
    if collection == Collection.MAIL_LIST:
        return _compose_mail_list(deltas)

    # Every other Collection keys its Delta by entry, with new values overwriting old ones entirely
    composed: dict[Any, Any] = {}
    for delta in deltas:
        composed.update(delta)
    return composed


def _compose_mail_list(deltas: Iterable[Mapping[int, list[bytes]]]) -> dict[int, list[bytes]]:
    """Compose MAIL_LIST Deltas as existence per Message id, keeping ADDED and DELETED disjoint"""
    added_key = int(MailListDeltaKey.ADDED)
    deleted_key = int(MailListDeltaKey.DELETED)

    exists: dict[bytes, bool] = {}
    for delta in deltas:
        for message_id in delta.get(deleted_key, []):
            exists[message_id] = False
        for message_id in delta.get(added_key, []):
            exists[message_id] = True

    return {
        added_key: [message_id for message_id, present in exists.items() if present],
        deleted_key: [message_id for message_id, present in exists.items() if not present],
    }
