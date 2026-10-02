"""The Reticulum Network Mail Management Protocol client library"""

from .deltas import apply_delta, initial_state
from .link import (
    DEFAULT_TIMEOUT,
    MailboxLink,
    NoAnswer,
    Unreachable,
    UnreachableReason,
    VoidAnswer,
    connect,
)
from .response_types import (
    Capabilities,
    CollectionDelta,
    CollectionSync,
    CollectionUpdate,
    CreatedTags,
    SingleModeSubscription,
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
    "CollectionUpdate",
    "CreatedTags",
    "ExchangeTransport",
    "LinkTransport",
    "MailboxLink",
    "NoAnswer",
    "SingleModeSubscription",
    "TokenChange",
    "Unreachable",
    "UnreachableReason",
    "VoidAnswer",
    "UpdatedStates",
    "UploadResult",
    "__version__",
    "apply_delta",
    "connect",
    "initial_state",
]
