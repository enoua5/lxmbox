"""
A test implementation server using the server protocol implementation.

Built from a fixture (`test_service.service_environment`) for end-to-end tests.
"""

import datetime
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
    Serve the mailbox described in the run directory until terminated

    Reads `mailbox.json` from the set up run directory for settings and initial state

    Dumps `server.json` in the same directory with the destination hash on launch
    """
    port, rundir = int(sys.argv[1]), sys.argv[2]
    confdir = os.path.join(rundir, "server-conf")
    os.makedirs(confdir, exist_ok=True)
    with open(os.path.join(confdir, "config"), "w") as config:
        config.write(CONFIG.format(port=port))

    import RNS

    RNS.Reticulum(configdir=confdir)

    from rnmmp_core import MetadataKey
    from rnmmp_server import MailboxModel, MailboxService, MemoryStore

    with open(os.path.join(rundir, "mailbox.json")) as mailbox_file:
        spec = json.load(mailbox_file)
    authorized_hash = bytes.fromhex(spec["authorized"])

    model = MailboxModel(MemoryStore())
    for message in spec["messages"]:
        model.ingest(
            bytes.fromhex(message["id"]),
            bytes.fromhex(message["raw"]),
            lxmf=True,
            tags=message["tags"],
            metadata={int(MetadataKey.RECEIVE_TIME): datetime.datetime.now(datetime.UTC)},
        )

    service = MailboxService(model, RNS.Identity(), check_authorized=lambda sender: sender == authorized_hash)
    with open(os.path.join(rundir, "server.json"), "w") as report:
        json.dump({"destination": service.destination_hash.hex()}, report)

    while True:
        service.announce()
        time.sleep(2)


if __name__ == "__main__":
    main()
