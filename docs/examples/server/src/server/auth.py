"""Auth system for the example server"""

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

import RNS


def get_allowed_identity_path() -> Path | None:
    """Get the path to the allowed identities file"""

    file_name = "allowed_identities"

    config_dirs = [
        Path("/etc/rnmmp/"),
        Path(os.path.expanduser("~/.config/rnmmp/")),
        Path(os.path.expanduser("~/.rnmmp")),
    ]

    for config_dir in config_dirs:
        path = config_dir / file_name
        if os.path.isfile(path):
            return path

    return None


class ReticulumAuth:
    """Handle authorization for the mailbox control"""

    def __init__(
        self,
        *,
        allowed_identity_hashes: Iterable[bytes] | None = None,
        identity_allowed_file: Path | None = None,
        auth_disabled: bool = False,
    ) -> None:
        """
        Initialize the authorization system

        Args:
            allowed_identity_hashes: A list of identities that are allowed
            identity_allowed_file: A path to a file containing either a newline-separated list of
                allowed identity hashes in hex, or if executable, a program that accepts
                a hex identity hash as a command-line argument and returns a newline-separated list
                of identity hashes in hex to stdout ran per check
        """

        self._allowed_identity_hashes = list(allowed_identity_hashes or [])
        self._identity_allowed_file = identity_allowed_file
        self._auth_disabled = auth_disabled

    def is_auth_disabled(self) -> bool:
        """Whether auth is disabled"""
        return self._auth_disabled

    def is_identity_statically_allowed(self, identity: bytes) -> bool:
        """Whether the given identity is defined as allowed from the command line arguments"""
        return identity in self._allowed_identity_hashes

    def _parse_allow_file(self, file_text: bytes) -> list[bytes]:
        """Parse allow file text"""

        lines = file_text.splitlines()

        allow_list: list[bytes] = []

        for line in lines:
            if len(line) == RNS.Identity.TRUNCATED_HASHLENGTH // 8 * 2:
                try:
                    allowed_hash = bytes.fromhex(line.decode("utf-8"))
                    allow_list.append(allowed_hash)
                except Exception as e:
                    RNS.log("Could not decode RNS Identity hash from: " + str(line), RNS.LOG_DEBUG)
                    RNS.log("The contained exception was: " + str(e), RNS.LOG_DEBUG)

        return allow_list

    def _get_allowed_from_exe(self, file_path: Path, identity: bytes) -> list[bytes]:
        """Get the allowed identities from an executable allow file"""
        allowed_input = subprocess.run([file_path, identity.hex()], stdout=subprocess.PIPE).stdout
        return self._parse_allow_file(allowed_input)

    def _get_allowed_from_file(self, file_path: Path) -> list[bytes]:
        """Get the allowed identities from a static allow file"""
        # NOTE it would probably make sense to cache this
        # Not really worth implementing for the example server, though
        with open(file_path, "rb") as f:
            allowed_input = f.read()
        return self._parse_allow_file(allowed_input)

    def is_identity_allowed_by_file(self, identity: bytes) -> bool:
        """Whether the identity is allowed by the `allowed_identity` file"""

        if not self._identity_allowed_file:
            return False

        if not os.path.isfile(self._identity_allowed_file):
            return False

        if os.access(self._identity_allowed_file, os.X_OK):
            allow_list = self._get_allowed_from_exe(self._identity_allowed_file, identity)
        else:
            allow_list = self._get_allowed_from_file(self._identity_allowed_file)

        return identity in allow_list

    def is_authorized(self, identity: bytes) -> bool:
        """Whether the given identity hash is authorized"""

        if self.is_auth_disabled():
            return True
        if self.is_identity_statically_allowed(identity):
            return True
        return self.is_identity_allowed_by_file(identity)
