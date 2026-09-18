"""Tests for `MailboxLink`"""

import datetime
import threading
import time
from collections.abc import Callable

import pytest

from rnmmp_client import (
    Capabilities,
    CollectionDelta,
    CreatedTags,
    MailboxLink,
    NoAnswer,
    TokenChange,
    UploadResult,
    VoidAnswer,
)
from rnmmp_core import (
    Collection,
    ConflictingFiltersError,
    Exchange,
    MalformedExchangeError,
    Request,
    RequestType,
    Response,
    SearchTitleParam,
    ServerTag,
    StateMismatchError,
    UnknownStateError,
    UploadParam,
    WriteParam,
)

MID = b"\x11" * 32
Responder = Callable[[Request], Response | None]


class FakeTransport:
    """Mock test-controlled transport for testing"""

    def __init__(self, respond: Responder | None = None) -> None:
        """
        Args:
            respond: method to be called in response to a Request
        """
        self.sent: list[Request] = []
        self.respond = respond
        self._open = True
        self._on_exchange: Callable[[Exchange], None] | None = None
        self._on_closed: Callable[[], None] | None = None

    def send(self, exchange: Exchange) -> None:
        """Record the Request and let `respond` answer it"""
        assert isinstance(exchange, Request)
        self.sent.append(exchange)
        if self.respond is not None and self._on_exchange is not None:
            response = self.respond(exchange)
            if response is not None:
                self._on_exchange(response)

    def attach(self, on_exchange: Callable[[Exchange], None], on_closed: Callable[[], None]) -> None:
        """Capture the delivery hooks"""
        self._on_exchange = on_exchange
        self._on_closed = on_closed

    def close(self) -> None:
        """Close the transport"""
        self._open = False
        if self._on_closed is not None:
            self._on_closed()

    @property
    def is_open(self) -> bool:
        """Whether the transport is open"""
        return self._open


def create_mailbox(respond: Responder | None = None) -> tuple[MailboxLink, FakeTransport]:
    """A MailboxLink over a scripted mock, with a short timeout for no-response tests"""
    transport = FakeTransport(respond)
    return MailboxLink(transport, default_timeout=0.2), transport


def ok(*returns: object) -> Responder:
    """Basic response stub useful as a default"""
    return lambda request: Response.ok(request.request_id, *returns)


class TestMailboxLink:
    """The exchange plumbing itself"""

    def test_request_ids_change(self) -> None:
        """Each Request SHOULD use a fresh Request id"""
        link, transport = create_mailbox(ok())
        link.noop()
        link.noop()

        first, second = transport.sent
        assert second.request_id != first.request_id

    def test_an_unsolicited_response_is_ignored(self) -> None:
        """A Response to no outstanding Request is ignored"""

        def respond(request: Request) -> Response:
            return Response.ok(request.request_id + 900)

        link, _ = create_mailbox(respond)

        assert link.noop() == NoAnswer()

    def test_silence_is_no_answer(self) -> None:
        """A transport that never answers yields `NoAnswer` after the timeout"""
        link, _ = create_mailbox(respond=None)

        assert link.noop() == NoAnswer()

    def test_a_closed_transport_answers_immediately(self) -> None:
        """No waiting out a long timeout on a transport known to be dead"""
        link, transport = create_mailbox(respond=None)
        transport.close()
        started = time.monotonic()

        assert link.noop(timeout=30.0) == NoAnswer()
        assert time.monotonic() - started < 1.0

    def test_closing_wakes_a_waiting_call(self) -> None:
        """A call waiting on a response returns promptly when the transport closes"""
        link, transport = create_mailbox(respond=None)
        results: list[VoidAnswer | NoAnswer] = []
        caller = threading.Thread(target=lambda: results.append(link.noop(timeout=30.0)))
        caller.start()
        time.sleep(0.1)
        transport.close()
        caller.join(timeout=2.0)

        assert not caller.is_alive()
        assert results == [NoAnswer()]

    def test_protocol_errors_decode_typed(self) -> None:
        """A NO Response raises the exception class for its code"""

        def respond(request: Request) -> Response:
            return Response.failure(request.request_id, UnknownStateError(), RequestType.SYNC)

        link, _ = create_mailbox(respond)

        with pytest.raises(UnknownStateError):
            link.sync_collection(int(Collection.MAIL_LIST), b"stale")


class TestRequestHandling:
    """How each method packs its request"""

    def test_sync_collection_sends_collection_and_token(self) -> None:
        """SYNC sends the Collection id and the last known State Token"""
        link, transport = create_mailbox(ok({}, b"\x01"))
        link.sync_collection(int(Collection.TAG_LIST), b"\x00")

        request = transport.sent[-1]
        assert request.request_type == RequestType.SYNC
        assert request.positional_parameters == [int(Collection.TAG_LIST), b"\x00"]

    def test_fetches_send_the_id_list(self) -> None:
        """A fetch sends its Message IDs as one positional list"""
        link, transport = create_mailbox(ok([None]))
        link.fetch_title([MID])

        request = transport.sent[-1]
        assert request.request_type == RequestType.FETCH_TITLE
        assert request.positional_parameters == [[MID]]

    def test_search_sends_its_keyed_filters(self) -> None:
        """MAX_RESULTS, ONLY_TAGS and EXCLUDE_TAGS use the Keyed Parameters"""
        link, transport = create_mailbox(ok([]))
        link.search_title("query", only_tags=[1], exclude_tags=[int(ServerTag.TRASH)], max_results=5)

        request = transport.sent[-1]
        assert request.positional_parameters == ["query"]
        assert request.keyed_parameters == {
            int(SearchTitleParam.MAX_RESULTS): 5,
            int(SearchTitleParam.ONLY_TAGS): [1],
            int(SearchTitleParam.EXCLUDE_TAGS): [int(ServerTag.TRASH)],
        }

    def test_conflicting_filters_never_reach_the_server(self) -> None:
        """An overlap between ONLY_TAGS and EXCLUDE_TAGS is refused before anything is sent"""
        link, transport = create_mailbox(ok([]))

        with pytest.raises(ConflictingFiltersError):
            link.search_title("query", only_tags=[1], exclude_tags=[1])
        assert transport.sent == []

    def test_writes_send_if_in_state(self) -> None:
        """The shared optimistic-concurrency map is sent in writes' Keyed Parameters"""
        link, transport = create_mailbox(ok({}))
        link.delete([MID], if_in_state={0: b"\x01"})

        assert transport.sent[-1].keyed_parameters == {int(WriteParam.IF_IN_STATE): {0: b"\x01"}}

    def test_upload_sends_tags_and_metadata(self) -> None:
        """UPLOAD's optional tags and metadata are sent in its Keyed Parameters"""
        link, transport = create_mailbox(ok({}, [b"\x01"]))
        link.upload([b"raw"], tags=[int(ServerTag.DRAFT)], metadata={"kind": "note"})

        request = transport.sent[-1]
        assert request.positional_parameters == [[b"raw"]]
        assert request.keyed_parameters == {
            int(UploadParam.TAGS): [int(ServerTag.DRAFT)],
            int(UploadParam.METADATA): {"kind": "note"},
        }


class TestResultHandling:
    """Parsing and typing of Responses"""

    def test_capability_splits_version_from_features(self) -> None:
        """The Capability List leads with the version; the rest are features"""
        link, _ = create_mailbox(ok([1, "an-extension", 7]))

        assert link.capability() == Capabilities(version=1, features=["an-extension", 7])

    def test_a_capability_list_without_a_version_is_malformed(self) -> None:
        """The first element MUST be a protocol version"""
        link, _ = create_mailbox(ok(["not a version"]))

        with pytest.raises(MalformedExchangeError):
            link.capability()

    def test_sync_collection_returns_a_collection_delta(self) -> None:
        """The Delta and the State it leads to come back named"""
        link, _ = create_mailbox(ok({0: [MID], 1: []}, b"\x02"))

        assert link.sync_collection(int(Collection.MAIL_LIST), b"") == CollectionDelta({0: [MID], 1: []}, b"\x02")

    @pytest.mark.parametrize(
        "returns",
        [
            pytest.param([["wrong-type"]], id="fetch-element-not-bytes"),
            pytest.param([[b"x", None]], id="fetch-wrong-length"),
        ],
    )
    def test_a_malformed_fetch_answer_is_refused(self, returns: list[object]) -> None:
        """A fetch answering the wrong shape raises rather than returning nonsense"""
        link, _ = create_mailbox(ok(*returns))

        with pytest.raises(MalformedExchangeError):
            link.fetch_title([MID])

    def test_fetch_tags_validates_the_inner_ids(self) -> None:
        """Tag lists carry integers; a boolean is not one"""
        link, _ = create_mailbox(ok([[True]]))

        with pytest.raises(MalformedExchangeError):
            link.fetch_tags([MID])

    def test_fetch_timestamp_returns_datetimes(self) -> None:
        """The ext -1 Timestamp arrives as an aware datetime and passes through typed"""
        instant = datetime.datetime.fromtimestamp(1757900000.5, tz=datetime.UTC)
        link, _ = create_mailbox(ok([instant, None]))

        assert link.fetch_timestamp([MID, b"\x22" * 32]) == [instant, None]

    def test_search_matches_must_be_message_ids(self) -> None:
        """A search answering anything but Bytes ids is refused"""
        link, _ = create_mailbox(ok(["not bytes"]))

        with pytest.raises(MalformedExchangeError):
            link.search_title("query")

    def test_updated_states_become_token_changes(self) -> None:
        """The packed [previous, new] pairs are repacked as named TokenChange pairs"""
        link, _ = create_mailbox(ok({0: [b"\x01", b"\x02"]}))

        assert link.delete([MID]) == {0: TokenChange(b"\x01", b"\x02")}

    @pytest.mark.parametrize(
        "states",
        [
            pytest.param({0: [b"\x01"]}, id="pair-too-short"),
            pytest.param({0: [b"\x01", "not bytes"]}, id="token-not-bytes"),
            pytest.param({"0": [b"\x01", b"\x02"]}, id="collection-not-int"),
            pytest.param("not a map", id="not-a-map"),
        ],
    )
    def test_malformed_updated_states_are_refused(self, states: object) -> None:
        """Updated States that do not hold token pairs raise"""
        link, _ = create_mailbox(ok(states))

        with pytest.raises(MalformedExchangeError):
            link.delete([MID])

    def test_upload_and_create_tags_return_their_named_results(self) -> None:
        """The two-value writes come back as UploadResult and CreatedTags"""
        link, _ = create_mailbox(ok({}, [b"\x01"]))
        assert link.upload([b"raw"]) == UploadResult({}, [b"\x01"])

        link, _ = create_mailbox(ok({}, [3]))
        assert link.create_tags(["Work"]) == CreatedTags({}, [3])


class TestSyncHelper:
    """The multi-Collection sync and its Initial-state fallback"""

    @staticmethod
    def _server(known_token: bytes) -> Responder:
        """A responder that knows one token and refuses all others with UNKNOWN_STATE"""

        def respond(request: Request) -> Response:
            last_known = request.positional_parameters[1]
            if last_known == known_token:
                return Response.ok(request.request_id, {1: "Tag"}, b"\x02")
            if last_known == b"":
                return Response.ok(request.request_id, {1: "Tag", -1: "UNREAD"}, b"\x02")
            return Response.failure(request.request_id, UnknownStateError(), RequestType.SYNC)

        return respond

    def test_a_known_token_syncs_directly(self) -> None:
        """No fallback when the server can answer the delta"""
        link, _ = create_mailbox(self._server(known_token=b"\x01"))
        outcome = link.sync({int(Collection.TAG_LIST): b"\x01"})

        assert not isinstance(outcome, NoAnswer)
        assert outcome[int(Collection.TAG_LIST)].full_resync is False

    def test_an_unknown_token_falls_back_to_initial(self) -> None:
        """UNKNOWN_STATE retries from the Initial State and flags the full resync"""
        link, transport = create_mailbox(self._server(known_token=b"\x01"))
        outcome = link.sync({int(Collection.TAG_LIST): b"forgotten"})

        assert not isinstance(outcome, NoAnswer)
        result = outcome[int(Collection.TAG_LIST)]
        assert result.full_resync is True
        assert result.delta == {1: "Tag", -1: "UNREAD"}
        assert [request.positional_parameters[1] for request in transport.sent] == [b"forgotten", b""]

    def test_silence_abandons_the_whole_sync(self) -> None:
        """Partial results on a dead link must be rolled back"""

        def respond(request: Request) -> Response | None:
            if request.positional_parameters[0] == int(Collection.MAIL_LIST):
                return Response.ok(request.request_id, {0: [], 1: []}, b"\x02")
            return None

        link, _ = create_mailbox(respond)

        assert link.sync({int(Collection.MAIL_LIST): b"", int(Collection.TAG_LIST): b""}) == NoAnswer()

    def test_a_stale_write_raises_through_the_map(self) -> None:
        """The Request id → request type map resolves STATE_MISMATCH to its typed error"""

        def respond(request: Request) -> Response:
            return Response.failure(
                request.request_id, StateMismatchError(details={0: {0: b"\x02"}}), RequestType.DELETE
            )

        link, _ = create_mailbox(respond)

        with pytest.raises(StateMismatchError):
            link.delete([MID], if_in_state={0: b"\x01"})
