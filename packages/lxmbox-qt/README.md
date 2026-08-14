# lxmbox-qt

The lxmbox native desktop client, built with Qt ([PySide6](https://doc.qt.io/qtforpython/)).

Runs on a user's own device, using the configured identity to communicate with the rnmmp/lxmbox server (via `lxmbox-cli`'s client library).

It keeps a local cache for offline reading and integrates with the desktop (system notifications, tray).
Unlike the NomadNet and web frontends, it is not part of the Docker stack — it is installed on the user's machine.

Scaffold only; see the workspace root `README.md` for status.
