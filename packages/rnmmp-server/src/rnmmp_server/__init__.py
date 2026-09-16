"""The Reticulum Network Mail Management Protocol server"""

from .model import (
    RESERVED_METADATA_KEYS,
    SERVER_DEFINED_TAG_NAMES,
    TOKEN_LENGTH,
    MailboxModel,
    TokenPair,
    UpdatedStates,
)
from .store import ChangeSet, LogEntry, MemoryStore, Store, StoredMessage

__version__ = "0.0.0"

__all__ = [
    "RESERVED_METADATA_KEYS",
    "SERVER_DEFINED_TAG_NAMES",
    "TOKEN_LENGTH",
    "ChangeSet",
    "LogEntry",
    "MailboxModel",
    "MemoryStore",
    "Store",
    "StoredMessage",
    "TokenPair",
    "UpdatedStates",
    "__version__",
]
