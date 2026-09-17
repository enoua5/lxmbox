"""Tests for the content-class portion slicing of stored LXMF messages."""

import pytest

from rnmmp_core import pack
from rnmmp_server import lxmf_portions

HEAD = bytes(range(48)) * 2
PAYLOAD = pack([1757900000.5, b"the title", b"the content", {7: b"field"}])


class TestSlicing:
    """Payload, Content and Fields from a packed message."""

    def test_payload_is_everything_after_the_head(self) -> None:
        """FETCH_PAYLOAD returns the packed `[Timestamp, Title, Content, Fields]`, raw."""
        assert lxmf_portions.payload(HEAD + PAYLOAD) == PAYLOAD

    def test_content_and_fields_decode_from_the_payload(self) -> None:
        """FETCH_CONTENT and FETCH_FIELDS return their decoded portions."""
        raw = HEAD + PAYLOAD

        assert lxmf_portions.content(raw) == b"the content"
        assert lxmf_portions.fields(raw) == {7: b"field"}

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(HEAD, id="no-payload"),
            pytest.param(HEAD[:40], id="shorter-than-a-head"),
            pytest.param(HEAD + b"\xc1 not msgpack", id="undecodable-payload"),
            pytest.param(HEAD + pack([1.0, b"t"]), id="too-few-parts"),
        ],
    )
    def test_anything_that_is_not_lxmf_shaped_slices_to_nothing(self, raw: bytes) -> None:
        """A message that does not decode as LXMF answers like one that is not LXMF."""
        assert lxmf_portions.content(raw) is None
        assert lxmf_portions.fields(raw) is None

    def test_portions_are_validated_individually(self) -> None:
        """A mistyped portion is `nil` on its own; the others still answer."""
        raw = HEAD + pack([1.0, b"t", "not bytes", {7: b"field"}])

        assert lxmf_portions.content(raw) is None
        assert lxmf_portions.fields(raw) == {7: b"field"}
