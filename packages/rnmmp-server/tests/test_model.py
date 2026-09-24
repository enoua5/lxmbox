"""
Tests for `MailboxModel`, the mailbox-state rules
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import Any

import pytest

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
    ConflictingFiltersError,
    DuplicateTagNameError,
    InvalidMetadataKeyError,
    InvalidTagNameError,
    MailListDeltaKey,
    MetadataKey,
    ReservedMetadataKeyError,
    ResponseStatus,
    ServerDefinedTagError,
    ServerTag,
    StateMismatchDetail,
    StateMismatchError,
    UnknownCollectionError,
    UnknownMessageError,
    UnknownStateError,
    UnknownTagError,
    pack,
)
from rnmmp_server import MailboxModel, MemoryStore, MessageIndex, StoredMessage

MID = b"\x11" * 32
OTHER_MID = b"\x22" * 32
ADDED = int(MailListDeltaKey.ADDED)
DELETED = int(MailListDeltaKey.DELETED)


def fresh(**store_args: int) -> MailboxModel:
    """A model over an empty in-memory store."""
    return MailboxModel(MemoryStore(**store_args))


def with_message(model: MailboxModel | None = None, message_id: bytes = MID) -> MailboxModel:
    """A model holding one delivered LXMF message tagged UNREAD."""
    model = model or fresh()
    model.ingest(message_id, b"raw", lxmf=True, tags=[ServerTag.UNREAD])
    return model


def token_of(model: MailboxModel, collection: int) -> bytes:
    """The Collection's current token, learned the way a client would: from a sync."""
    return model.sync(collection, INITIAL_STATE_TOKEN)[1]


class TestSeeding:
    """The Server-Defined Tags every mailbox starts with."""

    def test_a_fresh_mailbox_serves_the_server_defined_tags(self) -> None:
        """A client syncing TAG_LIST from the Initial State receives all ten, under the spec's names."""
        delta, _ = fresh().sync(Collection.TAG_LIST, INITIAL_STATE_TOKEN)

        assert delta == {tag.value: tag.name for tag in ServerTag}

    def test_seeding_does_not_change_the_state_token(self) -> None:
        """The Initial State of TAG_LIST already includes the Server-Defined Tags."""
        assert token_of(fresh(), Collection.TAG_LIST) == INITIAL_STATE_TOKEN

    def test_seeding_is_idempotent(self) -> None:
        """Wrapping an already-seeded store changes nothing."""
        store = MemoryStore()
        MailboxModel(store)
        delta, token = MailboxModel(store).sync(Collection.TAG_LIST, INITIAL_STATE_TOKEN)

        assert len(delta) == len(list(ServerTag))
        assert token == INITIAL_STATE_TOKEN


class TestSync:
    """The SYNC request: deltas, tokens, and the two failure modes."""

    def test_an_unknown_collection_is_refused(self) -> None:
        """Server does not have a Collection with the requested id."""
        with pytest.raises(UnknownCollectionError) as excinfo:
            fresh().sync(99, INITIAL_STATE_TOKEN)

        assert excinfo.value.status is ResponseStatus.NO

    def test_sync_returns_the_token_the_delta_brings_the_client_to(self) -> None:
        """Syncing again from the returned State yields an empty delta."""
        model = with_message()
        _, token = model.sync(Collection.MAIL_LIST, INITIAL_STATE_TOKEN)

        assert model.sync(Collection.MAIL_LIST, token)[0] == {ADDED: [], DELETED: []}

    def test_a_caught_up_non_mail_collection_gets_an_empty_map(self) -> None:
        """The other Collections' deltas are plain maps, empty when nothing changed."""
        model = with_message()

        assert model.sync(Collection.MESSAGE_TAG, token_of(model, Collection.MESSAGE_TAG))[0] == {}

    def test_the_initial_state_always_means_the_full_state(self) -> None:
        """A client at the Initial State requires the full current state of the Collection."""
        model = with_message()

        assert model.sync(Collection.MAIL_LIST, INITIAL_STATE_TOKEN)[0] == {ADDED: [MID], DELETED: []}
        assert model.sync(Collection.MESSAGE_TAG, INITIAL_STATE_TOKEN)[0] == {MID: [int(ServerTag.UNREAD)]}

    def test_a_full_message_tag_delta_omits_untagged_messages(self) -> None:
        """A message with no tags has no MESSAGE_TAG entries to report."""
        model = with_message()
        model.remove_tags({MID: [ServerTag.UNREAD]})

        assert model.sync(Collection.MESSAGE_TAG, INITIAL_STATE_TOKEN)[0] == {}

    def test_a_token_from_another_collection_is_unknown(self) -> None:
        """Tokens are per-Collection; one Collection's token means nothing to another."""
        model = with_message()

        with pytest.raises(UnknownStateError):
            model.sync(Collection.TAG_LIST, token_of(model, Collection.MAIL_LIST))

    def test_a_pruned_token_is_unknown_and_initial_still_works(self) -> None:
        """A too-old cursor gets UNKNOWN_STATE; the client resyncs from the Initial State."""
        model = fresh(log_limit=2)
        model.create_tags(["anchor"])
        stale = token_of(model, Collection.TAG_LIST)
        for index in range(3):
            model.create_tags([f"tag {index}"])

        with pytest.raises(UnknownStateError) as excinfo:
            model.sync(Collection.TAG_LIST, stale)
        assert excinfo.value.status is ResponseStatus.NO
        assert len(model.sync(Collection.TAG_LIST, INITIAL_STATE_TOKEN)[0]) == len(list(ServerTag)) + 4


class TestDeltaRules:
    """The MUST rules of the four Delta shapes."""

    def test_a_message_added_and_deleted_appears_in_neither_list(self) -> None:
        """If a message was added and then deleted since the Last Known State, it should not appear."""
        model = fresh()
        token = token_of(model, Collection.MAIL_LIST)
        _, ids = model.upload([b"ephemeral"])
        model.delete(ids)

        assert model.sync(Collection.MAIL_LIST, token)[0] == {ADDED: [], DELETED: []}

    def test_no_id_appears_in_both_added_and_deleted(self) -> None:
        """A Message id MUST NOT appear in both the ADDED and DELETED lists."""
        model = with_message()
        token = token_of(model, Collection.MAIL_LIST)
        model.delete([MID])
        model.ingest(MID, b"raw", lxmf=True)

        delta = model.sync(Collection.MAIL_LIST, token)[0]

        assert not set(delta[ADDED]) & set(delta[DELETED])
        # It could be in added (unless the server kept that it was deleted and de-duped it),
        # but it must not be in deleted
        assert MID not in delta[DELETED]

    def test_intermediary_renames_are_not_represented(self) -> None:
        """If a tag is renamed multiple times, only the current name is shown."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["First"])
        token = token_of(model, Collection.TAG_LIST)
        model.rename_tags({tag_id: "Second"})
        model.rename_tags({tag_id: "Third"})

        assert model.sync(Collection.TAG_LIST, token)[0] == {tag_id: "Third"}

    def test_a_rename_that_comes_back_around_doesnt_sync_the_intermediary(self) -> None:
        """
        Tag IDs that have the same name as in the Last Known State SHOULD NOT appear.

        The server may report the no-op, but only ever with the current name
        """
        model = fresh()
        _, (tag_id,) = model.create_tags(["Stable"])
        token = token_of(model, Collection.TAG_LIST)
        model.rename_tags({tag_id: "Wandering"})
        model.rename_tags({tag_id: "Stable"})

        assert model.sync(Collection.TAG_LIST, token)[0] in ({}, {tag_id: "Stable"})

    def test_a_deleted_tag_reports_nil(self) -> None:
        """For tags that have been deleted since the Last Known State, the value is nil."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Doomed"])
        token = token_of(model, Collection.TAG_LIST)
        model.delete_tags([tag_id])

        assert model.sync(Collection.TAG_LIST, token)[0] == {tag_id: None}

    def test_a_recreated_tag_id_reads_as_a_rename(self) -> None:
        """If a tag is deleted and a new tag with the same id is created, the Delta shows a rename."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Old"])
        token = token_of(model, Collection.TAG_LIST)
        model.delete_tags([tag_id])
        _, (new_id,) = model.create_tags(["New"])

        assert new_id == tag_id
        assert model.sync(Collection.TAG_LIST, token)[0] == {tag_id: "New"}

    def test_a_tag_added_and_removed_from_a_message_is_not_reported(self) -> None:
        """
        If a tag is added and then removed, its addition MUST NOT be reported.

        The message's entry may survive as a no-op value holding the current set.
        """
        model = with_message()
        token = token_of(model, Collection.MESSAGE_TAG)
        model.add_tags({MID: [ServerTag.IMPORTANT]})
        model.remove_tags({MID: [ServerTag.IMPORTANT]})

        assert model.sync(Collection.MESSAGE_TAG, token)[0] in ({}, {MID: [ServerTag.UNREAD]})

    def test_a_deleted_message_reports_nil_in_message_tag(self) -> None:
        """For messages that have been deleted, the value is nil."""
        model = with_message()
        token = token_of(model, Collection.MESSAGE_TAG)
        model.delete([MID])

        assert model.sync(Collection.MESSAGE_TAG, token)[0] == {MID: None}

    def test_an_untagged_message_deletion_is_no_message_tag_change(self) -> None:
        """A message with no MESSAGE_TAG entries leaves none behind."""
        model = with_message()
        model.remove_tags({MID: [ServerTag.UNREAD]})
        token = token_of(model, Collection.MESSAGE_TAG)
        model.delete([MID])

        assert model.sync(Collection.MESSAGE_TAG, token)[0] == {}

    def test_metadata_deltas_report_the_current_map(self) -> None:
        """For messages that have had Metadata changed, the value is the current metadata map."""
        model = with_message()
        token = token_of(model, Collection.METADATA)
        model.set_metadata({MID: {"a": 1}})
        model.set_metadata({MID: {"b": 2}})

        assert model.sync(Collection.METADATA, token)[0] == {MID: {"a": 1, "b": 2}}

    def test_metadata_that_comes_back_around_is_omitted(self) -> None:
        """If a metadata field is added and then removed, it MUST NOT be included in the Delta."""
        model = with_message()
        token = token_of(model, Collection.METADATA)
        model.set_metadata({MID: {"a": 1}})
        model.remove_metadata({MID: ["a"]})

        assert model.sync(Collection.METADATA, token)[0] == {}


class TestIngest:
    """Server-side storage of delivered and sent mail."""

    def test_ingest_stores_the_message_with_tags_and_metadata(self) -> None:
        """The delivered message appears with its initial tags and trusted metadata."""
        model = fresh()
        updated = model.ingest(MID, b"raw", lxmf=True, tags=[ServerTag.UNREAD], metadata={0: 12345})

        assert set(updated) == {Collection.MAIL_LIST, Collection.MESSAGE_TAG, Collection.METADATA}
        assert model.tags_of([MID]) == [[int(ServerTag.UNREAD)]]
        assert model.metadata_of([MID]) == [{0: 12345}]

    def test_ingest_accepts_the_reserved_keys(self) -> None:
        """`RECEIVE_TIME` is server-managed, and ingest is the server's own path."""
        model = fresh()
        model.ingest(MID, b"raw", lxmf=True, metadata={int(MetadataKey.RECEIVE_TIME): 12345})

        assert model.metadata_of([MID]) == [{int(MetadataKey.RECEIVE_TIME): 12345}]

    def test_a_redelivered_message_changes_nothing(self) -> None:
        """Identical LXMF messages SHOULD be considered the same message."""
        model = with_message()
        token = token_of(model, Collection.MAIL_LIST)

        assert model.ingest(MID, b"other bytes", lxmf=True) == {}
        assert token_of(model, Collection.MAIL_LIST) == token

    def test_an_unknown_tag_is_refused(self) -> None:
        """Initial tags must exist in the TAG_LIST Collection."""
        with pytest.raises(UnknownTagError):
            fresh().ingest(MID, b"raw", lxmf=True, tags=[42])

    def test_a_bare_ingest_touches_only_the_mail_list(self) -> None:
        """A Collection that did not change MUST NOT appear in Updated States."""
        assert set(fresh().ingest(MID, b"raw", lxmf=True)) == {Collection.MAIL_LIST}


class TestIndexOf:
    """The index read: everything except content, without touching content."""

    LXMF_HEAD = bytes(range(48)) * 2

    def test_index_records_come_back_in_order_with_none_for_missing(self) -> None:
        """An ingested LXMF message indexes its portions; an upload and a missing id do not."""
        model = fresh()
        raw = self.LXMF_HEAD + pack([1757900000.5, b"the title", b"content", {}])
        model.ingest(MID, raw, lxmf=True)
        _, (upload_id,) = model.upload([b"opaque"])

        lxmf, upload, missing = model.index_of([MID, upload_id, b"missing"])

        assert lxmf == MessageIndex(MID, True, self.LXMF_HEAD, 1757900000.5, b"the title")
        assert upload == MessageIndex(upload_id, False, None, None, None)
        assert missing is None

    def test_index_reads_never_touch_content(self) -> None:
        """The index is served without loading message bodies."""

        class ContentGuard(MemoryStore):
            def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
                raise AssertionError("content was fetched on an index path")

        model = MailboxModel(ContentGuard())
        raw = self.LXMF_HEAD + pack([1.0, b"t", b"c", {}])
        model.ingest(MID, raw, lxmf=True)
        model.upload([b"opaque"])

        assert model.index_of([MID])[0] is not None


class TestSearch:
    """Tests for the model's handling of searches"""

    HEAD = bytes(range(48)) * 2
    GROCERY = b"\x01" * 32
    REPLY = b"\x02" * 32
    UNICODE = b"\x03" * 32

    def _mailbox(self, store: MemoryStore | None = None) -> MailboxModel:
        """Three LXMF messages with distinct titles, contents and tags, plus one upload."""
        model = MailboxModel(store or MemoryStore())
        rows = [
            (self.GROCERY, b"Grocery List", b"eggs and milk", [int(ServerTag.UNREAD)]),
            (self.REPLY, b"Re: grocery", b"got the EGGS", [int(ServerTag.UNREAD), int(ServerTag.IMPORTANT)]),
            (self.UNICODE, "\u00dcn\u00efcode Groc\u00e9ry".encode(), b"unrelated", []),
        ]
        for message_id, title, content, tags in rows:
            model.ingest(message_id, self.HEAD + pack([1.0, title, content, {}]), lxmf=True, tags=tags)
        model.upload([b"an opaque note about eggs"])
        return model

    def test_title_matching_is_case_insensitive(self) -> None:
        """A server SHOULD at minimum perform a case-insensitive substring match"""
        assert set(self._mailbox().search_title("GROCERY")) == {self.GROCERY, self.REPLY}

    def test_title_matching_casefolds_beyond_ascii(self) -> None:
        """Case-insensitivity covers the whole of the text"""
        assert set(self._mailbox().search_title("groc\u00e9ry")) == {self.UNICODE}

    def test_title_search_covers_lxmf_messages_only(self) -> None:
        """SEARCH_TITLE only applies to LXMF messages"""
        assert self._mailbox().search_title("eggs") == []

    def test_content_search_reaches_uploads(self) -> None:
        """A non-LXMF message is searched by its full content"""
        model = self._mailbox()
        matches = model.search_content("eggs")

        assert {self.GROCERY, self.REPLY} < set(matches) and len(matches) == 3

    def test_only_tags_require_every_tag(self) -> None:
        """The server MUST NOT return any Message ID that does not have every specified tag set"""
        matches = self._mailbox().search_title("grocery", only_tags=[int(ServerTag.UNREAD), int(ServerTag.IMPORTANT)])

        assert matches == [self.REPLY]

    def test_exclude_tags_reject_any_tag(self) -> None:
        """The server MUST NOT return any Message ID that has any specified tag set"""
        matches = self._mailbox().search_title("grocery", exclude_tags=[int(ServerTag.IMPORTANT)])

        assert matches == [self.GROCERY]

    @pytest.mark.parametrize("which", ["title", "content"])
    def test_conflicting_filters_are_refused(self, which: str) -> None:
        """The same tag in both ONLY_TAGS and EXCLUDE_TAGS is a conflict, checked before searching"""
        model = self._mailbox()
        search = model.search_title if which == "title" else model.search_content

        with pytest.raises(ConflictingFiltersError):
            search("x", only_tags=[int(ServerTag.UNREAD)], exclude_tags=[int(ServerTag.UNREAD)])

    def test_max_results_truncates_after_filtering(self) -> None:
        """If MAX_RESULTS is given, the server MUST NOT return more"""
        matches = self._mailbox().search_content("eggs", only_tags=[int(ServerTag.UNREAD)], max_results=1)

        assert len(matches) == 1 and matches[0] in {self.GROCERY, self.REPLY}

    def test_max_results_zero_returns_nothing(self) -> None:
        """Zero is a limit like any other"""
        assert self._mailbox().search_content("eggs", max_results=0) == []

    def test_the_filter_hints_reach_the_store(self) -> None:
        """The store receives the tag filters, so an indexed backend can narrow its search"""

        class Probe(MemoryStore):
            seen: tuple[set[int], set[int]] | None = None

            def search_title(
                self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
            ) -> list[bytes]:
                Probe.seen = (set(only_tags), set(exclude_tags))
                return []

        self._mailbox(Probe()).search_title("x", only_tags=[1], exclude_tags=[-1])

        assert Probe.seen == ({1}, {-1})

    def test_a_hint_ignoring_store_is_still_filtered(self) -> None:
        """The hints are optional for the store; the model re-applies the filters authoritatively"""

        class Sloppy(MemoryStore):
            def search_title(
                self, query: str, *, only_tags: AbstractSet[int], exclude_tags: AbstractSet[int]
            ) -> list[bytes]:
                return list(super().search_title(query, only_tags=frozenset(), exclude_tags=frozenset()))

        matches = self._mailbox(Sloppy()).search_title("grocery", exclude_tags=[int(ServerTag.IMPORTANT)])

        assert matches == [self.GROCERY]

    def test_a_limited_content_search_loads_few_bodies(self) -> None:
        """A small limit keeps body loads bounded by the scan batch"""

        class Counting(MemoryStore):
            loads = 0

            def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
                Counting.loads += len(message_ids)
                return super().get_messages(message_ids)

        model = MailboxModel(Counting())
        for index in range(200):
            raw = self.HEAD + pack([1.0, b"t", b"eggs %d" % index, {}])
            model.ingest(bytes([index]) * 32, raw, lxmf=True)
        Counting.loads = 0

        assert len(model.search_content("eggs", max_results=3)) == 3
        assert Counting.loads <= 32


class TestUpload:
    """Client-supplied messages, stored opaquely."""

    def test_uploads_take_fresh_uuid_ids_and_are_not_lxmf(self) -> None:
        """Uploads are stored opaquely and take UUID ids, never parsed as anything."""
        model = fresh()
        _, ids = model.upload([b"a", b"a"])
        records = model.get_messages(ids)

        assert len(ids) == 2 and ids[0] != ids[1]
        assert all(record is not None and len(record.message_id) == 16 and not record.lxmf for record in records)

    def test_uploaded_bytes_are_stored_verbatim(self) -> None:
        """The raw messages are stored byte-for-byte as uploaded."""
        model = fresh()
        _, ids = model.upload([b"\x00 opaque \xff"])
        record = model.get_messages(ids)[0]

        assert isinstance(record, StoredMessage) and record.raw == b"\x00 opaque \xff"

    def test_upload_applies_tags_and_client_metadata(self) -> None:
        """TAGS and METADATA apply to every uploaded message."""
        model = fresh()
        updated, ids = model.upload([b"a", b"b"], tags=[ServerTag.DRAFT], metadata={"kind": "note"})

        assert set(updated) == {Collection.MAIL_LIST, Collection.MESSAGE_TAG, Collection.METADATA}
        assert model.tags_of(ids) == [[int(ServerTag.DRAFT)], [int(ServerTag.DRAFT)]]
        assert model.metadata_of(ids) == [{"kind": "note"}, {"kind": "note"}]

    def test_a_reserved_client_key_is_refused(self) -> None:
        """A METADATA key the server manages itself is not accepted from a client."""
        with pytest.raises(ReservedMetadataKeyError):
            fresh().upload([b"a"], metadata={int(MetadataKey.RECEIVE_TIME): 1})

    @pytest.mark.parametrize("key", [pytest.param(True, id="bool"), pytest.param((1,), id="tuple")])
    def test_an_invalid_client_key_is_refused(self, key: Any) -> None:
        """Metadata Map keys are integers or strings."""
        with pytest.raises(InvalidMetadataKeyError):
            fresh().upload([b"a"], metadata={key: 1})

    def test_server_metadata_bypasses_the_client_checks(self) -> None:
        """The server MAY set Metadata the client did not specify — its own keys included."""
        model = fresh()
        _, ids = model.upload([b"a"], server_metadata={int(MetadataKey.RECEIVE_TIME): 99})

        assert model.metadata_of(ids) == [{int(MetadataKey.RECEIVE_TIME): 99}]

    def test_an_empty_upload_changes_nothing(self) -> None:
        """No messages, no state change, no ids."""
        assert fresh().upload([]) == ({}, [])


class TestDelete:
    """Permanent message deletion."""

    def test_deleting_removes_the_message_and_its_rows(self) -> None:
        """Deleting a message MUST also remove its MESSAGE_TAG and METADATA entries."""
        model = with_message()
        model.set_metadata({MID: {"a": 1}})
        updated = model.delete([MID])

        assert set(updated) == {Collection.MAIL_LIST, Collection.MESSAGE_TAG, Collection.METADATA}
        assert model.get_messages([MID]) == [None]
        assert model.tags_of([MID]) == [None]

    def test_deleting_an_absent_id_is_not_an_error(self) -> None:
        """Deleting an id that is not present causes no state change."""
        assert with_message().delete([b"never existed"]) == {}

    def test_deleting_an_untagged_bare_message_touches_only_the_mail_list(self) -> None:
        """Collections whose entries the message never had do not change."""
        model = fresh()
        model.ingest(MID, b"raw", lxmf=True)

        assert set(model.delete([MID])) == {Collection.MAIL_LIST}


class TestCreateTags:
    """CREATE_TAG: fresh positive ids, unique names."""

    def test_created_tags_take_fresh_positive_ids(self) -> None:
        """Each newly created tag gets a positive integer id not in use by another tag."""
        updated, ids = fresh().create_tags(["One", "Two"])

        assert ids == [1, 2]
        assert set(updated) == {Collection.TAG_LIST}

    def test_an_existing_name_returns_the_existing_id_without_change(self) -> None:
        """The server MUST NOT create a second tag; it MUST return the existing tag's id."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Work"])
        token = token_of(model, Collection.TAG_LIST)
        updated, ids = model.create_tags(["work"])

        assert ids == [tag_id]
        assert updated == {}
        assert token_of(model, Collection.TAG_LIST) == token

    def test_a_server_defined_name_returns_the_server_tag(self) -> None:
        """Name uniqueness covers Server-Defined Tags too."""
        _, ids = fresh().create_tags(["unread"])

        assert ids == [int(ServerTag.UNREAD)]

    def test_duplicate_names_in_one_batch_share_one_tag(self) -> None:
        """The same name twice creates one tag and returns its id twice."""
        _, ids = fresh().create_tags(["Twin", "twin"])

        assert ids == [1, 1]

    @pytest.mark.parametrize(
        "name", [pytest.param("", id="empty"), pytest.param("   ", id="blank"), pytest.param(7, id="not-a-string")]
    )
    def test_an_invalid_name_is_refused(self, name: Any) -> None:
        """The server considers the tag name invalid."""
        with pytest.raises(InvalidTagNameError):
            fresh().create_tags([name])


class TestDeleteTags:
    """DELETE_TAG: user tags only, with the MESSAGE_TAG cascade."""

    def test_deleting_a_tag_removes_it_from_its_messages(self) -> None:
        """Deleting a tag MUST also remove every MESSAGE_TAG entry that references it."""
        model = with_message()
        _, (tag_id,) = model.create_tags(["Doomed"])
        model.add_tags({MID: [tag_id]})
        updated = model.delete_tags([tag_id])

        assert set(updated) == {Collection.TAG_LIST, Collection.MESSAGE_TAG}
        assert model.tags_of([MID]) == [[int(ServerTag.UNREAD)]]

    def test_an_unused_tag_deletion_touches_only_the_tag_list(self) -> None:
        """With no MESSAGE_TAG entries to remove, that Collection does not change."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Unused"])

        assert set(model.delete_tags([tag_id])) == {Collection.TAG_LIST}

    def test_a_server_defined_tag_cannot_be_deleted(self) -> None:
        """Server-Defined Tags — those with negative IDs — cannot be deleted by the client."""
        with pytest.raises(ServerDefinedTagError) as excinfo:
            fresh().delete_tags([ServerTag.TRASH])

        assert excinfo.value.status is ResponseStatus.NO

    def test_deleting_an_absent_tag_is_not_an_error(self) -> None:
        """Deleting an id that is not present causes no state change."""
        assert fresh().delete_tags([42]) == {}


class TestRenameTags:
    """RENAME_TAG: names move, ids and MESSAGE_TAG entries stay."""

    def test_renaming_changes_the_name_and_nothing_else(self) -> None:
        """Renaming does not change a tag's id, so the MESSAGE_TAG Collection is unchanged."""
        model = with_message()
        _, (tag_id,) = model.create_tags(["Before"])
        model.add_tags({MID: [tag_id]})
        updated = model.rename_tags({tag_id: "After"})

        assert set(updated) == {Collection.TAG_LIST}
        assert model.tags()[tag_id] == "After"
        assert model.tags_of([MID]) == [sorted([int(ServerTag.UNREAD), tag_id])]

    def test_renaming_to_the_current_name_changes_nothing(self) -> None:
        """Renaming a tag to the name it currently has is not an error, and causes no state change."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Same"])

        assert model.rename_tags({tag_id: "Same"}) == {}

    def test_a_server_defined_tag_cannot_be_renamed(self) -> None:
        """The request tried to rename a Server-Defined Tag."""
        with pytest.raises(ServerDefinedTagError):
            fresh().rename_tags({int(ServerTag.UNREAD): "Seen"})

    def test_an_unknown_tag_cannot_be_renamed(self) -> None:
        """A supplied Tag ID does not exist in the TAG_LIST Collection."""
        with pytest.raises(UnknownTagError):
            fresh().rename_tags({42: "Ghost"})

    def test_a_name_already_in_use_is_refused(self) -> None:
        """The supplied name is already in use by another tag."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Mine"])

        with pytest.raises(DuplicateTagNameError):
            model.rename_tags({tag_id: "junk"})

    def test_an_invalid_name_is_refused(self) -> None:
        """The server does not support the provided tag name."""
        model = fresh()
        _, (tag_id,) = model.create_tags(["Fine"])

        with pytest.raises(InvalidTagNameError):
            model.rename_tags({tag_id: "  "})


class TestAddRemoveTags:
    """ADD_TAG and REMOVE_TAG: idempotent portions, strict ids, atomic requests."""

    def test_adding_and_removing_round_trips(self) -> None:
        """Tags added appear on the message; removed, they are gone."""
        model = with_message()
        model.add_tags({MID: [ServerTag.IMPORTANT]})
        assert model.tags_of([MID]) == [sorted([int(ServerTag.UNREAD), int(ServerTag.IMPORTANT)])]

        model.remove_tags({MID: [ServerTag.IMPORTANT]})
        assert model.tags_of([MID]) == [[int(ServerTag.UNREAD)]]

    def test_adding_a_held_tag_changes_nothing(self) -> None:
        """Adding a tag to a message that already has that tag is not an error, and causes no state change."""
        assert with_message().add_tags({MID: [ServerTag.UNREAD]}) == {}

    def test_removing_an_unheld_tag_changes_nothing(self) -> None:
        """Removing a tag a message does not have is not an error, and causes no state change."""
        assert with_message().remove_tags({MID: [ServerTag.IMPORTANT]}) == {}

    def test_a_removed_tag_stays_in_the_tag_list(self) -> None:
        """A tag remains in the TAG_LIST Collection even if not assigned to any message."""
        model = with_message()
        model.remove_tags({MID: [ServerTag.UNREAD]})

        assert int(ServerTag.UNREAD) in model.tags()

    def test_an_unknown_message_is_refused(self) -> None:
        """A supplied Message ID does not exist in the MAIL_LIST Collection."""
        with pytest.raises(UnknownMessageError):
            with_message().add_tags({b"ghost": [ServerTag.UNREAD]})

    def test_an_unknown_tag_is_refused(self) -> None:
        """A supplied Tag ID does not exist in the TAG_LIST Collection."""
        with pytest.raises(UnknownTagError):
            with_message().add_tags({MID: [42]})

    def test_a_partially_invalid_request_changes_nothing(self) -> None:
        """A write request MUST be applied atomically: either every change is applied, or none are."""
        model = with_message(with_message(fresh(), OTHER_MID))
        token = token_of(model, Collection.MESSAGE_TAG)

        with pytest.raises(UnknownTagError):
            model.add_tags({MID: [ServerTag.IMPORTANT], OTHER_MID: [42]})
        assert token_of(model, Collection.MESSAGE_TAG) == token
        assert model.tags_of([MID]) == [[int(ServerTag.UNREAD)]]


class TestMetadataWrites:
    """SET_METADATA and REMOVE_METADATA."""

    def test_entries_merge_into_the_existing_map(self) -> None:
        """A key present in the request sets or replaces the current value; others are left unchanged."""
        model = with_message()
        model.set_metadata({MID: {"a": 1, "b": 2}})
        model.set_metadata({MID: {"b": 3}})

        assert model.metadata_of([MID]) == [{"a": 1, "b": 3}]

    def test_setting_the_current_value_changes_nothing(self) -> None:
        """Setting a key to the value it already holds causes no state change."""
        model = with_message()
        model.set_metadata({MID: {"a": 1}})

        assert model.set_metadata({MID: {"a": 1}}) == {}

    def test_removing_keys_deletes_them(self) -> None:
        """Removed keys disappear from the Metadata Map."""
        model = with_message()
        model.set_metadata({MID: {"a": 1, "b": 2}})
        model.remove_metadata({MID: ["a"]})

        assert model.metadata_of([MID]) == [{"b": 2}]

    def test_removing_an_absent_key_changes_nothing(self) -> None:
        """Removing a key not present in the Metadata Map is not an error, and causes no state change."""
        assert with_message().remove_metadata({MID: ["never set"]}) == {}

    @pytest.mark.parametrize("method", ["set", "remove"])
    def test_an_unknown_message_is_refused(self, method: str) -> None:
        """A supplied Message ID does not exist in the MAIL_LIST Collection."""
        model = fresh()

        with pytest.raises(UnknownMessageError):
            if method == "set":
                model.set_metadata({b"ghost": {"a": 1}})
            else:
                model.remove_metadata({b"ghost": ["a"]})

    @pytest.mark.parametrize("method", ["set", "remove"])
    def test_a_reserved_key_is_refused(self, method: str) -> None:
        """A supplied key is one the server manages itself."""
        model = with_message()

        with pytest.raises(ReservedMetadataKeyError):
            if method == "set":
                model.set_metadata({MID: {int(MetadataKey.RECEIVE_TIME): 1}})
            else:
                model.remove_metadata({MID: [int(MetadataKey.RECEIVE_TIME)]})


class TestIfInState:
    """Optimistic concurrency, shared by every write."""

    def test_a_matching_state_lets_the_write_through(self) -> None:
        """IF_IN_STATE with the current token behaves as if absent."""
        model = with_message()
        current = token_of(model, Collection.MAIL_LIST)

        assert model.delete([MID], if_in_state={int(Collection.MAIL_LIST): current}) != {}

    def test_a_stale_state_rejects_the_write_and_reports_current_tokens(self) -> None:
        """A mismatch MUST reject the request, apply no changes, and report the updated Collections."""
        model = with_message()
        current = token_of(model, Collection.MAIL_LIST)

        with pytest.raises(StateMismatchError) as excinfo:
            model.delete([MID], if_in_state={int(Collection.MAIL_LIST): b"stale"})

        assert excinfo.value.status is ResponseStatus.NO
        details = excinfo.value.details
        assert details == {int(StateMismatchDetail.UPDATED_STATES): {int(Collection.MAIL_LIST): current}}
        assert model.get_messages([MID]) != [None]

    def test_only_mismatched_collections_are_reported(self) -> None:
        """The details map holds the Collections whose tokens differed."""
        model = with_message()
        mail = token_of(model, Collection.MAIL_LIST)

        with pytest.raises(StateMismatchError) as excinfo:
            model.delete(
                [MID],
                if_in_state={int(Collection.MAIL_LIST): mail, int(Collection.MESSAGE_TAG): b"stale"},
            )

        assert excinfo.value.details is not None
        assert list(excinfo.value.details[int(StateMismatchDetail.UPDATED_STATES)]) == [int(Collection.MESSAGE_TAG)]

    def test_updated_states_pairs_chain_between_writes(self) -> None:
        """Each write returns (previous, new); the previous of the next write is the new of the last."""
        model = fresh()
        first = model.ingest(MID, b"raw", lxmf=True)[Collection.MAIL_LIST]
        second = model.delete([MID])[Collection.MAIL_LIST]

        assert first.previous == INITIAL_STATE_TOKEN
        assert second.previous == first.new
        assert second.new != second.previous


class TestContentIsolation:
    """Existence questions don't need to load message content."""

    def test_no_model_path_but_get_messages_touches_content(self) -> None:
        """Reads, writes and syncs run against a store whose content fetch refuses to answer."""

        class ContentGuard(MemoryStore):
            def get_messages(self, message_ids: Sequence[bytes]) -> list[StoredMessage | None]:
                raise AssertionError("content was fetched on an existence-only path")

        model = MailboxModel(ContentGuard())
        model.ingest(MID, b"raw", lxmf=True, tags=[ServerTag.UNREAD])
        token = token_of(model, Collection.MAIL_LIST)
        model.set_metadata({MID: {"a": 1}})
        model.tags_of([MID, b"ghost"])
        model.metadata_of([MID])
        model.delete([MID])
        model.sync(Collection.MAIL_LIST, token)
        model.sync(Collection.MESSAGE_TAG, INITIAL_STATE_TOKEN)
