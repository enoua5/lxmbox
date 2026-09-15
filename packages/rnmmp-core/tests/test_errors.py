"""
Tests for the error-information map and the exception hierarchy
"""

import pytest

from rnmmp_core import (
    GENERAL_ERROR_TO_EXCEPTION,
    SPECIFIC_ERROR_TO_EXCEPTION,
    AddTagError,
    ErrorInfoKey,
    GeneralError,
    RequestType,
    ResponseStatus,
    RnmmpError,
    StateMismatchDetail,
    StateMismatchError,
    SyncError,
    UnknownStateError,
    UnknownTagError,
    UnsupportedError,
    WrongTypeError,
)


class TestRendering:
    """How an error reads in a log or a traceback"""

    def test_the_message_and_codes_are_both_rendered(self) -> None:
        """An error states what went wrong and under which codes it will travel"""
        error = UnknownTagError(specific_error_code=AddTagError.UNKNOWN_TAG, message="tag 7 does not exist")

        assert str(error) == "tag 7 does not exist (status=NO, specific=UNKNOWN_TAG)"

    def test_codes_render_as_their_enum_names(self) -> None:
        """A code the protocol names is rendered by that name"""
        assert str(UnsupportedError(message=None)) == "(status=NO, general=UNSUPPORTED)"

    def test_an_unrecognised_code_renders_as_a_bare_integer(self) -> None:
        """A code from an extension has no name to render, so the number itself is shown"""
        assert str(RnmmpError(status=ResponseStatus.NO, general_error_code=900, message=None)) == (
            "(status=NO, general=900)"
        )

    def test_an_error_carrying_nothing_still_renders_its_status(self) -> None:
        """Even an error with no message and no codes says whether the request was performed"""
        assert str(RnmmpError(message=None)) == "(status=BAD)"

    def test_the_message_reaches_a_traceback(self) -> None:
        """The message appears when a raised error is rendered"""
        with pytest.raises(RnmmpError) as excinfo:
            raise UnknownTagError(message="tag 7 does not exist")

        assert "tag 7 does not exist" in str(excinfo.value)


class TestIdentity:
    """Errors behave like exceptions"""

    def test_an_error_is_hashable(self) -> None:
        """An error can go in a set or serve as a dict key."""
        error = UnknownTagError()

        assert error in {error}

    def test_two_errors_are_distinct_even_when_identical(self) -> None:
        """Errors compare by identity, not by field values"""
        assert UnknownTagError() != UnknownTagError()


class TestPackaging:
    """Turning an exception into the map that travels in a NO or BAD Response"""

    def test_only_the_fields_that_were_set_are_packaged(self) -> None:
        """An absent code MUST NOT appear in the map as a nil"""
        packaged = UnsupportedError(message=None).package_as_dict()

        assert packaged == {ErrorInfoKey.GENERAL_ERROR: GeneralError.UNSUPPORTED}

    def test_every_field_is_packaged_under_its_reserved_key(self) -> None:
        """The four reserved keys carry the general code, specific code, message and details"""
        error = RnmmpError(
            status=ResponseStatus.NO,
            general_error_code=GeneralError.STATE_MISMATCH,
            specific_error_code=1,
            message="stale",
            details={StateMismatchDetail.UPDATED_STATES: {0: b"\x01"}},
        )

        assert error.package_as_dict() == {
            ErrorInfoKey.GENERAL_ERROR: GeneralError.STATE_MISMATCH,
            ErrorInfoKey.SPECIFIC_ERROR: 1,
            ErrorInfoKey.ERROR_MESSAGE: "stale",
            ErrorInfoKey.ERROR_DETAILS: {StateMismatchDetail.UPDATED_STATES: {0: b"\x01"}},
        }

    def test_implementation_defined_keys_are_packaged(self) -> None:
        """The map MAY include implementation-defined key-value pairs"""
        error = RnmmpError(status=ResponseStatus.NO, message=None, extra={900: "vendor detail"})

        assert error.package_as_dict() == {900: "vendor detail"}


GENERAL_ERROR_CASES = [
    pytest.param(code, cls, id=code.name)
    for code, cls in GENERAL_ERROR_TO_EXCEPTION.items()
    if isinstance(code, GeneralError)
]


class TestUnpackaging:
    """Recovering a typed exception from a received error-information map"""

    def test_general_error_smoke(self) -> None:
        """Make sure the general errors we collected make sense"""
        assert len(GENERAL_ERROR_CASES) == len(GENERAL_ERROR_TO_EXCEPTION)

    @pytest.mark.parametrize(("code", "expected"), GENERAL_ERROR_CASES)
    def test_every_general_error_recovers_its_exception(self, code: GeneralError, expected: type[RnmmpError]) -> None:
        """Each general error code maps to the exception class that represents it"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.GENERAL_ERROR: code})

        assert type(error) is expected
        assert error.general_error_code is code

    @pytest.mark.parametrize(
        ("request_type", "code", "expected"),
        [
            pytest.param(request_type, code, cls, id=f"{RequestType(request_type).name}-{code}")
            for (request_type, code), cls in SPECIFIC_ERROR_TO_EXCEPTION.items()
        ],
    )
    def test_every_specific_error_recovers_its_exception(
        self, request_type: int, code: int, expected: type[RnmmpError]
    ) -> None:
        """Each (request type, specific code) pair maps to the exception class that represents it"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.SPECIFIC_ERROR: code}, request_type)

        assert type(error) is expected

    def test_a_specific_code_is_resolved_into_its_request_types_enum(self) -> None:
        """The same code means different things per request type, so it is named against that type"""
        error = RnmmpError.unpackage_from_dict(
            ResponseStatus.NO, {ErrorInfoKey.SPECIFIC_ERROR: SyncError.UNKNOWN_STATE}, RequestType.SYNC
        )

        assert type(error) is UnknownStateError
        assert error.specific_error_code is SyncError.UNKNOWN_STATE

    def test_a_specific_code_outranks_a_general_one(self) -> None:
        """A request-type-specific code says more than the general code accompanying it"""
        error = RnmmpError.unpackage_from_dict(
            ResponseStatus.NO,
            {
                ErrorInfoKey.GENERAL_ERROR: GeneralError.SERVER_ERROR,
                ErrorInfoKey.SPECIFIC_ERROR: SyncError.UNKNOWN_STATE,
            },
            RequestType.SYNC,
        )

        assert type(error) is UnknownStateError

    def test_a_specific_code_is_not_resolved_without_its_request_type(self) -> None:
        """A Response does not carry the request type, so a caller that cannot supply it gets the general error"""
        error = RnmmpError.unpackage_from_dict(
            ResponseStatus.NO,
            {ErrorInfoKey.GENERAL_ERROR: GeneralError.STATE_MISMATCH, ErrorInfoKey.SPECIFIC_ERROR: 1},
        )

        assert type(error) is StateMismatchError

    def test_the_message_and_details_survive(self) -> None:
        """An error carries its message and details through to the client that receives it"""
        error = RnmmpError.unpackage_from_dict(
            ResponseStatus.NO,
            {
                ErrorInfoKey.GENERAL_ERROR: GeneralError.STATE_MISMATCH,
                ErrorInfoKey.ERROR_MESSAGE: "stale",
                ErrorInfoKey.ERROR_DETAILS: {0: {1: b"\x02"}},
            },
        )

        assert error.message == "stale"
        assert error.details == {0: {1: b"\x02"}}

    def test_a_round_trip_preserves_the_packaged_map(self) -> None:
        """Packaging an unpackaged error reproduces the map it came from"""
        raw = {
            ErrorInfoKey.GENERAL_ERROR: GeneralError.TOO_LARGE,
            ErrorInfoKey.ERROR_MESSAGE: "too many messages",
            900: "vendor detail",
        }

        assert RnmmpError.unpackage_from_dict(ResponseStatus.NO, raw).package_as_dict() == raw


class TestUnrecognisedCodes:
    """Codes from an extension, or from a newer version of the protocol."""

    def test_an_unrecognised_general_code_is_kept_as_an_integer(self) -> None:
        """A code with no enum member is preserved"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.GENERAL_ERROR: 900})

        assert type(error) is RnmmpError
        assert error.general_error_code == 900

    def test_an_unrecognised_specific_code_is_kept_as_an_integer(self) -> None:
        """An unmapped specific code still reaches the caller under a general exception"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.SPECIFIC_ERROR: 900}, RequestType.SYNC)

        assert error.specific_error_code == 900

    def test_an_unrecognised_request_type_does_not_resolve_specific_codes(self) -> None:
        """A request type this version does not define has no specific-error table to resolve against"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.SPECIFIC_ERROR: 1}, 5000)

        assert error.specific_error_code == 1

    def test_implementation_defined_keys_are_preserved(self) -> None:
        """Keys outside the reserved range are kept rather than discarded"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, {ErrorInfoKey.GENERAL_ERROR: 4, 900: "detail"})

        assert error.extra == {900: "detail"}

    @pytest.mark.parametrize(
        ("raw", "field"),
        [
            pytest.param({ErrorInfoKey.ERROR_MESSAGE: 7}, "message", id="non-string-message"),
            pytest.param({ErrorInfoKey.ERROR_DETAILS: "not a map"}, "details", id="non-map-details"),
            pytest.param({ErrorInfoKey.GENERAL_ERROR: "not a code"}, "general_error_code", id="non-integer-code"),
        ],
    )
    def test_a_field_of_the_wrong_type_is_dropped(self, raw: dict[int, object], field: str) -> None:
        """A peer sending the wrong type for a reserved key does not break the receiving client"""
        error = RnmmpError.unpackage_from_dict(ResponseStatus.NO, raw)

        assert getattr(error, field) is None


class TestDecodeFailureCodes:
    """The exceptions raised while decoding default to the codes their Response will carry"""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(WrongTypeError(), GeneralError.WRONG_TYPE, id="wrong-type"),
            pytest.param(UnsupportedError(), GeneralError.UNSUPPORTED, id="unsupported"),
            pytest.param(StateMismatchError(), GeneralError.STATE_MISMATCH, id="state-mismatch"),
        ],
    )
    def test_an_error_knows_its_own_general_code(self, error: RnmmpError, expected: GeneralError) -> None:
        """Each general-error exception defaults to the code it represents"""
        assert error.general_error_code is expected

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(WrongTypeError(), ResponseStatus.BAD, id="wrong-type-is-bad"),
            pytest.param(UnsupportedError(), ResponseStatus.NO, id="unsupported-is-no"),
        ],
    )
    def test_an_error_knows_its_own_status(self, error: RnmmpError, expected: ResponseStatus) -> None:
        """A request that was not understood is BAD; one understood but not performed is NO"""
        assert error.status is expected
