"""
The integer codes rnmmp uses in place of names in packets
"""

from collections.abc import Mapping
from enum import IntEnum
from typing import Final

__all__ = [
    "AddTagError",
    "Collection",
    "CreateTagError",
    "DeleteTagError",
    "ErrorInfoKey",
    "ExchangeType",
    "GeneralError",
    "INITIAL_STATE_TOKEN",
    "MailListDeltaKey",
    "MetadataKey",
    "NotificationType",
    "PROTOCOL_VERSION",
    "RemoveMetadataError",
    "RemoveTagError",
    "RenameTagError",
    "RequestType",
    "SPECIFIC_ERRORS",
    "SearchContentError",
    "SearchContentParam",
    "SearchTitleError",
    "SearchTitleParam",
    "SendLxmfError",
    "SendLxmfParam",
    "SendRawError",
    "SendRawParam",
    "ServerTag",
    "SetMetadataError",
    "ResponseStatus",
    "StateMismatchDetail",
    "SubscribeError",
    "SyncError",
    "UploadError",
    "UploadParam",
    "WriteParam",
]

################################################################################
# General constants
################################################################################

PROTOCOL_VERSION: Final = 1
"""The protocol version, as reported in the first element of the `CAPABILITY` list"""

INITIAL_STATE_TOKEN: Final = b""
"""
The Initial State Token.

The zero-length byte string, which a client uses as its last known State Token to indicate it need a full resync.
"""

################################################################################
# General enums
################################################################################


class ExchangeType(IntEnum):
    """The Exchange Type IDs"""

    REQUEST = 0
    """Request expecting a response"""
    RESPONSE = 1
    """Response to a request"""
    NOTIFICATION = 2
    """Notification not expecting a response"""


class ResponseStatus(IntEnum):
    """The status code of a Response"""

    OK = 0
    """The action was performed"""

    NO = 1
    """The request was understood, but was either ignored or an error was encountered"""

    BAD = 2
    """The request was not understood"""


class RequestType(IntEnum):
    """The Request Type IDs"""

    NOOP = 0
    """No action to be performed, may be sent periodically to keep a link"""
    CAPABILITY = 1
    """Fetch information about the server's supported features"""
    SUBSCRIBE = 2
    """Indicate that the client would like to receive active updates regarding a Collection state"""
    UNSUBSCRIBE = 3
    """Indicate that the client would like to stop receiving active updates regarding a Collection state"""
    LIST_SUBSCRIPTIONS = 4
    """List active subscriptions for Single Mode destinations"""
    SYNC = 5
    """Get the delta for a Collection from a given State Token"""
    FETCH_FULL = 6
    """Fetch raw stored messages"""
    FETCH_HEAD = 7
    """Fetch the Destination, Source, and Signature fields of stored LXMF messages"""
    FETCH_PAYLOAD = 8
    """Fetch the Payload portion of stored LXMF messages"""
    FETCH_CONTENT = 9
    """Fetch the Content portion of stored messages"""
    FETCH_FIELDS = 10
    """Fetch the Fields portion of stored LXMF messages"""
    FETCH_TIMESTAMP = 11
    """Fetch the Timestamp portion of stored LXMF messages"""
    FETCH_TITLE = 12
    """Fetch the Title portion of stored LXMF messages"""
    FETCH_TAGS = 13
    """Fetch the Tags present on messages"""
    FETCH_METADATA = 14
    """Fetch the Metadata present on messages"""
    SEARCH_TITLE = 15
    """Search messages by the Title portion"""
    SEARCH_CONTENT = 16
    """Search messages by the Content portion """
    UPLOAD = 17
    """Add messages to the MAIL_LIST Collection manually outside of the built-in delivery mechanism"""
    DELETE = 18
    """Remove messages from the MAIL_LIST Collection"""
    CREATE_TAG = 19
    """Add named tags to the TAG_LIST Collection"""
    DELETE_TAG = 20
    """Remove named tags from the TAG_LIST Collection"""
    RENAME_TAG = 21
    """Rename tags in the TAG_LIST Collection"""
    ADD_TAG = 22
    """Add tags to MESSAGE_TAG Collection"""
    REMOVE_TAG = 23
    """Remove tags from the MESSAGE_TAG Collection"""
    SET_METADATA = 24
    """Add entries to items in the METADATA Collection"""
    REMOVE_METADATA = 25
    """Remove entries from items in the METADATA Collection"""
    SEND_RAW = 26
    """Send a raw message from the server to another destination"""
    SEND_LXMF = 27
    """Send an LXMF message from the server to another destination"""


class NotificationType(IntEnum):
    """The Notification Type IDs"""

    COLLECTION_UPDATE = 0


class Collection(IntEnum):
    """The Collection IDs"""

    MAIL_LIST = 0
    TAG_LIST = 1
    MESSAGE_TAG = 2
    METADATA = 3


class ServerTag(IntEnum):
    """The Server-Defined Tags the specification defines"""

    UNREAD = -1
    """Message unread"""
    RESPONDED = -2
    """Message has been responded to"""
    IMPORTANT = -3
    """Message is important"""
    TRASH = -4
    """Message marked for eventual deletion"""
    OUTBOX = -5
    """Message was sent from this mailbox"""
    DRAFT = -6
    """Message is a draft from this mailbox"""
    FORWARDED = -7
    """Message has been forwarded to another server"""
    JUNK = -8
    """Message is junk/spam"""
    SUSPICIOUS = -9
    """Message is suspicious/phishing"""
    DELIVERED = -10
    """Outbox message is known to have been delivered"""
    UNVERIFIED_SENDER = -11
    """Message arrived without a verifiable sender"""


class MetadataKey(IntEnum):
    """Standard Metadata Map keys"""

    RECEIVE_TIME = 0
    """When the message was received by the server"""


class MailListDeltaKey(IntEnum):
    """The keys of a MAIL_LIST Delta"""

    ADDED = 0
    DELETED = 1


class ErrorInfoKey(IntEnum):
    """Error information keys potentially returned for a `NO` or `BAD` Response"""

    GENERAL_ERROR = 0
    """The request was rejected for a generally-applicable reason"""
    SPECIFIC_ERROR = 1
    """An error code returned as defined by the request type spec"""
    ERROR_MESSAGE = 2
    """An implementation-defined user-facing error message"""
    ERROR_DETAILS = 3
    """A map of error details as specified for the combination of Request Type and Error Type"""


class GeneralError(IntEnum):
    """Values for the `GENERAL_ERROR` error key, applicable to any error type"""

    UNAUTHENTICATED = 0
    """May be returned to an unauthenticated client instead of silently ignoring a request"""
    UNAUTHORIZED = 1
    """May be returned to a client with an unexpected identity instead of silently ignoring a request"""
    INCOMPLETE = 2
    """Request is missing required information"""
    WRONG_TYPE = 3
    """A Request included a field with an unexpected datatype"""
    UNSUPPORTED = 4
    """The server understands the request, but has not implemented the functionality"""
    TOO_LARGE = 5
    """The server refuses to process the request because it exceeds size limits or storage space"""
    SERVER_ERROR = 6
    """The server encountered an error while processing the request and could not continue"""
    STATE_MISMATCH = 7
    """The client expected the mailbox to be in a state it was not found to be in"""
    MALFORMED = 8
    """The request was malformed and could not be parsed"""


class StateMismatchDetail(IntEnum):
    """Keys of the `ERROR_DETAILS` map accompanying a `STATE_MISMATCH` general error"""

    UPDATED_STATES = 0
    """Maps Collection id to that Collection's current State Token."""


################################################################################
# Keyed parameters
################################################################################


class WriteParam(IntEnum):
    """Keyed Parameters shared by every request that mutates state"""

    IF_IN_STATE = 0
    """Idempotency check, set to last known state"""


class UploadParam(IntEnum):
    """Keyed Parameters of UPLOAD"""

    IF_IN_STATE = 0
    """Idempotency check, set to last known state"""
    TAGS = 1
    """Tags to set"""
    METADATA = 2
    """Metadata to set"""


class SendRawParam(IntEnum):
    """Keyed Parameters of SEND_RAW"""

    IF_IN_STATE = 0
    """Idempotency check, set to last known state"""
    STORE = 1
    """Store an outbox copy"""
    TAGS = 2
    """Tags to set on the outbox copy"""


class SendLxmfParam(IntEnum):
    """Keyed Parameters of SEND_LXMF"""

    IF_IN_STATE = 0
    """Idempotency check, set to last known state"""
    STORE = 1
    """Store an outbox copy"""
    TAGS = 2
    """Tags to set on the outbox copy"""


class SearchTitleParam(IntEnum):
    """Keyed Parameters of SEARCH_TITLE"""

    MAX_RESULTS = 0
    """Limit on results to return"""
    ONLY_TAGS = 1
    """Return messages that have all these tags"""
    EXCLUDE_TAGS = 2
    """Return messages that have none of these tags"""


class SearchContentParam(IntEnum):
    """Keyed Parameters of SEARCH_CONTENT"""

    MAX_RESULTS = 0
    """Limit on results to return"""
    ONLY_TAGS = 1
    """Return messages that have all these tags"""
    EXCLUDE_TAGS = 2
    """Return messages that have none of these tags"""


################################################################################
# Specific error codes
################################################################################


class SubscribeError(IntEnum):
    """Specific error codes for SUBSCRIBE"""

    UNKNOWN_COLLECTION = 0
    """Server does not have a Collection with the requested id"""
    UNKNOWN_DESTINATION = 1
    """
    Server refuses to send LXMF notifications to the requested Destination
    because it does not recognize it as trusted
    """
    NO_PASSIVE_NOTIFS = 2
    """Server refuses to send LXMF notifications, only supporting notifications over an active link"""
    REFUSED = 3
    """Server refuses to send notifications as requested for unspecified/other reasons"""


class SyncError(IntEnum):
    """Specific error codes for SYNC."""

    UNKNOWN_COLLECTION = 0
    """Server does not have a Collection with the requested id"""
    UNKNOWN_STATE = 1
    """
    Server cannot generate a delta from the given state to the current state.
    Client should retry with the Initial State Token
    """


class SearchTitleError(IntEnum):
    """Specific error codes for SEARCH_TITLE"""

    CONFLICTING_FILTERS = 0
    """The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS"""


class SearchContentError(IntEnum):
    """Specific error codes for SEARCH_CONTENT"""

    CONFLICTING_FILTERS = 0
    """The client specified the same tag in both ONLY_TAGS and EXCLUDE_TAGS"""


class UploadError(IntEnum):
    """Specific error codes for UPLOAD"""

    UNKNOWN_TAG = 0
    """A Tag ID in TAGS does not exist in the TAG_LIST Collection"""
    RESERVED_KEY = 1
    """A METADATA key is one the server manages itself and does not accept from a client"""


class CreateTagError(IntEnum):
    """Specific error codes for CREATE_TAG"""

    INVALID_NAME = 0
    """The server considers the tag name invalid"""


class DeleteTagError(IntEnum):
    """Specific error codes for DELETE_TAG"""

    SERVER_DEFINED_TAG = 0
    """The request tried to delete a Server-Defined Tag"""


class RenameTagError(IntEnum):
    """Specific error codes for RENAME_TAG"""

    SERVER_DEFINED_TAG = 0
    """The request tried to rename a Server-Defined Tag"""
    UNKNOWN_TAG = 1
    """A supplied Tag ID does not exist in the TAG_LIST Collection"""
    INVALID_NAME = 2
    """The server does not support the provided tag name"""
    DUPLICATE_NAME = 3
    """The supplied name is already in use by another tag"""


class AddTagError(IntEnum):
    """Specific error codes for ADD_TAG"""

    UNKNOWN_MESSAGE = 0
    """A supplied Message ID does not exist in the MAIL_LIST Collection"""
    UNKNOWN_TAG = 1
    """A supplied Tag ID does not exist in the TAG_LIST Collection"""


class RemoveTagError(IntEnum):
    """Specific error codes for REMOVE_TAG"""

    UNKNOWN_MESSAGE = 0
    """A supplied Message ID does not exist in the MAIL_LIST Collection"""
    UNKNOWN_TAG = 1
    """A supplied Tag ID does not exist in the TAG_LIST Collection"""


class SetMetadataError(IntEnum):
    """Specific error codes for SET_METADATA"""

    UNKNOWN_MESSAGE = 0
    """A supplied Message ID does not exist in the MAIL_LIST Collection"""
    RESERVED_KEY = 1
    """A supplied key is one the server manages itself and does not accept from a client"""
    INVALID_KEY = 2
    """A supplied key is neither an integer nor a string, or is otherwise rejected by the server"""


class RemoveMetadataError(IntEnum):
    """Specific error codes for REMOVE_METADATA"""

    UNKNOWN_MESSAGE = 0
    """A supplied Message ID does not exist in the MAIL_LIST Collection"""
    RESERVED_KEY = 1
    """A supplied key is one the server manages itself and does not allow a client to remove"""


class SendRawError(IntEnum):
    """Specific error codes for SEND_RAW"""

    SEND_FAILED = 0
    """The server failed to deliver the message"""
    UNKNOWN_TAG = 1
    """A Tag ID in TAGS does not exist in the TAG_LIST Collection"""
    REFUSED = 2
    """The server refuses to transmit the message"""


class SendLxmfError(IntEnum):
    """Specific error codes for SEND_LXMF"""

    SEND_FAILED = 0
    """The server failed to deliver the message"""
    UNKNOWN_TAG = 1
    """A Tag ID in TAGS does not exist in the TAG_LIST Collection"""
    REFUSED = 2
    """The server refuses to transmit the message"""


################################################################################
# Helpers
################################################################################

SPECIFIC_ERRORS: Final[Mapping[RequestType, type[IntEnum]]] = {
    RequestType.SUBSCRIBE: SubscribeError,
    RequestType.SYNC: SyncError,
    RequestType.SEARCH_TITLE: SearchTitleError,
    RequestType.SEARCH_CONTENT: SearchContentError,
    RequestType.UPLOAD: UploadError,
    RequestType.CREATE_TAG: CreateTagError,
    RequestType.DELETE_TAG: DeleteTagError,
    RequestType.RENAME_TAG: RenameTagError,
    RequestType.ADD_TAG: AddTagError,
    RequestType.REMOVE_TAG: RemoveTagError,
    RequestType.SET_METADATA: SetMetadataError,
    RequestType.REMOVE_METADATA: RemoveMetadataError,
    RequestType.SEND_RAW: SendRawError,
    RequestType.SEND_LXMF: SendLxmfError,
}
"""
Which specific-error enum applies to which request type,
if the request type defines specific errors
"""
