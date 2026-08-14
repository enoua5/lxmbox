# rnmmp-client

The **client** library for the [Reticulum Network Mail Management Protocol](../rnmmp). Imported as `rnmmp_client`.

Controls an rnmmp mailbox over a Reticulum link:
authenticates using a device identity,
syncs by requesting changes since last known state,
fetches message content and metadata,
and sets flags/labels with optimistic concurrency.

Standalone — usable on its own against any rnmmp server, independent of the lxmbox daemon and CLI.

Scaffold only; see the workspace root `README.md` for status.
