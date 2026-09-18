"""Tests for the client state helpers"""

import pytest

from rnmmp_client import apply_delta, initial_state
from rnmmp_core import Collection

MID = b"\x11" * 32
OTHER = b"\x22" * 32


class TestMailList:
    """MAIL_LIST Collection delta tests"""

    def test_a_full_sync_from_initial_is_the_added_list(self) -> None:
        """Applying a full-state Delta to the initial state yields the full state"""
        assert apply_delta(Collection.MAIL_LIST, initial_state(Collection.MAIL_LIST), {0: [MID], 1: []}) == {MID}

    def test_additions_and_deletions_apply_together(self) -> None:
        """
        ADDED ids join the set and DELETED ids leave it

        Strictly speaking, the server shouldn't ever send this,
        but it's best to handle it smoothly
        """
        assert apply_delta(Collection.MAIL_LIST, {MID}, {0: [OTHER], 1: [MID]}) == {OTHER}

    def test_the_input_state_is_not_mutated(self) -> None:
        """Updates are not done in place"""
        state = {MID}
        apply_delta(Collection.MAIL_LIST, state, {0: [OTHER], 1: [MID]})

        assert state == {MID}


class TestTagList:
    """TAG_LIST Collection delta tests"""

    def test_names_set_and_nil_deletes(self) -> None:
        """Test adding and removing tags"""
        state = apply_delta(Collection.TAG_LIST, {1: "Old", 2: "Doomed"}, {1: "New", 2: None, 3: "Made"})

        assert state == {1: "New", 3: "Made"}

    def test_an_empty_name_is_a_value_not_an_absence(self) -> None:
        """Empty string is a valid tag name"""
        assert apply_delta(Collection.TAG_LIST, {1: "Named"}, {1: ""}) == {1: ""}


class TestEntryCollections:
    """MESSAGE_TAG and METADATA Collection delta tests"""

    def test_message_tag_values_become_frozensets(self) -> None:
        """Tag lists are returned by the server as lists, we keep them as a frozenset for better semantics"""
        state = apply_delta(Collection.MESSAGE_TAG, {}, {MID: [1, -1]})

        assert state == {MID: frozenset({1, -1})}

    @pytest.mark.parametrize(
        ("collection", "emptied"),
        [
            pytest.param(Collection.MESSAGE_TAG, [], id="message-tag-emptied"),
            pytest.param(Collection.MESSAGE_TAG, None, id="message-tag-nil"),
            pytest.param(Collection.METADATA, {}, id="metadata-emptied"),
            pytest.param(Collection.METADATA, None, id="metadata-nil"),
        ],
    )
    def test_nil_and_emptied_both_remove_the_entry(self, collection: Collection, emptied: object) -> None:
        """An empty list is the same state as nil"""
        populated = {MID: frozenset({1})} if collection == Collection.MESSAGE_TAG else {MID: {"k": 1}}

        assert apply_delta(collection, populated, {MID: emptied}) == {}

    def test_metadata_maps_replace_wholesale(self) -> None:
        """A METADATA Delta value is the message's whole current value"""
        state = apply_delta(Collection.METADATA, {MID: {"old": 1}}, {MID: {"new": 2}})

        assert state == {MID: {"new": 2}}


class TestUnknownCollections:
    """A Collection with no known semantics raises instead of using a potentially incorrect merge"""

    @pytest.mark.parametrize("collection", [pytest.param(99, id="unassigned"), pytest.param(1000, id="extension")])
    def test_initial_state_refuses(self, collection: int) -> None:
        """No inital state can be created for an unknown Collection"""
        with pytest.raises(ValueError):
            initial_state(collection)  # type: ignore[arg-type]

    @pytest.mark.parametrize("collection", [pytest.param(99, id="unassigned"), pytest.param(1000, id="extension")])
    def test_apply_delta_refuses(self, collection: int) -> None:
        """No delta can be applied for an unknown Collection"""
        with pytest.raises(ValueError):
            apply_delta(collection, {}, {})  # type: ignore[arg-type]
