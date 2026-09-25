"""
Tests for the server's Delta-fragment helpers
"""

import pytest

from rnmmp_core import Collection, MailListDeltaKey, pack
from rnmmp_server import compose_deltas, is_empty_delta, make_empty_delta, pack_fragment, unpack_fragment

MID = b"\x11" * 32
OTHER_MID = b"\x22" * 32
ADDED = int(MailListDeltaKey.ADDED)
DELETED = int(MailListDeltaKey.DELETED)

ALL_COLLECTIONS = [pytest.param(collection, id=collection.name) for collection in Collection]


class TestFragments:
    """Packing Deltas for a Store to hold opaquely"""

    def test_a_fragment_is_the_packed_delta(self) -> None:
        """
        A fragment is the Delta as plain msgpack

        The bytes are derived from the msgpack specification:
        a two-entry fixmap of the list keys, each holding an empty fixarray
        """
        assert pack_fragment({ADDED: [], DELETED: []}) == b"\x82\x00\x90\x01\x90"

    def test_a_fragment_round_trips(self) -> None:
        """Unpacking a packed Delta returns it unchanged, nils and byte keys included"""
        delta = {MID: [1, -1], OTHER_MID: None}

        assert unpack_fragment(pack_fragment(delta)) == delta

    def test_bytes_that_are_not_msgpack_raise(self) -> None:
        """A fragment that does not decode is rejected"""
        with pytest.raises(ValueError):
            unpack_fragment(b"\xc1")

    def test_a_packed_non_map_raises(self) -> None:
        """Well-formed msgpack that is not a Delta map is rejected"""
        with pytest.raises(ValueError):
            unpack_fragment(pack([1, 2]))

    def test_trailing_bytes_raise(self) -> None:
        """A fragment must be exactly one packed value"""
        with pytest.raises(ValueError):
            unpack_fragment(pack({}) + b"\x00")


class TestEmptyDeltas:
    """The no-change Delta and its recognition"""

    def test_the_empty_mail_list_delta_carries_both_lists(self) -> None:
        """MAIL_LIST always has its ADDED and DELETED keys"""
        assert make_empty_delta(Collection.MAIL_LIST) == {ADDED: [], DELETED: []}

    @pytest.mark.parametrize(
        "collection", [Collection.TAG_LIST, Collection.MESSAGE_TAG, Collection.METADATA], ids=lambda c: c.name
    )
    def test_the_other_collections_are_empty_maps(self, collection: Collection) -> None:
        """The keyed Collections are just an empty dict when empty"""
        assert make_empty_delta(collection) == {}

    @pytest.mark.parametrize("collection", ALL_COLLECTIONS)
    def test_the_empty_delta_is_recognized_as_empty(self, collection: Collection) -> None:
        """`make_empty_delta` passes `is_empty_delta` check"""
        assert is_empty_delta(collection, make_empty_delta(collection))

    @pytest.mark.parametrize("delta", [{}, {ADDED: []}, {DELETED: []}])
    def test_a_mail_list_delta_with_missing_keys_is_still_empty(self, delta: dict[int, list[bytes]]) -> None:
        """A missing list means the same as an empty one"""
        assert is_empty_delta(Collection.MAIL_LIST, delta)

    @pytest.mark.parametrize(
        ("collection", "delta"),
        [
            pytest.param(Collection.MAIL_LIST, {ADDED: [MID], DELETED: []}, id="an-addition"),
            pytest.param(Collection.MAIL_LIST, {ADDED: [], DELETED: [MID]}, id="a-deletion"),
            pytest.param(Collection.TAG_LIST, {1: "Name"}, id="a-name"),
            pytest.param(Collection.MESSAGE_TAG, {MID: None}, id="a-nil-entry"),
        ],
    )
    def test_any_content_is_not_empty(self, collection: Collection, delta: dict[int, object]) -> None:
        """A Delta with any entry — a nil entry included — is a change"""
        assert not is_empty_delta(collection, delta)


class TestComposition:
    """Combining step Deltas into one SYNC Delta"""

    @pytest.mark.parametrize("collection", ALL_COLLECTIONS)
    def test_no_deltas_compose_to_no_change(self, collection: Collection) -> None:
        """An empty history is the empty Delta"""
        assert compose_deltas(collection, []) == make_empty_delta(collection)

    def test_test_last_name_is_used(self) -> None:
        """
        Intermediary states MUST NOT be represented

        If a tag is renamed multiple times, only the current name is shown
        """
        composed = compose_deltas(Collection.TAG_LIST, [{1: "First"}, {1: "Second"}, {1: "Third"}])

        assert composed == {1: "Third"}

    def test_multiple_renames_are_merged(self) -> None:
        """A key that isn't overwritten stays and gets merged"""
        composed = compose_deltas(Collection.TAG_LIST, [{1: "One"}, {2: "Two"}])

        assert composed == {1: "One", 2: "Two"}

    def test_nil_overwrites_names(self) -> None:
        """For tags that have been deleted since the Last Known State, the value is nil"""
        composed = compose_deltas(Collection.TAG_LIST, [{1: "Doomed"}, {1: None}])

        assert composed == {1: None}

    @pytest.mark.parametrize("collection", [Collection.MESSAGE_TAG, Collection.METADATA], ids=lambda c: c.name)
    def test_entry_values_replace_wholesale(self, collection: Collection) -> None:
        """An entry's value is the message's whole current value, rather than a merge of the steps"""
        composed = compose_deltas(collection, [{MID: [1]}, {MID: [2, 3]}])

        assert composed == {MID: [2, 3]}

    def test_a_message_added_then_deleted_ends_deleted(self) -> None:
        """The later existence statement is the one reported"""
        history = [{ADDED: [MID], DELETED: []}, {ADDED: [], DELETED: [MID]}]

        assert compose_deltas(Collection.MAIL_LIST, history) == {ADDED: [], DELETED: [MID]}

    def test_a_message_deleted_then_added_ends_added(self) -> None:
        """A redelivered message is reported as present"""
        history = [{ADDED: [], DELETED: [MID]}, {ADDED: [MID], DELETED: []}]

        assert compose_deltas(Collection.MAIL_LIST, history) == {ADDED: [MID], DELETED: []}

    def test_lists_are_merged(self) -> None:
        """Ids from every step are merged into the final Delta"""
        history = [{ADDED: [MID]}, {ADDED: [OTHER_MID], DELETED: [b"\x33" * 32]}]

        composed = compose_deltas(Collection.MAIL_LIST, history)

        assert set(composed[ADDED]) == {MID, OTHER_MID}
        assert composed[DELETED] == [b"\x33" * 32]

    def test_added_and_deleted_never_overlap(self) -> None:
        """A Message id MUST NOT appear in both the ADDED and DELETED lists"""
        history = [
            {ADDED: [MID, OTHER_MID], DELETED: []},
            {ADDED: [], DELETED: [MID]},
            {ADDED: [MID], DELETED: [OTHER_MID]},
        ]

        composed = compose_deltas(Collection.MAIL_LIST, history)

        assert not set(composed[ADDED]) & set(composed[DELETED])
        assert set(composed[ADDED]) == {MID}
        assert set(composed[DELETED]) == {OTHER_MID}
