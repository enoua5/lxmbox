"""
End-to-end test of receiving a LXMF message and managing it through rnmmp
"""

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import LXMF
import pytest
import RNS

from rnmmp_core import (
    INITIAL_STATE_TOKEN,
    Collection,
    Exchange,
    MailListDeltaKey,
    MetadataKey,
    Request,
    RequestType,
    Response,
    ResponseStatus,
    ServerTag,
    unpack,
)
from rnmmp_server.carriage import attach_receiver, send_exchange

DELIVERY_TIMEOUT = 60.0


@dataclass(frozen=True)
class LxmfEnvironment:
    """Information about the LXMF fixture created"""

    destination_hash: bytes
    delivery_hash: bytes
    authorized_identity: RNS.Identity
    router: LXMF.LXMRouter
    source: RNS.Destination
    announced_storage: Path
    """Where a second, announcing sender keeps its LXMF state"""


class LinkClient:
    """A Link to the served mailbox, with Request/Response correlation"""

    def __init__(self, destination_hash: bytes, identity: RNS.Identity) -> None:
        """Establish the Link and identify as `identity`"""
        server = RNS.Identity.recall(destination_hash)
        assert server is not None, "server identity not recalled from its announce"
        destination = RNS.Destination(server, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnmmp", "request")
        established = threading.Event()
        self.link = RNS.Link(destination, established_callback=lambda _link: established.set())
        assert established.wait(5), "link not established"
        self._responses: dict[int, Response] = {}
        self._received = threading.Condition()
        self._next_request_id = 0
        attach_receiver(self.link, self._payload_received)
        self.link.identify(identity)
        time.sleep(0.2)

    def _payload_received(self, payload: bytes) -> None:
        exchange = Exchange.decode(payload)
        if isinstance(exchange, Response):
            with self._received:
                self._responses[exchange.request_id] = exchange
                self._received.notify_all()

    def ask(self, request_type: int, *positional: Any, timeout: float = 15.0) -> Response:
        """Send one Request and wait for its Response"""
        self._next_request_id += 1
        request_id = self._next_request_id
        send_exchange(self.link, Request(request_id, request_type, {}, list(positional)))
        with self._received:
            assert self._received.wait_for(lambda: request_id in self._responses, timeout), (
                f"no response to request {request_id}"
            )
            return self._responses.pop(request_id)


def _await_path(destination_hash: bytes, what: str, timeout: float = 20.0) -> None:
    """Block until Reticulum knows a path to `destination_hash`"""
    RNS.Transport.request_path(destination_hash)
    deadline = time.time() + timeout
    while not RNS.Transport.has_path(destination_hash):
        assert time.time() < deadline, f"no path to {what}"
        time.sleep(0.1)


@pytest.fixture(scope="module")
def lxmf_environment(tmp_path_factory: pytest.TempPathFactory, reticulum_hub: int) -> Iterator[LxmfEnvironment]:
    """A mailbox and LXMF router set up for testing"""
    rundir = tmp_path_factory.mktemp("rnmmp-lxmf-integration")
    port = reticulum_hub

    identity = RNS.Identity()
    (rundir / "mailbox.json").write_text(json.dumps({"authorized": bytes(identity.hash).hex()}))

    server_script = Path(__file__).with_name("_integration_lxmf_server.py")
    process = subprocess.Popen([sys.executable, str(server_script), str(port), str(rundir)])
    try:
        deadline = time.time() + 10
        while not (rundir / "server.json").exists():
            assert process.poll() is None, "server process died during startup"
            assert time.time() < deadline, "server never reported its destinations"
            time.sleep(0.1)
        report = json.loads((rundir / "server.json").read_text())
        destination_hash = bytes.fromhex(report["destination"])
        delivery_hash = bytes.fromhex(report["delivery"])

        _await_path(destination_hash, "the served mailbox")
        _await_path(delivery_hash, "the mailbox's LXMF delivery destination")

        router = LXMF.LXMRouter(storagepath=str(rundir / "sender-lxmf"))
        source = router.register_delivery_identity(RNS.Identity(), display_name="Integration Sender")

        yield LxmfEnvironment(destination_hash, delivery_hash, identity, router, source, rundir / "announced-lxmf")
    finally:
        process.terminate()
        process.wait(timeout=10)


def send_lxmf(
    environment: LxmfEnvironment, content: bytes, title: bytes, source: RNS.Destination | None = None
) -> LXMF.LXMessage:
    """Send an LXMF message to the mailbox and return it, packed as it went out"""
    server_identity = RNS.Identity.recall(environment.delivery_hash)
    assert server_identity is not None, "delivery identity not recalled from its announce"
    destination = RNS.Destination(server_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    message = LXMF.LXMessage(
        destination,
        source or environment.source,
        content,
        title=title,
        desired_method=LXMF.LXMessage.DIRECT,
    )
    environment.router.handle_outbound(message)
    return message


def tags_of(client: LinkClient, message_id: bytes) -> list[int]:
    """The tags the mailbox holds for one message"""
    [[tags]] = client.ask(RequestType.FETCH_TAGS, [message_id]).parameters
    return list(tags)


def mail_list(client: LinkClient) -> list[bytes]:
    """Every message id the mailbox holds, learned the way a client learns it"""
    response = client.ask(RequestType.SYNC, Collection.MAIL_LIST, INITIAL_STATE_TOKEN)
    assert response.status == ResponseStatus.OK, f"SYNC failed: {response.parameters}"
    delta = unpack(response.parameters[0]) if isinstance(response.parameters[0], bytes) else response.parameters[0]
    added: list[bytes] = delta.get(MailListDeltaKey.ADDED, [])
    return added


def await_message(client: LinkClient, message_id: bytes) -> None:
    """Poll the mailbox until the delivered message shows up in its MAIL_LIST"""
    deadline = time.time() + DELIVERY_TIMEOUT
    while message_id not in mail_list(client):
        assert time.time() < deadline, "the delivered message never reached the mailbox"
        time.sleep(0.2)


class TestLxmfInRnmmpOut:
    """A message sent as LXMF, read back over rnmmp"""

    def test_lxmf_round_trip(self, lxmf_environment: LxmfEnvironment) -> None:
        """LXMF makes round trip from delivery to fetch byte identical"""
        client = LinkClient(lxmf_environment.destination_hash, lxmf_environment.authorized_identity)
        message = send_lxmf(lxmf_environment, b"the message content", b"the message title")

        await_message(client, message.hash)

        [[full]] = client.ask(RequestType.FETCH_FULL, [message.hash]).parameters
        assert full == message.packed

    def test_the_delivered_message_has_defaults_applied(self, lxmf_environment: LxmfEnvironment) -> None:
        """The ingest policies ran on the delivered message"""
        client = LinkClient(lxmf_environment.destination_hash, lxmf_environment.authorized_identity)
        message = send_lxmf(lxmf_environment, b"tagged on arrival", b"a title")

        await_message(client, message.hash)

        [[metadata]] = client.ask(RequestType.FETCH_METADATA, [message.hash]).parameters

        assert ServerTag.UNREAD in tags_of(client, message.hash)
        assert MetadataKey.RECEIVE_TIME in metadata

    def test_message_portions_round_trip(self, lxmf_environment: LxmfEnvironment) -> None:
        """The portions of a message come back the same through a round trip"""
        client = LinkClient(lxmf_environment.destination_hash, lxmf_environment.authorized_identity)
        message = send_lxmf(lxmf_environment, b"content", b"a searchable title")

        await_message(client, message.hash)

        [[payload]] = client.ask(RequestType.FETCH_PAYLOAD, [message.hash]).parameters
        assert unpack(payload)[1] == b"a searchable title"


class TestSignatureHandling:
    """Handling of the `signature_validated` flag"""

    def test_a_message_from_an_unheard_sender_arrives_unverified(self, lxmf_environment: LxmfEnvironment) -> None:
        """
        LXMF cannot check a signature whose source identity it has never heard announced.

        The sending destination in this fixture never announces, so the mailbox has no identity
        to recall and the delivery is `SOURCE_UNKNOWN`, which the test mailbox records as `UNVERIFIED_SENDER`.
        """
        client = LinkClient(lxmf_environment.destination_hash, lxmf_environment.authorized_identity)
        message = send_lxmf(lxmf_environment, b"from a stranger", b"unheard")

        await_message(client, message.hash)

        assert ServerTag.UNVERIFIED_SENDER in tags_of(client, message.hash)

    def test_a_message_verifies_once_the_sender_has_been_heard(self, lxmf_environment: LxmfEnvironment) -> None:
        """
        A sender announcement makes its further deliveries verifiable.
        """
        client = LinkClient(lxmf_environment.destination_hash, lxmf_environment.authorized_identity)
        router = LXMF.LXMRouter(storagepath=str(lxmf_environment.announced_storage))
        source = router.register_delivery_identity(RNS.Identity(), display_name="Announced Sender")
        router.announce(source.hash)

        deadline = time.time() + DELIVERY_TIMEOUT
        while True:
            message = send_lxmf(lxmf_environment, b"from a known sender", b"heard", source=source)
            await_message(client, message.hash)
            if ServerTag.UNVERIFIED_SENDER not in tags_of(client, message.hash):
                break
            assert time.time() < deadline, "the sender's announce never reached the mailbox"
            router.announce(source.hash)
            time.sleep(0.2)
