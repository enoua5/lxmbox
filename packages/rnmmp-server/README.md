# rnmmp-server

The **server** implementation of the [Reticulum Network Mail Management Protocol](../rnmmp-core). Imported as `rnmmp_server`.

This is the implementation of the rnmmp portion of a mailbox alone.
For a full mailbox implementation that handles deliveries,
use the lxmbox server implementation, or use this library to implement your own.

See `/docs/examples/server` for an example implementation.

## Installation

As the basic `rnmmp-server` package, RNS and LXMF are *not* installed, relying instead on system-wide installations.
Use `rnmmp-server[rns]` to bring them into the local environment instead.

## Usage

`MailboxService` is the actual Reticulum server.
It handles binding an Identity and authentication handler to a `MailboxModel`.

`MailboxModel` handles the actual rnmmp server logic.
It can be used as-is or extended to add extended functionality.

`MailboxModel` in turn needs a `Store` handling the storage of mailbox data.
The only Store included with rnmmp-server is `MemoryStore`, which just uses an in-memory store not intended for real-world use.
There is also `ScanSearch(Store)` which implements the search portion of a `Store` from its other methods, allowing easy implementation of a store without store-specific search optimizations.
