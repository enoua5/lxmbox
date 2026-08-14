# lxmbox

lxmbox is a self-hosted, [Reticulum](https://reticulum.network/)-based mail
system. It allows you to run your own mailboxes over Reticulum's encrypted,
infrastructure-independent network, and lets you read and send mail from a command line,
a [NomadNet](https://github.com/markqvist/NomadNet) node, or a web browser.

It is built on [POPR](https://github.com/faragher/POPR)
and [LXMF](https://github.com/markqvist/lxmf);
standard Reticulum protocols allowing interoperability with existing Reticulum tooks.

lxmbox bundles a standalone POPR implementation, a multi-mailbox host,
a management library and CLI, and two frontends,
all designed to run together on hardware as small as a Raspberry Pi 3.

> [!INFO]
> **Status: early scaffolding.** This repository currently contains the
> workspace layout and package skeletons only. Nothing here is functional yet.

## Security model — read this first

> [!DANGER]
> **lxmbox has no user accounts — all authenticated users can read all mail on their instance.**

This follows how POPR mailboxes work: a mailbox is single-user by design.
The mailbox host and client necessarily handle decrypted message contents.
lxmbox does not add a multi-user login system to the backend to work around this —
doing so would be just encourage unsafe use.

**Privacy between people is achieved by running separate instances.** If two
people need private mailboxes, they run two lxmbox instances on hosts they each
control. We want to encourage users to embrace Reticulum's philosophy of *decentralization*.

*Profiles* replace the traditional notion of users: each profile has its own
client identity, message read flags, default mailbox, etc.
They make a shared (e.g. household) instance pleasant to use;
they do not isolate one person's mail from another's.

### Packages

This is a [`uv`](https://docs.astral.sh/uv/) workspace; each component is a
package under `packages/`:

| Package | Role |
|---|---|
| [`lxmbox-popr`](packages/lxmbox-popr) | A POPR-protocol client and server library (imported as `lxmbox_popr`). |
| [`lxmbox-mailboxd`](packages/lxmbox-mailboxd) | The POPR mailbox host. Handles storing LXMF messages coming in or out, as well as some configuration. Can be set up to handle multiple mailbox identities. |
| [`lxmbox-mgmt`](packages/lxmbox-mgmt) | Management library and the `lxmbox` CLI: profiles, local mailbox mirror, contacts, etc. |
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
uv run mypy packages/lxmbox-popr/src   # type-check the protocol core
```

## License

MIT. See [LICENSE](LICENSE).
