# rnmmp

The **Reticulum Network Mail Management Protocol** (rnmmp) is a Reticulum-native
mailbox-management protocol. This package holds server implementation of the protocol.

`rnmmp` is IMAP-inspired but rebuilt from scratch around Reticulum's primitives:
link identity verification for authentication, minimized request and response sizes,
and LXMF-specific features.

The protocol is specified in [the rnmmp spec](../../docs/spec/rnmmp.md).
The client half lives in the separate [`rnmmp-client`](../rnmmp-client) package.

Scaffold only; see the workspace root `README.md` for status.
