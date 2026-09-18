"""
Pure client-state helpers.

State shapes/containers:
- MAIL_LIST is a `set` of message ids
- TAG_LIST is a `dict` mapping tag id to name
- MESSAGE_TAG is a `dict` mapping message id to a `frozenset` of tag ids
- METADATA is a `dict` mapping message id to its metadata `dict`
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rnmmp_core import Collection, MailListDeltaKey

__all__ = ["apply_delta", "initial_state"]


def initial_state(collection: Collection) -> Any:
    """
    The initial empty state of a mailbox.

    Raises:
        ValueError: for a Collection this client has no state logic for
    """
    match collection:
        case Collection.MAIL_LIST:
            return set()
        case Collection.TAG_LIST | Collection.MESSAGE_TAG | Collection.METADATA:
            return {}
        case _:
            raise ValueError(f"No state shape known for Collection {collection}")


def apply_delta(collection: Collection, state: Any, delta: dict[Any, Any]) -> Any:
    """
    Calculate the state after applying `delta` to `state`.
    Returns the new state without mutating input.

    Raises:
        ValueError: for a Collection this client has no state logic for
    """
    match collection:
        case Collection.MAIL_LIST:
            # Just add/remove the ids
            added = set(delta.get(int(MailListDeltaKey.ADDED), ()))
            deleted = set(delta.get(int(MailListDeltaKey.DELETED), ()))
            return (set(state) | added) - deleted
        case Collection.TAG_LIST:
            # Values are tag names; `""` is a valid tag name
            return _apply_map_delta(state, delta, empty_means_absent=False)
        case Collection.MESSAGE_TAG:
            # Values are each message's entry set; `nil` and `[]` are the same
            return _apply_map_delta(state, delta, empty_means_absent=True, convert=frozenset)
        case Collection.METADATA:
            # Values are each message's entry map; `nil` and `{}` are the same
            return _apply_map_delta(state, delta, empty_means_absent=True)
        case _:
            raise ValueError(f"No delta semantics known for Collection {collection}")


def _apply_map_delta(
    state: Any,
    delta: dict[Any, Any],
    *,
    empty_means_absent: bool,
    convert: Callable[[Any], Any] | None = None,
) -> dict[Any, Any]:
    """Set changed values, drop what no longer exists"""

    updated = dict(state)

    for key, value in delta.items():
        cleared = value is None or (empty_means_absent and not value)
        if cleared:
            updated.pop(key, None)
        else:
            updated[key] = value if convert is None else convert(value)
    return updated
