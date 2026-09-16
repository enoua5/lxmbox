"""
The portions of a stored LXMF message, segmented for the FETCH_* request types.

A packed LXMF message has a header consisting of `destination hash`, `source hash`, and `signature`
followed by the body consisting of the msgpack encoded `timestamp`, `title`, `content`, and `fields` portions.

Only the content portions are sliced here; the indexed ones (head, timestamp, title)
are extracted once at write time by `StoredMessage.from_raw`.
"""

from __future__ import annotations

from typing import Any, Final

from LXMF import LXMessage

from rnmmp_core import unpack

__all__ = ["content", "fields", "payload"]

HEAD_LENGTH: Final[int] = 2 * LXMessage.DESTINATION_LENGTH + LXMessage.SIGNATURE_LENGTH
"""Destination hash, source hash and signature, as FETCH_HEAD returns them"""


def payload(raw: bytes) -> bytes | None:
    """Every byte after the head: the packed `[Timestamp, Title, Content, Fields]`, raw"""
    return raw[HEAD_LENGTH:] if len(raw) > HEAD_LENGTH else None


def _parts(raw: bytes) -> list[Any] | None:
    """The decoded payload, or `None` for anything that does not decode as one"""
    packed = payload(raw)
    if packed is None:
        return None
    try:
        parts = unpack(packed)
    except Exception:
        return None
    return parts if isinstance(parts, list) and len(parts) >= 4 else None


def content(raw: bytes) -> bytes | None:
    """The message's Content"""
    parts = _parts(raw)
    return parts[2] if parts is not None and isinstance(parts[2], bytes) else None


def fields(raw: bytes) -> dict[Any, Any] | None:
    """The message's Fields, decoded as a Map"""
    parts = _parts(raw)
    return parts[3] if parts is not None and isinstance(parts[3], dict) else None
