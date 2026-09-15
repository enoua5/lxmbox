"""
Thin wrapper around a msgpack implementation.

We chose `u-msgpack-python` because it's the same implementation found in `RNS.vendor.umsgpack`.

We could also replace this with `msgpack` or use `RNS.vendor.umsgpack`.
"""

import io
from typing import Any, cast

import umsgpack

from .errors import MalformedExchangeError

__all__ = ["pack", "unpack"]

_PACK_FAILURES = (umsgpack.PackException, ValueError, TypeError)
_UNPACK_FAILURES = (umsgpack.UnpackException, ValueError, TypeError)


def pack(value: object) -> bytes:
    """
    Encode `value` as msgpack.

    Raises:
        MalformedExchangeError: if `value` contains something msgpack cannot represent.
    """
    try:
        return cast(bytes, umsgpack.packb(value))
    except _PACK_FAILURES as exc:
        raise MalformedExchangeError(message=f"could not encode as msgpack: {exc}") from exc


def unpack(data: bytes) -> Any:
    """
    Decode a msgpack value from `data`.

    Raises:
        MalformedExchangeError: if `data` is not exactly one well-formed msgpack value.
    """
    stream = io.BytesIO(data)
    try:
        value = umsgpack.unpack(stream)
    except _UNPACK_FAILURES as exc:
        raise MalformedExchangeError(message=f"could not decode msgpack: {exc}") from exc

    # umsgpack doesn't require the full stream to be consumed
    # but for us, that means a message was packed wrong
    remaining = len(data) - stream.tell()
    if remaining:
        raise MalformedExchangeError(message=f"{remaining} trailing byte(s) after a complete msgpack value")
    return value
