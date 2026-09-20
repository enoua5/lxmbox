"""Inbound LXMF handling"""

import LXMF
import RNS

from rnmmp_server import MailboxModel

from .config import Config


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
        """Handle LXMF delivery"""

        self._mailbox.ingest_lxmf(message)

    def announce(self) -> None:
        """Announce destination"""
        self._router.announce(self._destination.hash)
