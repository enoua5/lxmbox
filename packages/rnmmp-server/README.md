# rnmmp-server

The **server** implementation of the [Reticulum Network Mail Management Protocol](../rnmmp-core). Imported as `rnmmp_server`.

Serves a mailbox over Reticulum: binds the per-mailbox `rnmmp.request` destination, authorizes every
Exchange by the sender's Reticulum identity, and answers the requests.

Standalone — usable on its own to serve any rnmmp client, independent of lxmbox.
Protocol definitions are shared with the client via [`rnmmp-core`](../rnmmp-core).

Contains the storage-agnostic mailbox model — Collections, State Tokens, delta sync — over a
`Store` protocol with an in-memory reference backend. The Reticulum binding is not written yet.

## Installation

As the basic `rnmmp-server` package, RNS and LXMF are *not* installed, relying instead on system-wide installations.
Use `rnmmp-server[rns]` to bring them into the local environment instead.
