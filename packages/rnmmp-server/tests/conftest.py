"""
Shared fixtures for rnmmp-server tests
"""

import socket
from collections.abc import Iterator

import pytest
import RNS

HUB_CONFIG = """[reticulum]
  enable_transport = False
  share_instance = No
  panic_on_interface_error = False

[logging]
  loglevel = 0

[interfaces]
  [[TCP Server]]
    type = TCPServerInterface
    enabled = True
    listen_ip = 127.0.0.1
    listen_port = {port}
"""


def get_free_port() -> int:
    """Acquire a free port for use"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def reticulum_hub(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """Start this process's Reticulum and yield the port test servers should connect to"""
    port = get_free_port()
    confdir = tmp_path_factory.mktemp("rnmmp-hub") / "hub-conf"
    confdir.mkdir()
    (confdir / "config").write_text(HUB_CONFIG.format(port=port))
    RNS.Reticulum(configdir=str(confdir))
    yield port
