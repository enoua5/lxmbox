"""
The typed results `MailboxLink` calls return.

The multi-value results are `NamedTuple`s, so they still unpack positionally, while providing field names
"""

from __future__ import annotations

from typing import Any, NamedTuple

__all__ = [
    "Capabilities",
    "CollectionDelta",
    "CollectionSync",
    "CreatedTags",
    "TokenChange",
    "UpdatedStates",
    "UploadResult",
]


class TokenChange(NamedTuple):
    """One Collection's State Token change from a write"""

    previous: bytes
    """
    The previous State Token for the state the Collection was in,
    which the client can use to determine if a fast-forward is safe
    """
    new: bytes
    """
    The new State Token for the state the the Collection is in,
    which the client can set as current after a fast-forward or sync
    """


type UpdatedStates = dict[int, TokenChange]
"""Collection id → the State Token change"""


class Capabilities(NamedTuple):
    """What CAPABILITY returns"""

    version: int
    """The protocol version the server uses"""
    features: list[Any]
    """The rest of the Capability List: optional feature codes, extension strings, feature-variant pairs"""


class CollectionDelta(NamedTuple):
    """A Collection's SYNC response"""

    delta: dict[Any, Any]
    """The Delta to apply to the state the last known token named"""
    state: bytes
    """The State Token the Delta brings the client to"""


class CollectionSync(NamedTuple):
    """One Collection's outcome from a multi-Collection `sync`"""

    delta: dict[Any, Any]
    """The Delta to apply to the state the last known token named"""
    state: bytes
    """The State Token the Delta brings the client to"""
    full_resync: bool
    """
    Whether the server no longer knew the client's token,
    forcing Delta to be from the Initial State
    """


class UploadResult(NamedTuple):
    """What UPLOAD returns"""

    updated_states: UpdatedStates
    message_ids: list[bytes]
    """The id assigned to each uploaded message, in the order supplied"""


class CreatedTags(NamedTuple):
    """What CREATE_TAG returns"""

    updated_states: UpdatedStates
    tag_ids: list[int]
    """The id for each name, in the order supplied"""
