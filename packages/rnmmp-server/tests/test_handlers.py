"""
Tests for the request handlers: a decoded `Request` in, the `Response` that answers it out.

Handlers are pure model glue, so everything here runs against `MailboxModel` over `MemoryStore`
with no RNS. Responses are asserted through their decoded form — status, parameters, and the
error recovered by `Response.error(request_type)` — the same way a client will read them.
"""

import datetime
from typing import Any

import pytest

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    PROTOCOL_VERSION,
    Collection,
    ConflictingFiltersError,
    ErrorInfoKey,
    GeneralError,
    MetadataKey,
    Request,
    RequestType,
    Response,
    ResponseStatus,
    SearchContentParam,
    SearchTitleError,
    SearchTitleParam,
    ServerTag,
    StateMismatchDetail,
    StateMismatchError,
    SyncError,
    UnknownCollectionError,
    UnknownStateError,
    UnsupportedError,
    UploadError,
    UploadParam,
    WriteParam,
    pack,
)
from rnmmp_server import MailboxModel, MemoryStore, handle

MID = b"\x11" * 32
LXMF_HEAD = bytes(range(48)) * 2
LXMF_RAW = LXMF_HEAD + pack([1757900000.5, b"the title", b"the content", {7: b"field"}])


def mailbox() -> MailboxModel:
    """A mailbox holding one delivered LXMF message tagged UNREAD."""
    model = MailboxModel(MemoryStore())
    model.ingest(MID, LXMF_RAW, lxmf=True, tags=[ServerTag.UNREAD])
    return model


def ask(model: MailboxModel, request_type: int, *positional: Any, keyed: dict[Any, Any] | None = None) -> Response:
    """Answer one request, checking the Request id is echoed."""
    response = handle(model, Request(7, request_type, keyed or {}, list(positional)))
    assert response.request_id == 7
    return response


class TestDispatch:
    """The `handle` entry itself."""

    def test_noop_performs_nothing_and_succeeds(self) -> None:
        """NOOP: no action to be performed."""
        response = ask(mailbox(), RequestType.NOOP)

        assert response.is_ok and response.parameters == []

    def test_capability_leads_with_the_protocol_version(self) -> None:
        """The first element of the Capability List MUST be a protocol version; extensions may follow."""
        (capabilities,) = ask(mailbox(), RequestType.CAPABILITY).parameters

        assert isinstance(capabilities, list)
        assert capabilities[0] == PROTOCOL_VERSION

    @pytest.mark.parametrize(
        "request_type",
        [pytest.param(99, id="unassigned-official"), pytest.param(1000, id="extension-range")],
    )
    def test_an_unknown_request_type_answers_unsupported(self, request_type: int) -> None:
        """A request type the server does not serve is refused as UNSUPPORTED, not dropped."""
        error = ask(mailbox(), request_type).error(request_type)

        assert type(error) is UnsupportedError

    def test_a_missing_required_parameter_answers_bad(self) -> None:
        """Implementations MUST reject Exchanges with missing required parameters."""
        response = ask(mailbox(), RequestType.SYNC, int(Collection.MAIL_LIST))

        assert response.status is ResponseStatus.BAD
        assert response.parameters[0][ErrorInfoKey.GENERAL_ERROR] == GeneralError.INCOMPLETE

    def test_specific_error_codes_reach_the_packet(self) -> None:
        """A handler's raised error is packaged with the code its request type assigns."""
        response = ask(mailbox(), RequestType.SYNC, 99, INITIAL_STATE_TOKEN)

        assert type(response.error(RequestType.SYNC)) is UnknownCollectionError
        assert response.parameters[0][ErrorInfoKey.SPECIFIC_ERROR] == SyncError.UNKNOWN_COLLECTION


class TestSync:
    """The SYNC handler."""

    def test_sync_returns_the_delta_and_its_state(self) -> None:
        """SYNC answers with the Delta and the State Token it brings the client to."""
        delta, state = ask(mailbox(), RequestType.SYNC, int(Collection.MAIL_LIST), INITIAL_STATE_TOKEN).parameters

        assert delta == {0: [MID], 1: []}
        assert isinstance(state, bytes) and state != INITIAL_STATE_TOKEN

    def test_an_unknown_state_is_the_resync_signal(self) -> None:
        """A too-old cursor gets UNKNOWN_STATE and the client retries from the Initial State."""
        error = ask(mailbox(), RequestType.SYNC, int(Collection.MAIL_LIST), b"who?").error(RequestType.SYNC)

        assert type(error) is UnknownStateError


class TestFetch:
    """The per-portion fetches, plus tags and metadata"""

    @pytest.mark.parametrize(
        ("request_type", "expected"),
        [
            pytest.param(RequestType.FETCH_FULL, LXMF_RAW, id="full"),
            pytest.param(RequestType.FETCH_HEAD, LXMF_HEAD, id="head"),
            pytest.param(RequestType.FETCH_PAYLOAD, LXMF_RAW[len(LXMF_HEAD) :], id="payload"),
            pytest.param(RequestType.FETCH_CONTENT, b"the content", id="content"),
            pytest.param(RequestType.FETCH_FIELDS, {7: b"field"}, id="fields"),
            pytest.param(
                RequestType.FETCH_TIMESTAMP,
                datetime.datetime.fromtimestamp(1757900000.5, tz=datetime.UTC),
                id="timestamp",
            ),
            pytest.param(RequestType.FETCH_TITLE, b"the title", id="title"),
        ],
    )
    def test_each_portion_of_an_lxmf_message(self, request_type: int, expected: Any) -> None:
        """Each FETCH returns its portion, with `nil` for an id that is not present."""
        response = ask(mailbox(), request_type, [MID, b"missing"])

        assert response.parameters == [[expected, None]]

    @pytest.mark.parametrize(
        ("request_type", "expected"),
        [
            pytest.param(RequestType.FETCH_FULL, b"opaque", id="full-is-raw"),
            pytest.param(RequestType.FETCH_CONTENT, b"opaque", id="content-is-raw"),
            pytest.param(RequestType.FETCH_HEAD, None, id="head-is-nil"),
            pytest.param(RequestType.FETCH_PAYLOAD, None, id="payload-is-nil"),
            pytest.param(RequestType.FETCH_FIELDS, None, id="fields-is-nil"),
            pytest.param(RequestType.FETCH_TIMESTAMP, None, id="timestamp-is-nil"),
            pytest.param(RequestType.FETCH_TITLE, None, id="title-is-nil"),
        ],
    )
    def test_each_portion_of_an_upload(self, request_type: int, expected: Any) -> None:
        """A non-LXMF message returns its full content where content is asked, `nil` elsewhere."""
        model = mailbox()
        _, (upload_id,) = model.upload([b"opaque"])

        assert ask(model, request_type, [upload_id]).parameters == [[expected]]

    def test_fetch_tags_and_metadata_shapes(self) -> None:
        """Tags and Metadata come back per message, `nil` for a missing one."""
        model = mailbox()

        assert ask(model, RequestType.FETCH_TAGS, [MID, b"missing"]).parameters == [[[-1], None]]
        [[metadata, missing]] = ask(model, RequestType.FETCH_METADATA, [MID, b"missing"]).parameters
        assert missing is None
        assert isinstance(metadata[int(MetadataKey.RECEIVE_TIME)], datetime.datetime)

    def test_a_non_bytes_message_id_is_a_wrong_type(self) -> None:
        """Every item of Message IDs must be Bytes."""
        response = ask(mailbox(), RequestType.FETCH_FULL, [MID, "not bytes"])

        assert response.status is ResponseStatus.BAD
        assert response.parameters[0][ErrorInfoKey.GENERAL_ERROR] == GeneralError.WRONG_TYPE


class TestSearch:
    """The search request handlers"""

    def _mailbox(self) -> MailboxModel:
        """The shared fixture message, plus a second tagged IMPORTANT, and any extras"""
        model = mailbox()
        raw = LXMF_HEAD + pack([2.0, b"the other title", b"other content", {}])
        model.ingest(b"\x22" * 32, raw, lxmf=True, tags=[ServerTag.UNREAD, ServerTag.IMPORTANT])
        return model

    def test_search_title_returns_matching_ids(self) -> None:
        """SEARCH_TITLE answers with the ids of LXMF messages whose Title matches the Query"""
        response = ask(self._mailbox(), RequestType.SEARCH_TITLE, "THE TITLE")

        assert response.parameters == [[MID]]

    def test_the_keyed_filters_and_limit_apply(self) -> None:
        """ONLY_TAGS, EXCLUDE_TAGS and MAX_RESULTS apply"""
        model = self._mailbox()
        keyed = {
            int(SearchTitleParam.ONLY_TAGS): [int(ServerTag.UNREAD)],
            int(SearchTitleParam.EXCLUDE_TAGS): [int(ServerTag.IMPORTANT)],
            int(SearchTitleParam.MAX_RESULTS): 5,
        }

        assert ask(model, RequestType.SEARCH_TITLE, "title", keyed=keyed).parameters == [[MID]]

    @pytest.mark.parametrize(
        ("request_type", "conflict_key"),
        [
            pytest.param(RequestType.SEARCH_TITLE, int(SearchTitleParam.ONLY_TAGS), id="title"),
            pytest.param(RequestType.SEARCH_CONTENT, int(SearchContentParam.ONLY_TAGS), id="content"),
        ],
    )
    def test_conflicting_filters_raises_an_error(self, request_type: int, conflict_key: int) -> None:
        """The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS"""
        keyed = {conflict_key: [int(ServerTag.UNREAD)], conflict_key + 1: [int(ServerTag.UNREAD)]}
        response = ask(self._mailbox(), request_type, "x", keyed=keyed)

        assert type(response.error(request_type)) is ConflictingFiltersError
        assert response.parameters[0][ErrorInfoKey.SPECIFIC_ERROR] == SearchTitleError.CONFLICTING_FILTERS

    @pytest.mark.parametrize(
        ("positional", "keyed"),
        [
            pytest.param([], {}, id="missing-query"),
            pytest.param([7], {}, id="non-string-query"),
            pytest.param(["x"], {int(SearchTitleParam.MAX_RESULTS): -1}, id="negative-limit"),
            pytest.param(["x"], {int(SearchTitleParam.MAX_RESULTS): True}, id="bool-limit"),
            pytest.param(["x"], {int(SearchTitleParam.ONLY_TAGS): ["tag"]}, id="string-tag"),
        ],
    )
    def test_bad_parameters_are_bad_requests(self, positional: list[Any], keyed: dict[Any, Any]) -> None:
        """Missing or mistyped SEARCH parameters answer BAD"""
        response = ask(self._mailbox(), RequestType.SEARCH_TITLE, *positional, keyed=keyed)

        assert response.status is ResponseStatus.BAD


class TestWrites:
    """The write handlers; protocol shapes, the shared IF_IN_STATE, and the server's own records"""

    def test_upload_stores_tags_records_receipt_and_returns_ids(self) -> None:
        """UPLOAD returns Updated States and the id for each message; the server records RECEIVE_TIME."""
        model = mailbox()
        response = ask(model, RequestType.UPLOAD, [b"a"], keyed={int(UploadParam.TAGS): [int(ServerTag.DRAFT)]})

        updated, (upload_id,) = response.parameters
        assert set(updated) == {Collection.MAIL_LIST, Collection.MESSAGE_TAG, Collection.METADATA}
        assert all(isinstance(pair, list) and len(pair) == 2 for pair in updated.values())
        assert model.tags_of([upload_id]) == [[int(ServerTag.DRAFT)]]
        recorded = model.metadata_of([upload_id])[0]
        assert recorded is not None and isinstance(recorded[int(MetadataKey.RECEIVE_TIME)], datetime.datetime)

    def test_a_reserved_client_metadata_key_is_refused(self) -> None:
        """A METADATA key the server manages itself is not accepted from a client."""
        response = ask(
            mailbox(), RequestType.UPLOAD, [b"a"], keyed={int(UploadParam.METADATA): {int(MetadataKey.RECEIVE_TIME): 1}}
        )

        assert response.status is ResponseStatus.NO
        assert response.parameters[0][ErrorInfoKey.SPECIFIC_ERROR] == UploadError.RESERVED_KEY

    def test_delete_returns_only_the_changed_collections(self) -> None:
        """A Collection that did not change MUST NOT appear in Updated States."""
        model = MailboxModel(
            MemoryStore(), get_initial_tags=lambda message: (), get_initial_metadata=lambda message: {}
        )
        model.ingest(MID, LXMF_RAW, lxmf=True)

        (updated,) = ask(model, RequestType.DELETE, [MID]).parameters

        assert list(updated) == [int(Collection.MAIL_LIST)]

    def test_create_tags_returns_states_and_ids(self) -> None:
        """CREATE_TAG returns a positive id per new name, and the existing id for a name in use."""
        updated, tag_ids = ask(mailbox(), RequestType.CREATE_TAG, ["One", "unread"]).parameters

        assert tag_ids[0] > 0
        assert tag_ids[1] == int(ServerTag.UNREAD)
        assert list(updated) == [int(Collection.TAG_LIST)]

    def test_the_tag_and_metadata_writes_round_trip(self) -> None:
        """ADD_TAG, RENAME_TAG, SET_METADATA and their inverses apply through the handlers."""
        model = mailbox()
        _, (tag_id,) = model.create_tags(["Work"])

        assert ask(model, RequestType.ADD_TAG, {MID: [tag_id]}).is_ok
        assert ask(model, RequestType.RENAME_TAG, {tag_id: "Renamed"}).is_ok
        assert ask(model, RequestType.SET_METADATA, {MID: {"k": 1}}).is_ok
        assert model.tags_of([MID]) == [sorted([int(ServerTag.UNREAD), tag_id])]
        assert model.tags()[tag_id] == "Renamed"

        assert ask(model, RequestType.REMOVE_TAG, {MID: [tag_id]}).is_ok
        assert ask(model, RequestType.REMOVE_METADATA, {MID: ["k"]}).is_ok
        assert ask(model, RequestType.DELETE_TAG, [tag_id]).is_ok
        assert model.tags_of([MID]) == [[int(ServerTag.UNREAD)]]

    def test_a_stale_if_in_state_reports_the_mismatch(self) -> None:
        """A write against a stale token is rejected with the updated Collections in the details."""
        response = ask(
            mailbox(), RequestType.DELETE, [MID], keyed={int(WriteParam.IF_IN_STATE): {int(Collection.MAIL_LIST): b"x"}}
        )

        error = response.error(RequestType.DELETE)
        assert type(error) is StateMismatchError
        assert error is not None and list(error.details or {}) == [int(StateMismatchDetail.UPDATED_STATES)]

    @pytest.mark.parametrize(
        ("request_type", "positional", "keyed"),
        [
            pytest.param(RequestType.UPLOAD, [["not bytes"]], None, id="upload-raws"),
            pytest.param(RequestType.DELETE_TAG, [[True]], None, id="bool-tag-id"),
            pytest.param(RequestType.RENAME_TAG, [{"1": "x"}], None, id="rename-keyed-by-string"),
            pytest.param(RequestType.ADD_TAG, [{1: [1]}], None, id="additions-keyed-by-int"),
            pytest.param(RequestType.SET_METADATA, [{MID: "not a map"}], None, id="entries-not-maps"),
            pytest.param(
                RequestType.DELETE, [[MID]], {int(WriteParam.IF_IN_STATE): {"0": b"x"}}, id="if-in-state-keys"
            ),
        ],
    )
    def test_wrongly_typed_elements_are_bad_requests(
        self, request_type: int, positional: list[Any], keyed: dict[Any, Any] | None
    ) -> None:
        """Element types the accessors cannot check are still WRONG_TYPE, not server faults."""
        response = ask(mailbox(), request_type, *positional, keyed=keyed)

        assert response.status is ResponseStatus.BAD
        assert response.parameters[0][ErrorInfoKey.GENERAL_ERROR] == GeneralError.WRONG_TYPE
