# lxmbox

lxmbox is a self-hosted, [Reticulum](https://reticulum.network/)-based mail
system. It allows you to run your own mailboxes over Reticulum's encrypted,
infrastructure-independent network, and lets you read and send mail from a command line,
a [NomadNet](https://github.com/markqvist/NomadNet) node, or a web browser.

Mail is carried as [LXMF](https://github.com/markqvist/lxmf), Reticulum's message
format, so it interoperates with existing Reticulum tools. Mailbox *management* —
listing, fetching, flags, folders, multi-device sync — uses a purpose-built,
Reticulum-native protocol: **rnmmp** (Reticulum Network Mail Management Protocol).
This protocol is an open standard and may be reimplemented by other clients and servers.

lxmbox bundles the rnmmp protocol (a standalone client and server), a multi-mailbox
daemon, a command-line client, and a few frontend clients, all designed to run together on
hardware as small as a Raspberry Pi 3.

> [!INFO]
> **Status: early scaffolding.** This repository currently contains the
> workspace layout and package skeletons only. Nothing here is functional yet.

## Security model — read this first

> [!DANGER]
> **lxmbox has no user accounts — all authenticated users can read all mail on their instance.**

This follows from how the mailboxes work: mail is stored decrypted on the host, and
a mailbox is intended to be single-user. The daemon and client necessarily handle
decrypted message contents. lxmbox does not add a multi-user login system to the
backend to work around this — doing so would just encourage unsafe use.

**Privacy between people is achieved by running separate instances.** If two
people need private mailboxes, they run two lxmbox instances on hosts they each
control. We want to encourage users to embrace Reticulum's philosophy of *decentralization*.

*Profiles* replace the traditional notion of users: a profile groups a person's
contacts, default mailboxes, notification settings, etc.
They make a shared (e.g. household) instance pleasant to use;
they do not isolate one person's mail from another's.

### Packages

This is a [`uv`](https://docs.astral.sh/uv/) workspace; each component is a
package under `packages/`:

| Package | Role |
|---|---|
| [`rnmmp`](packages/rnmmp) | The rnmmp protocol and server implementation. Can stand alone and act as a server for any rnmmp client. |
| [`rnmmp-client`](packages/rnmmp-client) | The rnmmp client library for building client implementations. |
| [`lxmbox-mailboxd`](packages/lxmbox-mailboxd) | The mail daemon, hosting multiple mailbox identities. Accepts LXMF deliveries and serves rnmmp per mailbox, plus an `lxmbox.control` API for non-rnmmp functionality. |
| [`lxmbox-cli`](packages/lxmbox-cli) | The `lxmbox` CLI: a simple rnmmp + lxmbox client with a local cache. |
| [`lxmbox-nomad`](packages/lxmbox-nomad) | NomadNetwork frontend. |
| [`lxmbox-web`](packages/lxmbox-web) | Django PWA frontend (used outside of Reticulum for convenience). |

These packages are intended to be as standalone as possible, allowing you to mix-and-match them with other implementations as you please.

## Development

Requires Python 3.13+ and `uv`.

```sh
# From the repository root:
uv sync --all-packages          # create the workspace environment
uv run pytest                   # run the test suite
uv run ruff check .             # lint
uv run mypy packages/rnmmp/src   # type-check the protocol core
```

## License

MIT. See [LICENSE](LICENSE).
