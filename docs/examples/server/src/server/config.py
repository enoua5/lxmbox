"""Config and argument handler"""

from __future__ import annotations

import argparse
import configparser
from collections.abc import Sequence
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import RNS

from rnmmp_server import APP_NAME

DEFAULT_CONFIG_FILE: Final = Path("config.ini")
"""The config file when `--config` is not given"""

SECTION: Final = "server"
"""The main config file section"""

TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
"""Values understood as `True`"""

FALSE_VALUES: Final = frozenset({"0", "false", "no", "off"})
"""Values understood as `False`"""


class ConfigError(Exception):
    """Failed to read a config value"""


@dataclass(frozen=True, slots=True)
class Config:
    """The settings the server runs with"""

    allowed_identities: list[bytes]
    """The list of statically allowed identity hashes"""

    announce_period: float
    """The time between announces; <= 0 only does the initial announce"""

    disable_auth: bool
    """Whether to disable authorization"""

    identity_name: str
    """The identity to load and use for the mailbox"""

    rns_config_path: Path | None
    """The directory Reticulum reads its own config from; `None` leaves RNS its default"""

    storage_path: Path
    """The directory the mailbox writes its messages and state to"""


def load_config(argv: Sequence[str] | None = None) -> Config:
    """
    Resolve the configuration from `argv`, using the config file as a default

    Raises:
        ConfigError: A setting or the config file could not be read.
    """
    args = _parser().parse_args(argv)
    has_config_file_override = args.config is not None
    settings = _Settings(
        args,
        _read_file(
            Path(args.config) if has_config_file_override else DEFAULT_CONFIG_FILE, required=has_config_file_override
        ),
    )
    return Config(
        allowed_identities=settings.identity_list("allowed_identities", []),
        announce_period=settings.number("announce_period", 900.0),
        disable_auth=settings.boolean("disable_auth", False),
        identity_name=settings.string("identity_name", APP_NAME),
        rns_config_path=settings.path("rns_config_path", None),
        storage_path=settings.path("storage_path", Path("storage")),
    )


def _parser() -> argparse.ArgumentParser:
    """The command line, one option per `Config` field plus `--config`"""
    parser = argparse.ArgumentParser(prog="server", description="An example rnmmp mailbox server")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"the config file to read settings from (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--allowed-identities",
        metavar="HASHES",
        help="Comma-seperated list of statically-allowed hex-encoded identity hashes (default: none)",
    )
    parser.add_argument(
        "--announce-period",
        metavar="SECONDS",
        help="The time between announces; <= 0 only does the initial announce (default: 900.0)",
    )
    parser.add_argument(
        "--disable-auth",
        action="store_true",
        help="Whether to disable authorization and allow access from all nodes (default: false)",
    )
    parser.add_argument(
        "--identity-name", metavar="NAME", help="The identity to load and use for the mailbox (default: rnmmp)"
    )
    parser.add_argument(
        "--rns-config-path",
        metavar="PATH",
        help="the directory Reticulum reads its own config from (default: Reticulum's own)",
    )
    parser.add_argument(
        "--storage-path",
        metavar="PATH",
        help="the directory to write messages and mailbox state to (default: storage/)",
    )
    return parser


def _read_file(path: Path, required: bool) -> ConfigParser:
    """Read `path` config from path"""
    parser = ConfigParser()
    if not path.exists():
        if required:
            raise ConfigError(f"config file {path} does not exist")
        return parser
    try:
        parser.read(path)
    except (OSError, configparser.Error) as error:
        raise ConfigError(f"config file {path} could not be read: {error}") from error
    return parser


class _Settings:
    """Loader for settings from either `args` or `file`"""

    def __init__(self, args: argparse.Namespace, file: ConfigParser) -> None:
        """Resolve settings out of parsed arguments and/or a config file"""
        self._args = args
        self._file = file

    def string[T](self, name: str, default: T) -> str | T:
        """The setting as written, stripped of surrounding whitespace"""
        raw = self._raw(name)
        return default if raw is None else raw

    def path[T](self, name: str, default: T) -> Path | T:
        """The setting as a path, with a leading `~` expanded"""
        raw = self._raw(name)
        return default if raw is None else Path(raw).expanduser()

    def integer[T](self, name: str, default: T) -> int | T:
        """The setting as an integer"""
        raw = self._raw(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a whole number, not {raw!r}") from None

    def number[T](self, name: str, default: T) -> float | T:
        """The setting as a float"""
        raw = self._raw(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a number, not {raw!r}") from None

    def boolean[T](self, name: str, default: T) -> bool | T:
        """The setting as a flag, written as any of `1/0`, `true/false`, `yes/no` or `on/off`"""
        raw = self._raw(name)
        if raw is None:
            return default
        folded = raw.casefold()
        if folded in TRUE_VALUES:
            return True
        if folded in FALSE_VALUES:
            return False
        raise ConfigError(f"{name} must be true or false, not {raw!r}")

    def identity_list[T](self, name: str, default: T) -> list[bytes] | T:
        """A list of identity hashes, loaded from comma-seperated hex-encoded hashes"""
        raw = self._raw(name)
        if raw is None:
            return default

        identity_hexes = raw.split(",")
        identities: list[bytes] = []

        hex_length = RNS.Identity.TRUNCATED_HASHLENGTH // 8 * 2
        for identity_hex in identity_hexes:
            identity_hex_trimmed = identity_hex.strip()
            if len(identity_hex_trimmed) != hex_length:
                raise ConfigError(f"Each item of {name} must be {hex_length} characters long")
            try:
                allowed_hash = bytes.fromhex(identity_hex_trimmed)
                identities.append(allowed_hash)
            except Exception as e:
                raise ConfigError(f"Each item of {name} must be provided in hexadecimal") from e

        return identities

    def _raw(self, name: str) -> str | None:
        """The value set for `name`"""
        from_args = getattr(self._args, name, None)
        if from_args is not None:
            return str(from_args).strip()
        from_file = self._file.get(SECTION, name, fallback=None)
        return None if from_file is None else from_file.strip()
