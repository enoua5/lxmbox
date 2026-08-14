# lxmbox-mailboxd

The lxmbox mail daemon. Hosts multiple mailboxes in one process;
each mailbox has its own identity and serves three Reticulum destination aspects:
- `lxmf.delivery` (for incoming mail)
- `rnmmp.manage` (the [rnmmp](../rnmmp) mailbox-management protocol)
- `rnmmp.send` (outbound submission, repacked with the mailbox as the sender)

The daemon also serves a single `lxmbox.control` aspect for non-rnmmp controls:
creating mailboxes, managing authorized identities and profiles, contacts, etc.

Backed by an authoritative SQLite store: messages keyed by their LXMF message-id, labels, flags, etc.
Also handles retention/quotas and blackhole/allowed-identity filtering.

Scaffold only; see the workspace root `README.md` for status.
