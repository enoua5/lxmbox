# rnmmp-client

The **client** library for  the [Reticulum Network Mail Management Protocol](../rnmmp-core). Imported as
`rnmmp_client`.

Controls an rnmmp mailbox over a Reticulum link:
authenticates using a device identity,
syncs by requesting each Collection's delta since its last known state,
fetches message content, tags and metadata,
and writes with optimistic concurrency (`IF_IN_STATE`).

Standalone — usable on its own against any rnmmp server, independent of the lxmbox daemon and frontend.
Protocol definitions are shared with the server via [`rnmmp-core`](../rnmmp-core).

Scaffold only; see the workspace root `README.md` for status.

## Installation

As the basic `rnmmp-server` package, RNS and LXMF are *not* installed, relying instead on system-wide installations.
Use `rnmmp-server[rns]` to bring them into the local environment instead.
