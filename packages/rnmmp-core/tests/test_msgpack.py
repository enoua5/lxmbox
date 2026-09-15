"""Tests for the msgpack wrapper"""

import pytest

from rnmmp_core import MalformedExchangeError, pack, unpack

REPRESENTATIVE_VALUES = [
    pytest.param(None, id="nil"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
    pytest.param(0, id="zero"),
    pytest.param(127, id="positive-fixint-max"),
    pytest.param(-1, id="negative-int"),
    pytest.param(-10, id="server-tag-id"),
    pytest.param(2**63 - 1, id="large-int"),
    pytest.param("", id="empty-string"),
    pytest.param("a title", id="string"),
    pytest.param("¥ € 🜁", id="non-ascii-string"),
    pytest.param(b"", id="initial-state-token"),
    pytest.param(b"\x00\xff", id="bytes"),
    pytest.param([], id="empty-list"),
    pytest.param([1, "two", b"\x03", None], id="mixed-list"),
    pytest.param({}, id="empty-map"),
    pytest.param({0: b"\x01", 1: b"\x02"}, id="integer-keyed-map"),
    pytest.param({-1: [1, 2], -4: []}, id="negative-integer-keyed-map"),
    pytest.param({0: {1: [b"\x01", {2: "nested"}]}}, id="nested"),
]


class TestRoundTrip:
    """Values survive a pack/unpack cycle unchanged"""

    @pytest.mark.parametrize("value", REPRESENTATIVE_VALUES)
    def test_a_value_survives_a_round_trip(self, value: object) -> None:
        """Unpacking a packed value reproduces it"""
        assert unpack(pack(value)) == value

    @pytest.mark.parametrize("value", REPRESENTATIVE_VALUES)
    def test_packing_is_deterministic(self, value: object) -> None:
        """The same value packs to the same bytes every time"""
        assert pack(value) == pack(value)


class TestProtocolRequirements:
    """Properties rnmmp requires of its encoder, pinned so an encoder swap cannot quietly change the protocol"""

    def test_integer_map_keys_stay_integers(self) -> None:
        """Keyed Parameters, error information and Collection maps are all keyed by integer"""
        unpacked = unpack(pack({0: "a", 1: "b"}))

        assert unpacked == {0: "a", 1: "b"}
        assert all(isinstance(key, int) for key in unpacked)

    def test_negative_integer_map_keys_stay_integers(self) -> None:
        """Server-Defined Tags take negative ids, so they appear as negative keys"""
        assert list(unpack(pack({-1: "unread"}))) == [-1]

    def test_strings_and_bytes_stay_distinct(self) -> None:
        """A Title is a string and a State Token is bytes; the two MUST NOT be conflated"""
        assert pack("x") != pack(b"x")
        assert isinstance(unpack(pack("x")), str)
        assert isinstance(unpack(pack(b"x")), bytes)

    def test_the_initial_state_token_is_a_zero_length_bin(self) -> None:
        """The zero-length byte array (msgpack `0xC4 0x00`) is reserved to represent the Initial State"""
        assert pack(b"") == b"\xc4\x00"


class TestRejection:
    """Input the wrapper refuses, rather than passing on something half-read"""

    def test_trailing_bytes_are_rejected(self) -> None:
        """A frame carrying two values is rejected, not read as its first value"""
        with pytest.raises(MalformedExchangeError, match="trailing"):
            unpack(pack(1) + pack(2))

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(b"", id="empty"),
            pytest.param(b"\xc4\x10\x01", id="truncated-bin"),
            pytest.param(b"\x93\x01", id="truncated-array"),
            pytest.param(b"\xc1", id="reserved-byte"),
        ],
    )
    def test_undecodable_bytes_are_rejected(self, data: bytes) -> None:
        """Bytes that are not one well-formed msgpack value raise rather than returning a partial result"""
        with pytest.raises(MalformedExchangeError):
            unpack(data)

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({1, 2}, id="set"),
            pytest.param(object(), id="arbitrary-object"),
            pytest.param(lambda: None, id="function"),
        ],
    )
    def test_unrepresentable_values_are_rejected(self, value: object) -> None:
        """A value msgpack cannot represent is an error at the encoder, not a silent omission"""
        with pytest.raises(MalformedExchangeError):
            pack(value)


class TestEncoderIdentity:
    """Byte identity with the encoder Reticulum vendors. Skips when RNS is absent; it is not a dependency"""

    @pytest.mark.parametrize("value", REPRESENTATIVE_VALUES)
    def test_packing_matches_the_vendored_encoder(self, value: object) -> None:
        """Our encoder produces exactly what `RNS.vendor.umsgpack` would have produced"""
        vendored = pytest.importorskip("RNS.vendor.umsgpack")

        assert pack(value) == vendored.packb(value)

    @pytest.mark.parametrize("value", REPRESENTATIVE_VALUES)
    def test_unpacking_accepts_the_vendored_encoder(self, value: object) -> None:
        """Anything Reticulum packs, we read back identically"""
        vendored = pytest.importorskip("RNS.vendor.umsgpack")

        assert unpack(vendored.packb(value)) == value
