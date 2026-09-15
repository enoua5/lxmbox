"""Tests for the Exchange types, their encoding, and the Request parameter accessors"""

import pytest

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
    ErrorInfoKey,
    Exchange,
    ExchangeType,
    GeneralError,
    IncompleteRequestError,
    MalformedExchangeError,
    Notification,
    NotificationType,
    Request,
    RequestType,
    Response,
    ResponseStatus,
    RnmmpError,
    UnknownExchange,
    WrongTypeError,
)

PACKET_ENCODINGS = [
    pytest.param(
        Request(1, RequestType.NOOP),
        b"\x93\x00\x01\x00",
        id="request-no-parameters",
    ),
    pytest.param(
        Request(7, RequestType.SYNC, {}, [Collection.MAIL_LIST, INITIAL_STATE_TOKEN]),
        b"\x96\x00\x07\x05\x80\x00\xc4\x00",
        id="request-with-positional-parameters",
    ),
    pytest.param(Response.ok(3, 42), b"\x94\x01\x03\x00\x2a", id="response-ok"),
    pytest.param(
        Response.failure(
            9, RnmmpError(status=ResponseStatus.NO, general_error_code=GeneralError.UNSUPPORTED, message=None)
        ),
        b"\x94\x01\x09\x01\x81\x00\x04",
        id="response-failure",
    ),
    pytest.param(
        Notification(
            NotificationType.COLLECTION_UPDATE,
            [Collection.TAG_LIST, INITIAL_STATE_TOKEN, b"\x01"],
        ),
        b"\x95\x02\x00\x01\xc4\x00\xc4\x01\x01",
        id="notification-collection-update",
    ),
    pytest.param(UnknownExchange(99, [1]), b"\x92\x63\x01", id="unknown-exchange"),
]


def _sync_request(*positional: object) -> Request:
    """Build a SYNC Request carrying the given Positional Parameters"""
    return Request(1, RequestType.SYNC, {}, list(positional))


class TestPacketFormat:
    """
    The bytes an Exchange packs into.

    Expected encodings are derived from the msgpack specification, not captured from this code.
    """

    @pytest.mark.parametrize(("exchange", "encoded"), PACKET_ENCODINGS)
    def test_an_exchange_encodes_to_the_specified_bytes(self, exchange: Exchange, encoded: bytes) -> None:
        """Every Exchange encodes to exactly the bytes the msgpack specification prescribes for it"""
        assert exchange.encode() == encoded

    @pytest.mark.parametrize(("exchange", "encoded"), PACKET_ENCODINGS)
    def test_an_exchange_decodes_back_from_the_specified_bytes(self, exchange: Exchange, encoded: bytes) -> None:
        """Decoding those same bytes reproduces the Exchange they were written for"""
        assert Exchange.decode(encoded) == exchange

    @pytest.mark.parametrize(
        ("exchange", "expected"),
        [
            pytest.param(Request(1, RequestType.NOOP), ExchangeType.REQUEST, id="request"),
            pytest.param(Response.ok(1), ExchangeType.RESPONSE, id="response"),
            pytest.param(
                Notification(NotificationType.COLLECTION_UPDATE), ExchangeType.NOTIFICATION, id="notification"
            ),
            pytest.param(UnknownExchange(99), 99, id="unknown"),
        ],
    )
    def test_each_exchange_declares_its_own_type(self, exchange: Exchange, expected: int) -> None:
        """A concrete Exchange knows its own type code; only an unrecognised one carries it as data"""
        assert exchange.exchange_type == expected
        assert exchange.to_array()[0] == expected

    def test_state_tokens_stay_bytes(self) -> None:
        """The State Token MUST be represented in msgpack using the Bin type family"""
        token = b"\x00\xff\x10"
        request = Request(1, RequestType.SYNC, {}, [Collection.MAIL_LIST, token])

        decoded = Exchange.decode(request.encode())

        assert isinstance(decoded, Request)
        assert decoded.positional_parameters[1] == token
        assert isinstance(decoded.positional_parameters[1], bytes)


class TestRequestStructure:
    """The shape of a Request array"""

    def test_the_keyed_map_is_omitted_when_nothing_follows_it(self) -> None:
        """The Keyed Parameter map is only included when additional parameters are provided"""
        assert Request(1, RequestType.NOOP).to_array() == [0, 1, 0]

    def test_an_empty_keyed_map_precedes_positional_parameters(self) -> None:
        """The first additional parameter will always be the Keyed Parameter map, even when empty"""
        request = Request(1, RequestType.DELETE, {}, [[b"\x01"]])

        assert request.to_array() == [0, 1, 18, {}, [b"\x01"]]

    def test_unrecognised_keyed_parameters_are_preserved(self) -> None:
        """A receiver MUST accept and ignore keys in the Keyed Parameter map it does not expect"""
        decoded = Exchange.decode(Request(1, RequestType.NOOP, {900: "extension"}).encode())

        assert isinstance(decoded, Request)
        assert decoded.keyed_parameters == {900: "extension"}

    def test_a_duplicate_request_id_is_accepted(self) -> None:
        """The receiver MUST accept requests with duplicate IDs"""
        first = Exchange.decode(Request(4, RequestType.NOOP).encode())
        second = Exchange.decode(Request(4, RequestType.CAPABILITY).encode())

        assert isinstance(first, Request)
        assert isinstance(second, Request)
        assert first.request_id == second.request_id == 4


class TestForwardCompatibility:
    """What happens when a peer uses a code this version does not define"""

    def test_an_unrecognised_exchange_type_decodes_rather_than_raising(self) -> None:
        """An Exchange whose type this version does not recognise is preserved, not rejected"""
        decoded = Exchange.from_array([99, "payload"])

        assert type(decoded) is UnknownExchange
        assert decoded.exchange_type == 99
        assert decoded.parameters == ["payload"]

    def test_an_unrecognised_exchange_re_encodes_unchanged(self) -> None:
        """An unrecognised Exchange survives a decode/encode cycle byte for byte"""
        encoded = b"\x92\x63\x01"

        assert Exchange.decode(encoded).encode() == encoded

    def test_an_unrecognised_request_type_stays_a_plain_integer(self) -> None:
        """Numbers outside 0-127 MAY be used for implementation-defined request types"""
        decoded = Exchange.from_array([0, 1, 5000])

        assert isinstance(decoded, Request)
        assert decoded.request_type == 5000

    def test_an_unrecognised_event_type_stays_a_plain_integer(self) -> None:
        """Extensions MAY use event-type integers outside 0-127, which a receiver MUST ignore"""
        decoded = Exchange.from_array([2, 5000, "payload"])

        assert isinstance(decoded, Notification)
        assert decoded.event_type == 5000
        assert decoded.parameters == ["payload"]


class TestMalformedExchanges:
    """Input that cannot be read as an Exchange at all"""

    @pytest.mark.parametrize(
        "array",
        [
            pytest.param([], id="empty-array"),
            pytest.param("not an array", id="string"),
            pytest.param(b"not an array", id="bytes"),
            pytest.param(7, id="integer"),
            pytest.param({0: 1}, id="map"),
            pytest.param(None, id="nil"),
            pytest.param([0, 1], id="request-without-a-request-type"),
            pytest.param([1, 1], id="response-without-a-status"),
            pytest.param([2], id="notification-without-an-event-type"),
            pytest.param([0, 1, 0, None, b"\x01"], id="request-with-a-nil-keyed-map"),
            pytest.param([0, 1, 0, ["not", "a", "map"]], id="request-with-a-list-keyed-map"),
        ],
    )
    def test_an_exchange_missing_its_required_parameters_is_rejected(self, array: object) -> None:
        """An Exchange that does not carry the parameters its type requires is a MALFORMED request"""
        with pytest.raises(MalformedExchangeError) as excinfo:
            Exchange.from_array(array)

        assert excinfo.value.status is ResponseStatus.BAD
        assert excinfo.value.package_as_dict()[ErrorInfoKey.GENERAL_ERROR] is GeneralError.MALFORMED

    def test_trailing_bytes_after_a_complete_exchange_are_rejected(self) -> None:
        """Bytes beyond the first complete msgpack value mean the frame was not packed as one Exchange"""
        with pytest.raises(MalformedExchangeError):
            Exchange.decode(b"\x93\x00\x01\x00" + b"\x93\x00\x02\x00")

    @pytest.mark.parametrize(
        "array",
        [
            pytest.param([True, 1, 0], id="exchange-type"),
            pytest.param([0, True, 0], id="request-id"),
            pytest.param([0, 1, True], id="request-type"),
            pytest.param([1, 1, True], id="response-status"),
            pytest.param([2, True], id="event-type"),
        ],
    )
    def test_a_boolean_is_not_accepted_where_an_integer_is_required(self, array: list[object]) -> None:
        """msgpack encodes `true` distinctly from an integer, so a boolean in an integer slot is rejected"""
        with pytest.raises(MalformedExchangeError):
            Exchange.from_array(array)


class TestExchangeBase:
    """Properties of the Exchange base class itself"""

    def test_the_base_exchange_cannot_be_instantiated(self) -> None:
        """The base Exchange cannot be constructed, only its concrete types"""
        with pytest.raises(TypeError):
            Exchange()  # type: ignore[abstract]


class TestResponses:
    """Building and reading a Response"""

    def test_an_ok_response_reports_success_and_carries_no_error(self) -> None:
        """For the OK status, zero or more Return Parameters are supplied as defined for the Request Type"""
        response = Response.ok(3, 42)

        assert response.is_ok
        assert response.parameters == [42]
        assert response.error(RequestType.NOOP) is None

    def test_a_failure_response_carries_the_error_information_map(self) -> None:
        """For the NO and BAD statuses, one additional Parameter MAY be supplied, and it MUST be a map"""
        error = RnmmpError(status=ResponseStatus.NO, general_error_code=GeneralError.UNSUPPORTED)
        response = Response.failure(9, error)

        assert not response.is_ok
        assert response.status is ResponseStatus.NO
        assert response.parameters[0][ErrorInfoKey.GENERAL_ERROR] is GeneralError.UNSUPPORTED

    def test_a_failure_with_nothing_to_report_omits_the_map(self) -> None:
        """The error-information Parameter is optional; an error carrying nothing to report sends no map"""
        assert Response.failure(9, RnmmpError(status=ResponseStatus.NO, message=None)).parameters == []

    def test_an_unrecognised_status_is_still_treated_as_a_failure(self) -> None:
        """A status this version does not recognise still means the request was not performed"""
        decoded = Exchange.from_array([1, 9, 77])

        assert isinstance(decoded, Response)
        assert not decoded.is_ok
        assert decoded.error() is not None


class TestGetRequired:
    """`Request.get_required`, for Positional Parameters a request type defines as required"""

    def test_a_parameter_of_the_expected_type_is_returned(self) -> None:
        """A required Positional Parameter of the declared type is returned as-is"""
        assert _sync_request(0, b"\x01").get_required(1, bytes, name="Last Known State") == b"\x01"

    @pytest.mark.parametrize(
        "request_",
        [
            pytest.param(_sync_request(0), id="absent"),
            pytest.param(_sync_request(0, None), id="explicit-nil"),
        ],
    )
    def test_an_absent_parameter_is_rejected(self, request_: Request) -> None:
        """An explicit `nil` in a required positional slot counts as missing, because none may be nil"""
        with pytest.raises(IncompleteRequestError) as excinfo:
            request_.get_required(1, bytes, name="Last Known State")

        assert excinfo.value.status is ResponseStatus.BAD
        assert excinfo.value.package_as_dict()[ErrorInfoKey.GENERAL_ERROR] is GeneralError.INCOMPLETE

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("a string", bytes, id="string-for-bytes"),
            pytest.param(b"\x01", int, id="bytes-for-int"),
            pytest.param(True, int, id="bool-for-int"),
            pytest.param(1.5, int, id="float-for-int"),
        ],
    )
    def test_a_parameter_of_the_wrong_type_is_rejected(self, value: object, expected: type) -> None:
        """A Request including a field with an unexpected datatype is a WRONG_TYPE request"""
        with pytest.raises(WrongTypeError) as excinfo:
            _sync_request(value).get_required(0, expected)

        assert excinfo.value.status is ResponseStatus.BAD
        assert excinfo.value.package_as_dict()[ErrorInfoKey.GENERAL_ERROR] is GeneralError.WRONG_TYPE

    def test_any_of_a_tuple_of_types_is_accepted(self) -> None:
        """A parameter the spec types as more than one thing is accepted as any of them"""
        assert _sync_request("a string").get_required(0, (int, str)) == "a string"

    def test_the_check_is_skipped_when_no_type_is_declared(self) -> None:
        """Omitting the expected type returns the parameter unchecked"""
        assert _sync_request(1.5).get_required(0) == 1.5


class TestGetOptional:
    """`Request.get_optional`, for Positional Parameters a request type defines as optional"""

    @pytest.mark.parametrize(
        "request_",
        [
            pytest.param(_sync_request(0), id="absent"),
            pytest.param(_sync_request(0, None), id="explicit-nil"),
        ],
    )
    def test_an_absent_parameter_falls_back_to_the_default(self, request_: Request) -> None:
        """An absent or explicitly nil optional Positional Parameter yields the caller's default"""
        assert request_.get_optional(1, bytes, default=INITIAL_STATE_TOKEN) == INITIAL_STATE_TOKEN

    def test_the_default_defaults_to_nil(self) -> None:
        """An optional parameter with no default is `None` when it was not supplied"""
        assert _sync_request(0).get_optional(1, bytes) is None

    def test_a_supplied_parameter_is_still_type_checked(self) -> None:
        """Being optional does not exempt a Positional Parameter from its declared type"""
        with pytest.raises(WrongTypeError):
            _sync_request(0, "a string").get_optional(1, bytes)


class TestGetKeyed:
    """`Request.get_keyed`, for the Keyed Parameter map"""

    def test_a_supplied_parameter_is_returned(self) -> None:
        """A Keyed Parameter present in the map is returned, resolved by its integer key"""
        request = Request(1, RequestType.DELETE, {0: {0: b"\x01"}})

        assert request.get_keyed(0, dict, name="IF_IN_STATE") == {0: b"\x01"}

    @pytest.mark.parametrize(
        "keyed",
        [
            pytest.param({}, id="absent"),
            pytest.param({0: None}, id="explicit-nil"),
        ],
    )
    def test_an_absent_parameter_falls_back_to_the_default(self, keyed: dict[int, object]) -> None:
        """Keyed Parameters are always optional, and an explicit `nil` counts as absent"""
        request = Request(1, RequestType.DELETE, keyed)

        assert request.get_keyed(0, dict, default={}) == {}

    def test_a_supplied_parameter_is_still_type_checked(self) -> None:
        """Being optional does not exempt a Keyed Parameter from its declared type"""
        request = Request(1, RequestType.DELETE, {0: "a string"})

        with pytest.raises(WrongTypeError):
            request.get_keyed(0, dict)
