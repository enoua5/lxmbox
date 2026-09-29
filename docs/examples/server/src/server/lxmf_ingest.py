"""Inbound LXMF handling"""

import LXMF
import RNS

from rnmmp_core import ServerTag
from rnmmp_server import MailboxModel

from .config import Config

UNVERIFIED_REASONS = {
    LXMF.LXMessage.SOURCE_UNKNOWN: "source unknown",
    LXMF.LXMessage.SIGNATURE_INVALID: "signature invalid",
}
"""Why LXMF could not verify a delivered message, for logging"""


class LxmfIngest:
    """Inbound LXMF handling"""

    def __init__(
        self,
        config: Config,
        identity: RNS.Identity,
        mailbox: MailboxModel,
    ) -> None:
        """Init LXMF delivery for `mailbox`"""

        self._mailbox = mailbox

        self._router = LXMF.LXMRouter(storagepath=str(config.storage_path / "mail"))
        destination = self._router.register_delivery_identity(identity, display_name="Test Mailbox")

        if not destination:
            RNS.log("Could not register identity for LXMF delivery.", RNS.LOG_ERROR)
            RNS.exit(2)
            return

        self._destination = destination
        self._router.register_delivery_callback(self.on_delivery)
        RNS.log("Ready to receive on: " + RNS.prettyhexrep(destination.hash))

    def on_delivery(self, message: LXMF.LXMessage) -> None:
        """
        Handle LXMF delivery.

        NOTE the server implementation must handle signature validation,
        the `rnmmp_server` library just trusts whatever it's handed.

        In this example, we accept the message but add an `UNVERIFIED_SENDER` tag to it.
        Rejecting the message entirely would also be a reasonable action to take.
        """

        tags = []
        if not message.signature_validated:
            reason = UNVERIFIED_REASONS.get(message.unverified_reason, "unknown reason")
            RNS.log(f"Ingesting an unverified message ({reason})", RNS.LOG_WARNING)
            tags.append(ServerTag.UNVERIFIED_SENDER)

        self._mailbox.ingest_lxmf(message, tags=tags)

    def announce(self) -> None:
        """Announce destination"""
        self._router.announce(self._destination.hash)
