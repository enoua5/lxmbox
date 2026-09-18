"""The Reticulum Network Mail Management Protocol client library"""

from .deltas import apply_delta, initial_state
from .link import (
    DEFAULT_TIMEOUT,
    MailboxLink,
    NoAnswer,
    Unreachable,
    UnreachableReason,
    connect,
)
from .response_types import (
    Capabilities,
    CollectionDelta,
    CollectionSync,
    CreatedTags,
    TokenChange,
    UpdatedStates,
    UploadResult,
)
from .transport import ExchangeTransport, LinkTransport

__version__ = "0.0.0"

__all__ = [
    "DEFAULT_TIMEOUT",
    "Capabilities",
    "CollectionDelta",
    "CollectionSync",
    "CreatedTags",
    "ExchangeTransport",
    "LinkTransport",
    "MailboxLink",
    "NoAnswer",
    "TokenChange",
    "Unreachable",
    "UnreachableReason",
    "UpdatedStates",
    "UploadResult",
    "__version__",
    "apply_delta",
    "connect",
    "initial_state",
]
