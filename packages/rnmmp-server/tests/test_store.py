"""Tests for the `Store` contract, exercised through `MemoryStore`, the reference backend"""

from collections.abc import Sequence

import pytest

from rnmmp_core import INITIAL_STATE_TOKEN, Collection, pack
from rnmmp_server import ChangeSet, LogEntry, MemoryStore, MessageIndex, StoredMessage

LXMF_HEAD = bytes(range(48)) * 2


def lxmf_raw(timestamp: object = 1757900000.5, title: object = b"the title", content: bytes = b"the content") -> bytes:
    """An example packed LXMF message"""
    return LXMF_HEAD + pack([timestamp, title, content, {}])


MID_A = b"\xaa" * 32
MID_B = b"\xbb" * 32


def _stored(message_id: bytes) -> StoredMessage:
    """A message record for tests that only care about ids"""
    return StoredMessage(message_id, b"raw:" + message_id[:2], lxmf=True)


def _populated() -> MemoryStore:
    """A store holding two tagged messages with metadata, applied the way the model would"""
    store = MemoryStore()
    store.apply(
        ChangeSet(
            messages_added=[_stored(MID_A), _stored(MID_B)],
            tags_created={1: "Work", 2: "Home"},
            tag_pairs_added=[(MID_A, 1), (MID_A, 2), (MID_B, 1)],
            metadata_set={MID_A: {0: 123, "note": "a"}},
        )
    )
    return store


class TestMessages:
    """Reading messages and their existence."""

    def test_get_messages_preserves_order_and_marks_missing(self) -> None:
        """Messages come back in the order requested, `None` for each id not present."""
        store = _populated()

        assert store.get_messages([MID_B, b"missing", MID_A]) == [_stored(MID_B), None, _stored(MID_A)]

    def test_existing_message_ids_is_the_present_subset(self) -> None:
        """Existence is answered without content: just the ids that are there."""
        assert _populated().get_existing_message_ids([MID_A, b"missing", MID_B]) == {MID_A, MID_B}

    def test_all_message_ids_lists_every_message(self) -> None:
        """The MAIL_LIST Collection, in insertion order."""
        assert _populated().get_all_message_ids() == [MID_A, MID_B]


class TestTagsAndMetadata:
    """Reading tags and metadata, batch-shaped."""

    def test_message_tags_answers_every_requested_id(self) -> None:
        """Every requested id is a key, the untagged and unknown with an empty set."""
        held = _populated().get_message_tags([MID_A, MID_B, b"missing"])

        assert held == {MID_A: {1, 2}, MID_B: {1}, b"missing": set()}

    def test_messages_with_tags_answers_every_requested_id(self) -> None:
        """Every requested tag id is a key, the unused and unknown with an empty set."""
        carriers = _populated().get_messages_with_tags([1, 2, 99])

        assert carriers == {1: {MID_A, MID_B}, 2: {MID_A}, 99: set()}

    def test_message_metadata_answers_every_requested_id(self) -> None:
        """Every requested id is a key, the bare and unknown with an empty map."""
        metadata = _populated().get_message_metadata([MID_A, MID_B, b"missing"])

        assert metadata == {MID_A: {0: 123, "note": "a"}, MID_B: {}, b"missing": {}}

    def test_reads_return_copies(self) -> None:
        """Mutating a read result doesn't mutate the store."""
        store = _populated()
        store.get_all_tags()[1] = "clobbered"
        store.get_message_metadata([MID_A])[MID_A]["note"] = "clobbered"

        assert store.get_all_tags()[1] == "Work"
        assert store.get_message_metadata([MID_A])[MID_A]["note"] == "a"


class TestApply:
    """The ChangeSet mutations, including the foreign-key-shaped cascades."""

    def test_deleting_a_message_cascades_its_rows(self) -> None:
        """Deleting a message removes its MESSAGE_TAG and METADATA rows with it."""
        store = _populated()
        store.apply(ChangeSet(message_ids_deleted=[MID_A]))

        assert store.get_existing_message_ids([MID_A]) == set()
        assert store.get_message_tags([MID_A]) == {MID_A: set()}
        assert store.get_message_metadata([MID_A]) == {MID_A: {}}
        assert store.get_message_tags([MID_B]) == {MID_B: {1}}

    def test_deleting_a_tag_cascades_its_pairs(self) -> None:
        """Deleting a tag removes its MESSAGE_TAG rows, leaving other tags in place."""
        store = _populated()
        store.apply(ChangeSet(tag_ids_deleted=[1]))

        assert 1 not in store.get_all_tags()
        assert store.get_message_tags([MID_A, MID_B]) == {MID_A: {2}, MID_B: set()}

    def test_metadata_set_merges_and_removal_deletes_keys(self) -> None:
        """Metadata entries merge into the existing map; removed keys disappear."""
        store = _populated()
        store.apply(ChangeSet(metadata_set={MID_A: {"note": "b", "extra": 1}}, metadata_keys_removed={MID_A: [0]}))

        assert store.get_message_metadata([MID_A]) == {MID_A: {"note": "b", "extra": 1}}

    def test_renames_replace_names_in_place(self) -> None:
        """A renamed tag keeps its id."""
        store = _populated()
        store.apply(ChangeSet(tags_renamed={1: "Renamed"}))

        assert store.get_all_tags() == {1: "Renamed", 2: "Home"}


class TestScanSearch:
    """The reference scan-based search"""

    def _populated_scan(self) -> MemoryStore:
        """Two LXMF messages, one tagged"""
        store = MemoryStore()
        store.apply(
            ChangeSet(
                messages_added=[
                    StoredMessage.from_raw(MID_A, lxmf_raw(title=b"Alpha Report", content=b"find me"), lxmf=True),
                    StoredMessage.from_raw(MID_B, lxmf_raw(title=b"beta report", content=b"skip me"), lxmf=True),
                ],
                tags_created={1: "Work"},
                tag_pairs_added=[(MID_A, 1)],
            )
        )
        return store

    def test_matching_is_a_casefolded_substring(self) -> None:
        """Case-insensitive substring over the decoded text."""
        store = self._populated_scan()

        assert set(store.search_title("REPORT", only_tags=frozenset(), exclude_tags=frozenset())) == {MID_A, MID_B}

    def test_undecodable_title_bytes_do_not_match_or_crash(self) -> None:
        """Bytes that are not UTF-8 are replaced, not fatal"""
        store = MemoryStore()
        store.apply(
            ChangeSet(messages_added=[StoredMessage.from_raw(MID_A, lxmf_raw(title=b"\xff\xfegro"), lxmf=True)])
        )

        assert list(store.search_title("gro", only_tags=frozenset(), exclude_tags=frozenset())) == [MID_A]
        assert list(store.search_title("\xff", only_tags=frozenset(), exclude_tags=frozenset())) == []

    def test_the_hints_skip_ruled_out_bodies(self) -> None:
        """Content search never loads a body the filters already rule out"""

        class Counting(MemoryStore):
            loads = 0

            def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
                Counting.loads += len(message_ids)
                return super().get_messages(message_ids)

        store = Counting()
        store.apply(
            ChangeSet(
                messages_added=[
                    StoredMessage.from_raw(MID_A, lxmf_raw(content=b"find me"), lxmf=True),
                    StoredMessage.from_raw(MID_B, lxmf_raw(content=b"find me too"), lxmf=True),
                ],
                tags_created={1: "Work"},
                tag_pairs_added=[(MID_A, 1)],
            )
        )
        matches = list(store.search_content("find", only_tags=frozenset({1}), exclude_tags=frozenset()))

        assert matches == [MID_A]
        assert Counting.loads == 1

    def test_content_search_is_lazy(self) -> None:
        """Consuming one match loads one batch of bodies, not the full mailbox"""
        from itertools import islice

        class Counting(MemoryStore):
            loads = 0

            def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
                Counting.loads += len(message_ids)
                return super().get_messages(message_ids)

        store = Counting()
        store.apply(
            ChangeSet(
                messages_added=[
                    StoredMessage.from_raw(bytes([index]) * 32, lxmf_raw(content=b"match"), lxmf=True)
                    for index in range(100)
                ]
            )
        )
        first = list(islice(store.search_content("match", only_tags=frozenset(), exclude_tags=frozenset()), 1))

        assert len(first) == 1
        assert Counting.loads <= 16


class TestTokensAndLog:
    """State Tokens and the change-log."""

    def test_a_fresh_collection_holds_the_initial_token(self) -> None:
        """A Collection that has never changed is in the Initial State."""
        assert MemoryStore().get_current_token(Collection.MAIL_LIST) == INITIAL_STATE_TOKEN

    def test_applied_tokens_become_current(self) -> None:
        """`apply` moves each named Collection to its new token."""
        store = MemoryStore()
        store.apply(ChangeSet(new_tokens={0: b"\x01"}))

        assert store.get_current_token(0) == b"\x01"
        assert store.get_current_token(1) == INITIAL_STATE_TOKEN

    def test_entries_since_the_current_token_are_empty(self) -> None:
        """A client at the current token has nothing to fetch."""
        store = MemoryStore()
        store.apply(ChangeSet(new_tokens={0: b"\x01"}, log_entries={0: LogEntry(INITIAL_STATE_TOKEN, b"fragment")}))

        assert store.get_entries_since(0, b"\x01") == []

    def test_entries_since_a_known_token_are_the_suffix(self) -> None:
        """Entries come back oldest first, starting at the write that left the given token."""
        store = MemoryStore()
        first = LogEntry(b"\x01", b"first fragment")
        second = LogEntry(b"\x02", b"second fragment")
        store.apply(ChangeSet(new_tokens={0: b"\x02"}, log_entries={0: first}))
        store.apply(ChangeSet(new_tokens={0: b"\x03"}, log_entries={0: second}))

        assert store.get_entries_since(0, b"\x01") == [first, second]
        assert store.get_entries_since(0, b"\x02") == [second]

    def test_an_unknown_token_is_none(self) -> None:
        """A token the store cannot answer for is `None`, distinct from the empty suffix."""
        assert MemoryStore().get_entries_since(0, b"who knows") is None

    def test_the_log_prunes_at_its_limit(self) -> None:
        """The oldest entries fall away, and their tokens become unknown."""
        store = MemoryStore(log_limit=2)
        for index in range(1, 5):
            store.apply(
                ChangeSet(new_tokens={0: bytes([index + 1])}, log_entries={0: LogEntry(bytes([index]), b"fragment")})
            )

        assert store.get_entries_since(0, bytes([1])) is None
        assert store.get_entries_since(0, bytes([3])) == [
            LogEntry(bytes([3]), b"fragment"),
            LogEntry(bytes([4]), b"fragment"),
        ]


class TestMessageIndex:
    """Write-time extraction of the index portions, and the read that serves them."""

    def test_an_lxmf_record_extracts_its_index_portions(self) -> None:
        """Head, Timestamp and Title are sliced once, when the record is built."""
        record = StoredMessage.from_raw(MID_A, lxmf_raw(), lxmf=True)

        assert record.index() == MessageIndex(MID_A, True, LXMF_HEAD, 1757900000.5, b"the title")

    def test_a_non_lxmf_record_has_no_portions(self) -> None:
        """Uploads are stored opaquely and are never parsed."""
        record = StoredMessage.from_raw(MID_A, lxmf_raw(), lxmf=False)

        assert record.index() == MessageIndex(MID_A, False, None, None, None)

    def test_head_needs_bytes_beyond_the_head_length(self) -> None:
        """A message no longer than the head cannot be LXMF, so no portion is extracted."""
        record = StoredMessage.from_raw(MID_A, LXMF_HEAD, lxmf=True)

        assert record.index() == MessageIndex(MID_A, True, None, None, None)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"\xc1 not msgpack", id="undecodable"),
            pytest.param(pack("not a list"), id="not-a-list"),
            pytest.param(pack([1.0, b"short"]), id="too-few-parts"),
        ],
    )
    def test_a_corrupt_payload_keeps_the_head_and_drops_the_rest(self, payload: bytes) -> None:
        """A message that does not decode as LXMF answers like one that is not LXMF."""
        record = StoredMessage.from_raw(MID_A, LXMF_HEAD + payload, lxmf=True)

        assert record.head == LXMF_HEAD
        assert record.timestamp is None and record.title is None

    def test_an_integer_timestamp_is_stored_as_a_float(self) -> None:
        """LXMF timestamps are epoch seconds however they were packed."""
        record = StoredMessage.from_raw(MID_A, lxmf_raw(timestamp=1757900000), lxmf=True)

        assert record.timestamp == 1757900000.0

    @pytest.mark.parametrize(
        ("timestamp", "title"),
        [
            pytest.param(True, b"t", id="bool-timestamp"),
            pytest.param(1.0, "not bytes", id="string-title"),
        ],
    )
    def test_wrongly_typed_portions_are_dropped(self, timestamp: object, title: object) -> None:
        """A portion of the wrong type is no portion at all."""
        record = StoredMessage.from_raw(MID_A, lxmf_raw(timestamp=timestamp, title=title), lxmf=True)

        assert (record.timestamp, record.title) != (timestamp, title)

    def test_message_index_preserves_order_and_marks_missing(self) -> None:
        """Index records come back in the order requested, `None` for each id not present."""
        store = MemoryStore()
        store.apply(ChangeSet(messages_added=[StoredMessage.from_raw(MID_A, lxmf_raw(), lxmf=True)]))

        first, missing = store.get_message_indexes([MID_A, b"missing"])

        assert first is not None and first.title == b"the title"
        assert missing is None
