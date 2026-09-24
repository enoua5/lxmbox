"""The Reticulum Network Mail Management Protocol server"""

from .deltas import compose_deltas, is_empty_delta, make_empty_delta, pack_fragment, unpack_fragment
from .handlers import handle
from .model import (
    RESERVED_METADATA_KEYS,
    SERVER_DEFINED_TAG_NAMES,
    TOKEN_LENGTH,
    MailboxModel,
    TokenPair,
    UpdatedStates,
)
from .service import APP_NAME, MailboxService
from .store import ChangeSet, LogEntry, MemoryStore, MessageIndex, ScanSearch, Store, StoredMessage

__version__ = "0.0.0"

__all__ = [
    "APP_NAME",
    "RESERVED_METADATA_KEYS",
    "SERVER_DEFINED_TAG_NAMES",
    "TOKEN_LENGTH",
    "ChangeSet",
    "LogEntry",
    "MailboxModel",
    "MailboxService",
    "MemoryStore",
    "MessageIndex",
    "ScanSearch",
    "Store",
    "StoredMessage",
    "TokenPair",
    "UpdatedStates",
    "__version__",
    "compose_deltas",
    "make_empty_delta",
    "handle",
    "is_empty_delta",
    "pack_fragment",
    "unpack_fragment",
]
