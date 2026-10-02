"""
A test server that takes LXMF delivery and serves the mailbox over rnmmp
"""

import json
import os
import sys
import time

CONFIG = """[reticulum]
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


def main() -> None:
    """
    Serve a mailbox taking in LXMF delivery.

    Reads `mailbox.json` from the run directory for the identity allowed to use rnmmp,
    and writes `server.json` with both destination hashes once they exist.
    """
    port, rundir = int(sys.argv[1]), sys.argv[2]
    confdir = os.path.join(rundir, "server-conf")
    os.makedirs(confdir, exist_ok=True)
    with open(os.path.join(confdir, "config"), "w") as config:
        config.write(CONFIG.format(port=port))

    import LXMF
    import RNS

    RNS.Reticulum(configdir=confdir)

    from rnmmp_core import ServerTag
    from rnmmp_server import MailboxModel, MailboxService, MemoryStore

    with open(os.path.join(rundir, "mailbox.json")) as mailbox_file:
        spec = json.load(mailbox_file)
    authorized_hash = bytes.fromhex(spec["authorized"])

    model = MailboxModel(MemoryStore())

    delivery_identity = RNS.Identity()
    router = LXMF.LXMRouter(storagepath=os.path.join(rundir, "lxmf"))
    delivery_destination = router.register_delivery_identity(delivery_identity, display_name="Integration Mailbox")

    def on_delivery(message: LXMF.LXMessage) -> None:
        """Ingest a delivered message, marking it if LXMF could not verify it"""
        tags = [] if message.signature_validated else [ServerTag.UNVERIFIED_SENDER]
        model.ingest_lxmf(message, tags=tags)

    router.register_delivery_callback(on_delivery)

    service = MailboxService(model, RNS.Identity(), check_authorized=lambda sender: sender == authorized_hash)
    with open(os.path.join(rundir, "server.json"), "w") as report:
        json.dump(
            {
                "destination": service.destination_hash.hex(),
                "delivery": bytes(delivery_destination.hash).hex(),
            },
            report,
        )

    while True:
        service.announce()
        router.announce(delivery_destination.hash)
        time.sleep(2)


if __name__ == "__main__":
    main()
