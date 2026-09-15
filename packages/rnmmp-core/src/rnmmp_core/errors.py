"""The error-information map and the exception hierarchy for rnmmp"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .codes import (
    SPECIFIC_ERRORS,
    AddTagError,
    CreateTagError,
    DeleteTagError,
    ErrorInfoKey,
    GeneralError,
    RemoveMetadataError,
    RemoveTagError,
    RenameTagError,
    RequestType,
    ResponseStatus,
    SearchContentError,
    SearchTitleError,
    SendLxmfError,
    SendRawError,
    SetMetadataError,
    SubscribeError,
    SyncError,
    UploadError,
)

__all__ = [
    "GENERAL_ERROR_TO_EXCEPTION",
    "SPECIFIC_ERROR_TO_EXCEPTION",
    "ConflictingFiltersError",
    "DuplicateTagNameError",
    "IncompleteRequestError",
    "InvalidMetadataKeyError",
    "InvalidTagNameError",
    "MalformedExchangeError",
    "NoPassiveNotificationsError",
    "ReservedMetadataKeyError",
    "RnmmpError",
    "SendFailedError",
    "SendRefusedError",
    "ServerDefinedTagError",
    "ServerError",
    "StateMismatchError",
    "SubscriptionRefusedError",
    "TooLargeError",
    "UnauthenticatedError",
    "UnauthorizedError",
    "UnknownCollectionError",
    "UnknownDestinationError",
    "UnknownMessageError",
    "UnknownStateError",
    "UnknownTagError",
    "UnsupportedError",
    "WrongTypeError",
]


def _render_code(value: int) -> str:
    """Render a code as its enum member name where it has one, else as a bare integer"""
    return value.name if isinstance(value, IntEnum) else str(value)


def _wrap_as_enum_value[E: int](value: Any, enum: type[E] | None) -> E | int | None:
    """
    Coerce an untyped int code into its enum member value.

    Codes not present in the enum (i.e. from extensions) or with an unknown enum type
    are returned as a raw int
    """
    if not isinstance(value, int):
        return None
    if enum is None:
        return value
    try:
        return enum(value)
    except ValueError:
        return value


@dataclass(slots=True, eq=False)
class RnmmpError(Exception):
    """
    Base class for rnmmp error information and exception raising
    """

    status: ResponseStatus = ResponseStatus.BAD
    """The response status returned by the server"""
    general_error_code: GeneralError | int | None = None
    """The `GENERAL_ERROR` code: rejection reason independent of request type"""
    specific_error_code: int | None = None
    """The `SPECIFIC_ERROR` code, context dependent on the request type"""
    message: str | None = None
    """The user-facing error message"""
    details: Mapping[Any, Any] | None = None
    """The `ERROR_DETAILS` map, additional details about the error if applicable"""
    extra: Mapping[Any, Any] = field(default_factory=dict)
    """Additional non-standard values in the error map"""

    def __str__(self) -> str:
        """
        Render the error for logs and tracebacks.
        """
        codes = [f"status={_render_code(self.status)}"]
        if self.general_error_code is not None:
            codes.append(f"general={_render_code(self.general_error_code)}")
        if self.specific_error_code is not None:
            codes.append(f"specific={_render_code(self.specific_error_code)}")
        return f"{self.message} ({', '.join(codes)})" if self.message else f"({', '.join(codes)})"

    def package_as_dict(self) -> dict[Any, Any]:
        """Package values as a dict ready to be encoded as msgpack"""
        raw: dict[Any, Any] = dict(self.extra)
        if self.general_error_code is not None:
            raw[ErrorInfoKey.GENERAL_ERROR] = self.general_error_code
        if self.specific_error_code is not None:
            raw[ErrorInfoKey.SPECIFIC_ERROR] = self.specific_error_code
        if self.message is not None:
            raw[ErrorInfoKey.ERROR_MESSAGE] = self.message
        if self.details is not None:
            raw[ErrorInfoKey.ERROR_DETAILS] = dict(self.details)
        return raw

    @classmethod
    def unpackage_from_dict(
        cls,
        status: ResponseStatus,
        raw: Mapping[Any, Any],
        request_type: int | None = None,
    ) -> RnmmpError:
        """Unpack decoded error information into an error"""

        general_error_code = _wrap_as_enum_value(raw.get(ErrorInfoKey.GENERAL_ERROR), GeneralError)
        specific_enum = None
        if request_type is not None and request_type in RequestType:
            specific_enum = SPECIFIC_ERRORS.get(RequestType(request_type))
        specific_error_code = _wrap_as_enum_value(raw.get(ErrorInfoKey.SPECIFIC_ERROR), specific_enum)
        message = raw.get(ErrorInfoKey.ERROR_MESSAGE)
        details = raw.get(ErrorInfoKey.ERROR_DETAILS)
        extra = {key: value for key, value in raw.items() if key not in ErrorInfoKey}

        error_class = GENERAL_ERROR_TO_EXCEPTION.get(general_error_code, cls)

        if request_type is not None and specific_error_code is not None:
            error_class = SPECIFIC_ERROR_TO_EXCEPTION.get((request_type, specific_error_code), error_class)

        return error_class(
            status=status,
            general_error_code=general_error_code,
            specific_error_code=specific_error_code,
            message=message if isinstance(message, str) else None,
            details=details if isinstance(details, Mapping) else None,
            extra=extra,
        )


################################################################################
# General errors
################################################################################


@dataclass(slots=True, eq=False)
class UnauthenticatedError(RnmmpError):
    """Client failed to authenticate before making a request"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.UNAUTHENTICATED
    message: str | None = "Unauthenticated; unknown identity"


@dataclass(slots=True, eq=False)
class UnauthorizedError(RnmmpError):
    """Client is not authorized to perform the requested transaction"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.UNAUTHORIZED
    message: str | None = "Unauthorized; unexpected identity"


@dataclass(slots=True, eq=False)
class IncompleteRequestError(RnmmpError):
    """Request is missing required information"""

    status: ResponseStatus = ResponseStatus.BAD
    general_error_code: GeneralError | int | None = GeneralError.INCOMPLETE
    message: str | None = "Request is missing required information"


@dataclass(slots=True, eq=False)
class WrongTypeError(RnmmpError):
    """Request included a field with an unexpected datatype"""

    status: ResponseStatus = ResponseStatus.BAD
    general_error_code: GeneralError | int | None = GeneralError.WRONG_TYPE
    message: str | None = "Request included a field with an unexpected datatype"


@dataclass(slots=True, eq=False)
class UnsupportedError(RnmmpError):
    """Server has not implemented the requested functionality"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.UNSUPPORTED
    message: str | None = "Server has not implemented the requested functionality"


@dataclass(slots=True, eq=False)
class TooLargeError(RnmmpError):
    """The server refuses to process the request because it exceeds size limits or storage space"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.TOO_LARGE
    message: str | None = "The server refuses to process the request because it exceeds size limits or storage space"


@dataclass(slots=True, eq=False)
class ServerError(RnmmpError):
    """Server encountered an unexpected error"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.SERVER_ERROR
    message: str | None = "Server encountered an unexpected error"


@dataclass(slots=True, eq=False)
class StateMismatchError(RnmmpError):
    """Expected the mailbox to be in a state it was not found to be in"""

    status: ResponseStatus = ResponseStatus.NO
    general_error_code: GeneralError | int | None = GeneralError.STATE_MISMATCH
    message: str | None = "Expected the mailbox to be in a state it was not found to be in"


@dataclass(slots=True, eq=False)
class MalformedExchangeError(RnmmpError):
    """The request was malformed and could not be parsed"""

    status: ResponseStatus = ResponseStatus.BAD
    general_error_code: GeneralError | int | None = GeneralError.MALFORMED
    message: str | None = "The request was malformed and could not be parsed"


################################################################################
# Request-specific errors
################################################################################


@dataclass(slots=True, eq=False)
class UnknownCollectionError(RnmmpError):
    """
    Server does not have a Collection with the requested id.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server does not have a Collection with the requested id"


@dataclass(slots=True, eq=False)
class UnknownDestinationError(RnmmpError):
    """
    Server refuses to send LXMF notifications to the requested Destination
    because it does not recognize it as trusted.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server does not recognize the notification Destination as trusted"


@dataclass(slots=True, eq=False)
class NoPassiveNotificationsError(RnmmpError):
    """
    Server refuses to send LXMF notifications, only supporting notifications over an active link.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server only supports notifications over an active link"


@dataclass(slots=True, eq=False)
class SubscriptionRefusedError(RnmmpError):
    """
    Server refuses to send notifications as requested for unspecified/other reasons.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server refuses to send notifications as requested"


@dataclass(slots=True, eq=False)
class UnknownStateError(RnmmpError):
    """
    Server cannot generate a delta from the given state to the current state.
    Client should retry with the Initial State Token.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server cannot generate a delta from the given state; resync from the Initial State"


@dataclass(slots=True, eq=False)
class ConflictingFiltersError(RnmmpError):
    """
    The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "The same tag was specified in both ONLY_TAGS and EXCLUDE_TAGS"


@dataclass(slots=True, eq=False)
class UnknownTagError(RnmmpError):
    """
    A supplied Tag ID does not exist in the TAG_LIST Collection.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "A supplied Tag ID does not exist"


@dataclass(slots=True, eq=False)
class UnknownMessageError(RnmmpError):
    """
    A supplied Message ID does not exist in the MAIL_LIST Collection.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "A supplied Message ID does not exist"


@dataclass(slots=True, eq=False)
class ReservedMetadataKeyError(RnmmpError):
    """
    A supplied Metadata key is one the server manages itself and does not let a client write or remove.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "A supplied Metadata key is managed by the server itself"


@dataclass(slots=True, eq=False)
class InvalidMetadataKeyError(RnmmpError):
    """
    A supplied Metadata key is neither an integer nor a string, or is otherwise rejected by the server.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "A supplied Metadata key is neither an integer nor a string, or was rejected by the server"


@dataclass(slots=True, eq=False)
class InvalidTagNameError(RnmmpError):
    """
    The server considers the tag name invalid.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "The server considers the tag name invalid"


@dataclass(slots=True, eq=False)
class DuplicateTagNameError(RnmmpError):
    """
    The supplied name is already in use by another tag.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "The supplied tag name is already in use by another tag"


@dataclass(slots=True, eq=False)
class ServerDefinedTagError(RnmmpError):
    """
    The request tried to delete or rename a Server-Defined Tag.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "Server-Defined Tags cannot be deleted or renamed"


@dataclass(slots=True, eq=False)
class SendFailedError(RnmmpError):
    """
    The server failed to deliver the message.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "The server failed to deliver the message"


@dataclass(slots=True, eq=False)
class SendRefusedError(RnmmpError):
    """
    The server refuses to transmit the message.
    """

    status: ResponseStatus = ResponseStatus.NO
    message: str | None = "The server refuses to transmit the message"


################################################################################
# Error code mapping
################################################################################


GENERAL_ERROR_TO_EXCEPTION: dict[GeneralError | int | None, type[RnmmpError]] = {
    GeneralError.UNAUTHENTICATED: UnauthenticatedError,
    GeneralError.UNAUTHORIZED: UnauthorizedError,
    GeneralError.INCOMPLETE: IncompleteRequestError,
    GeneralError.WRONG_TYPE: WrongTypeError,
    GeneralError.UNSUPPORTED: UnsupportedError,
    GeneralError.TOO_LARGE: TooLargeError,
    GeneralError.SERVER_ERROR: ServerError,
    GeneralError.STATE_MISMATCH: StateMismatchError,
    GeneralError.MALFORMED: MalformedExchangeError,
}
"""Mapping from general error codes to error classes"""

SPECIFIC_ERROR_TO_EXCEPTION: dict[tuple[int, int], type[RnmmpError]] = {
    (RequestType.SUBSCRIBE, SubscribeError.UNKNOWN_COLLECTION): UnknownCollectionError,
    (RequestType.SUBSCRIBE, SubscribeError.UNKNOWN_DESTINATION): UnknownDestinationError,
    (RequestType.SUBSCRIBE, SubscribeError.NO_PASSIVE_NOTIFS): NoPassiveNotificationsError,
    (RequestType.SUBSCRIBE, SubscribeError.REFUSED): SubscriptionRefusedError,
    (RequestType.SYNC, SyncError.UNKNOWN_COLLECTION): UnknownCollectionError,
    (RequestType.SYNC, SyncError.UNKNOWN_STATE): UnknownStateError,
    (RequestType.SEARCH_TITLE, SearchTitleError.CONFLICTING_FILTERS): ConflictingFiltersError,
    (RequestType.SEARCH_CONTENT, SearchContentError.CONFLICTING_FILTERS): ConflictingFiltersError,
    (RequestType.UPLOAD, UploadError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.UPLOAD, UploadError.RESERVED_KEY): ReservedMetadataKeyError,
    (RequestType.CREATE_TAG, CreateTagError.INVALID_NAME): InvalidTagNameError,
    (RequestType.DELETE_TAG, DeleteTagError.SERVER_DEFINED_TAG): ServerDefinedTagError,
    (RequestType.RENAME_TAG, RenameTagError.SERVER_DEFINED_TAG): ServerDefinedTagError,
    (RequestType.RENAME_TAG, RenameTagError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.RENAME_TAG, RenameTagError.INVALID_NAME): InvalidTagNameError,
    (RequestType.RENAME_TAG, RenameTagError.DUPLICATE_NAME): DuplicateTagNameError,
    (RequestType.ADD_TAG, AddTagError.UNKNOWN_MESSAGE): UnknownMessageError,
    (RequestType.ADD_TAG, AddTagError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.REMOVE_TAG, RemoveTagError.UNKNOWN_MESSAGE): UnknownMessageError,
    (RequestType.REMOVE_TAG, RemoveTagError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.SET_METADATA, SetMetadataError.UNKNOWN_MESSAGE): UnknownMessageError,
    (RequestType.SET_METADATA, SetMetadataError.RESERVED_KEY): ReservedMetadataKeyError,
    (RequestType.SET_METADATA, SetMetadataError.INVALID_KEY): InvalidMetadataKeyError,
    (RequestType.REMOVE_METADATA, RemoveMetadataError.UNKNOWN_MESSAGE): UnknownMessageError,
    (RequestType.REMOVE_METADATA, RemoveMetadataError.RESERVED_KEY): ReservedMetadataKeyError,
    (RequestType.SEND_RAW, SendRawError.SEND_FAILED): SendFailedError,
    (RequestType.SEND_RAW, SendRawError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.SEND_RAW, SendRawError.REFUSED): SendRefusedError,
    (RequestType.SEND_LXMF, SendLxmfError.SEND_FAILED): SendFailedError,
    (RequestType.SEND_LXMF, SendLxmfError.UNKNOWN_TAG): UnknownTagError,
    (RequestType.SEND_LXMF, SendLxmfError.REFUSED): SendRefusedError,
}
"""
Mapping from (request type, error code) pairings to an error class
"""
