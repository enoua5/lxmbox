"""
Partial type stubs for the RNS surface rnmmp uses, written to match RNS 1.4.2.

RNS doesn't ship MyPy-compatible type information, so these stubs are what mypy checks our RNS usage against.
They only cover what this workspace touches, so it's not meant for use outside of here.
"""

from collections.abc import Callable
from typing import BinaryIO

LOG_CRITICAL: int
LOG_ERROR: int
LOG_WARNING: int
LOG_NOTICE: int
LOG_INFO: int
LOG_VERBOSE: int
LOG_DEBUG: int
LOG_EXTREME: int

def log(msg: str, level: int = ...) -> None: ...

class Identity:
    hash: bytes
    def __init__(self, create_keys: bool = True) -> None: ...
    def get_private_key(self) -> bytes: ...
    @staticmethod
    def from_bytes(prv_bytes: bytes) -> Identity | None: ...
    @staticmethod
    def recall(target_hash: bytes, from_identity_hash: bool = False) -> Identity | None: ...

class Destination:
    IN: int
    OUT: int
    SINGLE: int
    hash: bytes
    def __init__(self, identity: Identity | None, direction: int, type: int, app_name: str, *aspects: str) -> None: ...
    def announce(
        self,
        app_data: bytes | None = None,
        path_response: bool = False,
        attached_interface: object | None = None,
        tag: bytes | None = None,
        send: bool = True,
    ) -> Packet | None: ...
    def set_link_established_callback(self, callback: Callable[[Link], None]) -> None: ...

class Link:
    MDU: int
    link_id: bytes
    ACCEPT_NONE: int
    ACCEPT_APP: int
    ACCEPT_ALL: int
    def __init__(
        self,
        destination: Destination | None = None,
        established_callback: Callable[[Link], None] | None = None,
        closed_callback: Callable[[Link], None] | None = None,
    ) -> None: ...
    def identify(self, identity: Identity) -> None: ...
    def get_remote_identity(self) -> Identity | None: ...
    def set_packet_callback(self, callback: Callable[[bytes, Packet], None]) -> None: ...
    def set_resource_strategy(self, resource_strategy: int) -> None: ...
    def set_resource_started_callback(self, callback: Callable[[Resource], None]) -> None: ...
    def set_resource_concluded_callback(self, callback: Callable[[Resource], None]) -> None: ...
    def set_remote_identified_callback(self, callback: Callable[[Link, Identity], None]) -> None: ...
    def set_link_closed_callback(self, callback: Callable[[Link], None]) -> None: ...
    def teardown(self) -> None: ...

class Packet:
    def __init__(self, destination: Destination | Link, data: bytes) -> None: ...
    def send(self) -> object: ...

class Resource:
    COMPLETE: int
    FAILED: int
    status: int
    data: bytes | BinaryIO | None
    link: Link
    def __init__(
        self,
        data: bytes,
        link: Link,
        metadata: object | None = None,
        advertise: bool = True,
        auto_compress: bool = True,
        callback: Callable[[Resource], None] | None = None,
        progress_callback: Callable[[Resource], None] | None = None,
        timeout: float | None = None,
    ) -> None: ...

class Reticulum:
    MTU: int
    def __init__(
        self,
        configdir: str | None = None,
        loglevel: int | None = None,
        logdest: object | None = None,
        verbosity: int | None = None,
    ) -> None: ...

class Transport:
    @staticmethod
    def has_path(destination_hash: bytes) -> bool: ...
    @staticmethod
    def request_path(
        destination_hash: bytes,
        on_interface: object | None = None,
        tag: bytes | None = None,
        recursive: bool = False,
    ) -> None: ...
