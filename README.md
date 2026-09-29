# lxmbox

lxmbox is a self-hosted, [Reticulum](https://reticulum.network/)-based mail system.
It allows you to run your own mailboxes over Reticulum's encrypted, infrastructure-independent network,
and lets you read and send mail remotely over any of the supported clients.

Mail is carried via Reticulum's standard message format, [LXMF](https://github.com/markqvist/lxmf),
so it interoperates with existing Reticulum messaging tools. Mailbox *management* —
listing, fetching, flags, folders, multi-device sync —
uses a purpose-built Reticulum protocol on top of the Reticulum stack:
**rnmmp** (Reticulum Network Mail Management Protocol).
This protocol is an open standard and may be reimplemented by other clients and servers.

lxmbox bundles the rnmmp protocol together with an LXMF destination and additional management tools into a full mailbox system.

> [!INFO]
> 
> **Status: in early development.** There will be stubs everywhere for awhile,
> and what does exist will have drastic breaking changes.

## Security model

> [!DANGER]
> 
> **lxmbox has no user accounts — all authenticated users can read all mail on their instance.**

Reticulum follows a philosophy of self-hosting and decentralization.
lxmbox and rnmmp have been designed to make the self-hosting as easy as possible,
and centralized hosting to be difficult.

A mailbox can be hosted on a relatively lightweight Reticulum node (currently planning to be usable on a Raspberry Pi 3).
Each mailbox is intended to be single-user, without a real account system.
lxmbox does not add a multi-user login system to the backend to work around this — doing so would just encourage unsafe use.

**Privacy between people is achieved by running separate instances.** If two people need private mailboxes,
they need to run two lxmbox instances on hosts they each control.

*Profiles* in lxmbox replace the traditional notion of users:
a profile groups a person's contacts, default mailboxes, notification settings, etc.
They make a shared (e.g. household) instance pleasant to use; they do not isolate one person's mail from another's.

### Packages

This is a [`uv`](https://docs.astral.sh/uv/) workspace; each component is a
package under `packages/`:

| Package | Role |
|---|---|
| [`rnmmp-core`](packages/rnmmp-core) | The rnmmp protocol constants and utilities shared by client and server. |
| [`rnmmp-server`](packages/rnmmp-server) | The rnmmp server implementation. Can stand alone and act as a server for any rnmmp client. |
| [`rnmmp-client`](packages/rnmmp-client) | The rnmmp client library for building client implementations. |
| [`lxmbox-mailboxd`](packages/lxmbox-mailboxd) | The mail daemon, hosting multiple mailbox identities. Accepts LXMF deliveries and serves rnmmp per mailbox, plus an `lxmbox.control` API for non-rnmmp functionality. |
| [`lxmbox-cli`](packages/lxmbox-cli) | The `lxmbox` CLI: a simple rnmmp + lxmbox client with a local cache. |
| [`lxmbox-qt`](packages/lxmbox-qt) | Native desktop client, built with Qt (PySide6) |
| [`lxmbox-nomad`](packages/lxmbox-nomad) | NomadNetwork frontend. |
| [`lxmbox-web`](packages/lxmbox-web) | Django PWA frontend (used outside of Reticulum for convenience). |

These packages are intended to be as standalone as possible, allowing you to mix-and-match them with other implementations as you please.

## Development

Requires Python 3.13+ and `uv`.

```sh
# From the repository root:
uv sync --all-packages --all-extras  # create the workspace environment
uv run pytest                        # run the test suite
uv run ruff check .                  # lint
uv run ruff format --check .         # check formatting
uv run mypy                          # run type checking
```

## Contributing

I am not currently accepting contributions.
I will probably open that up once there's a stable first version.

## License

MIT. See [LICENSE](LICENSE).
