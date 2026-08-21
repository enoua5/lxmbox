# rnmmp-server

The **server** implementation of the [Reticulum Network Mail Management Protocol](../rnmmp-core). Imported as `rnmmp_server`.

Serves a mailbox over Reticulum: binds the per-mailbox `rnmmp.request` destination, authorizes every
Exchange by the sender's Reticulum identity, and answers the requests.

Standalone — usable on its own to serve any rnmmp client, independent of lxmbox.
Protocol definitions are shared with the client via [`rnmmp-core`](../rnmmp-core).

Scaffold only; see the workspace root `README.md` for status.
