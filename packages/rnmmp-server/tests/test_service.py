"""
End-to-end tests of the rnmmp server implementation.

Everything uses a non-shared localhost TCP interface pair the harness sets up,
so we're running a "real" Reticulum network confined to localhost.
"""

import datetime
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import RNS

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
    Exchange,
    MetadataKey,
    Request,
    RequestType,
    Response,
    SearchTitleParam,
    ServerTag,
    StateMismatchDetail,
    StateMismatchError,
    UnauthenticatedError,
    UnauthorizedError,
    UnsupportedError,
    UploadParam,
    WriteParam,
    pack,
)
from rnmmp_server.carriage import attach_receiver, send_exchange

MID = b"\x11" * 32
BIG_MID = b"\x22" * 32
LXMF_HEAD = bytes(range(48)) * 2
SMALL_RAW = LXMF_HEAD + pack([1757900000.5, b"the title", b"the content", {7: b"field"}])
BIG_CONTENT = b"\xab" * 3000
BIG_RAW = LXMF_HEAD + pack([1.0, b"big", BIG_CONTENT, {}])

CLIENT_CONFIG = """[reticulum]
  enable_transport = False
  share_instance = No
  panic_on_interface_error = False

[logging]
  loglevel = 0

[interfaces]
  [[TCP Client]]
    type = TCPClientInterface
    enabled = True
    target_host = 127.0.0.1
    target_port = {port}
"""


@dataclass(frozen=True)
class ServiceEnvironment:
    """The running harness: where the served mailbox is, and who is allowed to use it."""

    destination_hash: bytes
    authorized_identity: RNS.Identity


class LinkClient:
    """One Link to the served mailbox, with Request/Response correlation."""

    def __init__(self, destination_hash: bytes, identity: RNS.Identity | None = None) -> None:
        """Establish the Link, optionally sending identification proof as `identity`"""
        server = RNS.Identity.recall(destination_hash)
        assert server is not None, "server identity not recalled from its announce"
        destination = RNS.Destination(server, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnmmp", "request")
        established = threading.Event()
        self.link = RNS.Link(destination, established_callback=lambda _link: established.set())
        assert established.wait(10), "link not established"
        self._responses: dict[int, Response] = {}
        self._received = threading.Condition()
        self._next_request_id = 0
        attach_receiver(self.link, self._payload_received)
        if identity is not None:
            self.link.identify(identity)
            time.sleep(0.2)

    def _payload_received(self, payload: bytes) -> None:
        exchange = Exchange.decode(payload)
        if isinstance(exchange, Response):
            with self._received:
                self._responses[exchange.request_id] = exchange
                self._received.notify_all()

    def ask(
        self, request_type: int, *positional: Any, keyed: dict[Any, Any] | None = None, timeout: float = 10.0
    ) -> Response:
        """Send one Request and wait for its Response."""
        self._next_request_id += 1
        request_id = self._next_request_id
        send_exchange(self.link, Request(request_id, request_type, keyed or {}, list(positional)))
        return self.response_to(request_id, timeout)

    def send_raw(self, payload: bytes) -> None:
        """Send raw bytes as a single link packet, bypassing the Exchange encoder."""
        RNS.Packet(self.link, payload).send()

    def response_to(self, request_id: int, timeout: float) -> Response:
        """The Response answering `request_id`, however its Request was sent."""
        with self._received:
            assert self._received.wait_for(lambda: request_id in self._responses, timeout), (
                f"no response to request {request_id}"
            )
            return self._responses.pop(request_id)

    def quiet(self, seconds: float) -> bool:
        """Whether nothing arrives for `seconds`."""
        time.sleep(seconds)
        with self._received:
            return not self._responses


@pytest.fixture(scope="session")
def service_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ServiceEnvironment]:
    """The child-process server and this process's client Reticulum, torn down with the session."""
    rundir = tmp_path_factory.mktemp("rnmmp-integration")
    with socket.socket() as probe:
        # Bind a random free port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # Configure the test server
    identity = RNS.Identity()
    mailbox = {
        "authorized": bytes(identity.hash).hex(),
        "messages": [
            {"id": MID.hex(), "raw": SMALL_RAW.hex(), "tags": [int(ServerTag.UNREAD)]},
            {"id": BIG_MID.hex(), "raw": BIG_RAW.hex(), "tags": []},
        ],
    }
    (rundir / "mailbox.json").write_text(json.dumps(mailbox))

    server_script = Path(__file__).with_name("_integration_server.py")
    process = subprocess.Popen([sys.executable, str(server_script), str(port), str(rundir)])
    try:
        deadline = time.time() + 20
        while not (rundir / "server.json").exists():
            assert process.poll() is None, "server process died during startup"
            assert time.time() < deadline, "server never reported its destination"
            time.sleep(0.1)
        destination_hash = bytes.fromhex(json.loads((rundir / "server.json").read_text())["destination"])

        confdir = rundir / "client-conf"
        confdir.mkdir()
        (confdir / "config").write_text(CLIENT_CONFIG.format(port=port))
        RNS.Reticulum(configdir=str(confdir))

        RNS.Transport.request_path(destination_hash)
        deadline = time.time() + 15
        while not RNS.Transport.has_path(destination_hash):
            assert time.time() < deadline, "no path to the served mailbox"
            time.sleep(0.1)

        yield ServiceEnvironment(destination_hash, identity)
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(scope="session")
def client(service_environment: ServiceEnvironment) -> LinkClient:
    """One identified, authorized Link, shared by the tests following the normal connection establishment path"""
    return LinkClient(service_environment.destination_hash, service_environment.authorized_identity)


@pytest.fixture
def unidentified_link(service_environment: ServiceEnvironment) -> LinkClient:
    """A fresh Link that hasn't sent its identification"""
    return LinkClient(service_environment.destination_hash)


@pytest.fixture
def stranger_link(service_environment: ServiceEnvironment) -> LinkClient:
    """A fresh Link identified as an identity the mailbox does not authorize"""
    return LinkClient(service_environment.destination_hash, RNS.Identity())


class TestAuthorization:
    """Who a Request is answered for."""

    def test_an_unidentified_sender_is_refused(self, unidentified_link: LinkClient) -> None:
        """A receiver MUST NOT process any Exchange received with missing authentication."""
        response = unidentified_link.ask(RequestType.CAPABILITY)

        assert type(response.error(RequestType.CAPABILITY)) is UnauthenticatedError

    def test_an_unknown_identity_is_refused(self, stranger_link: LinkClient) -> None:
        """An identity outside the mailbox's device list is not served."""
        response = stranger_link.ask(RequestType.CAPABILITY)

        assert type(response.error(RequestType.CAPABILITY)) is UnauthorizedError

    def test_an_authorized_identity_is_served(self, client: LinkClient) -> None:
        """The authorized device's Requests are answered."""
        assert client.ask(RequestType.CAPABILITY).is_ok


class TestExchanges:
    """Ordinary Requests over the Link, one packet each way."""

    def test_sync_from_initial_returns_full_state(self, client: LinkClient) -> None:
        """A full MAIL_LIST sync lists the served messages and returns a real token."""
        delta, state = client.ask(RequestType.SYNC, int(Collection.MAIL_LIST), INITIAL_STATE_TOKEN).parameters

        assert sorted(delta[0]) == sorted([MID, BIG_MID])
        assert isinstance(state, bytes) and state != INITIAL_STATE_TOKEN

    def test_fetch_title_answers_from_the_index(self, client: LinkClient) -> None:
        """Titles come back per message, `nil` for an id that is not present."""
        response = client.ask(RequestType.FETCH_TITLE, [MID, b"missing"])

        assert response.parameters == [[b"the title", None]]

    def test_fetch_timestamp_survives_the_wire_as_a_datetime(self, client: LinkClient) -> None:
        """The Timestamp type (msgpack ext -1) round-trips to an aware UTC instant."""
        (timestamps,) = client.ask(RequestType.FETCH_TIMESTAMP, [MID]).parameters

        assert timestamps == [datetime.datetime.fromtimestamp(1757900000.5, tz=datetime.UTC)]

    def test_search_title_over_the_link(self, client: LinkClient) -> None:
        """SEARCH runs end to end"""
        response = client.ask(RequestType.SEARCH_TITLE, "THE TITLE")
        assert response.parameters == [[MID]]

        keyed = {int(SearchTitleParam.EXCLUDE_TAGS): [int(ServerTag.UNREAD)]}
        assert client.ask(RequestType.SEARCH_TITLE, "THE TITLE", keyed=keyed).parameters == [[]]

    def test_an_unknown_request_type_is_unsupported(self, client: LinkClient) -> None:
        """A request type the server does not serve is refused as UNSUPPORTED, not dropped."""
        response = client.ask(99)

        assert type(response.error(99)) is UnsupportedError


class TestResources:
    """Exchanges too large for a link packet ride Resources, both directions."""

    def test_a_large_fetch_arrives_intact(self, client: LinkClient) -> None:
        """A Response beyond the packet MDU is carried as one Resource holding one Exchange."""
        (messages,) = client.ask(RequestType.FETCH_FULL, [BIG_MID], timeout=20).parameters

        assert messages == [BIG_RAW]

    def test_a_large_upload_is_stored_with_its_receive_time(self, client: LinkClient) -> None:
        """A Request beyond the packet MDU is carried as one Resource; the server records its receive time."""
        response = client.ask(
            RequestType.UPLOAD, [b"\xcd" * 2000], keyed={int(UploadParam.TAGS): [int(ServerTag.DRAFT)]}, timeout=20
        )
        updated, (upload_id,) = response.parameters
        assert int(Collection.MAIL_LIST) in updated

        (metadata,) = client.ask(RequestType.FETCH_METADATA, [upload_id]).parameters
        assert isinstance(metadata[0][int(MetadataKey.RECEIVE_TIME)], datetime.datetime)
        assert client.ask(RequestType.FETCH_CONTENT, [upload_id], timeout=20).parameters == [[b"\xcd" * 2000]]


class TestWriteSafety:
    """Optimistic concurrency across the wire."""

    def test_a_stale_write_is_rejected_with_current_tokens(self, client: LinkClient) -> None:
        """A stale IF_IN_STATE write is refused and reports the updated Collections."""
        response = client.ask(
            RequestType.DELETE, [MID], keyed={int(WriteParam.IF_IN_STATE): {int(Collection.MAIL_LIST): b"stale"}}
        )

        error = response.error(RequestType.DELETE)
        assert type(error) is StateMismatchError
        assert error is not None and list(error.details or {}) == [int(StateMismatchDetail.UPDATED_STATES)]
        assert client.ask(RequestType.FETCH_TITLE, [MID]).parameters == [[b"the title"]]


class TestMalformed:
    """The spec's rules for unparseable Exchanges, over the real link."""

    def test_a_malformed_request_with_a_readable_id_is_answered(self, client: LinkClient) -> None:
        """A `nil` Keyed Parameter map is malformed, and the readable Request id gets the answer."""
        client.send_raw(pack([0, 900, int(RequestType.SYNC), None]))

        response = client.response_to(900, timeout=10)
        assert response.error() is not None

    def test_garbage_is_discarded_silently(self, client: LinkClient) -> None:
        """When no Request id is recoverable the Exchange MUST be discarded silently."""
        client.send_raw(b"\x01\x02 garbage")

        assert client.quiet(1.0)
