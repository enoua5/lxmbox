# lxmbox-cli

The `lxmbox` command-line client, using **rnmmp** (via [`rnmmp-client`](../rnmmp-client)) to access mailboxes
and the **`lxmbox.control`** protocol to manage an lxmbox server.

It keeps a local cache/mirror for offline reading, but has no authoritative state of its own —
mailboxes, messages, flags, contacts, profiles, etc all live on the daemon.

Imported as `lxmbox_cli`; installs the `lxmbox` console script.

Scaffold only; see the workspace root `README.md` for status.
