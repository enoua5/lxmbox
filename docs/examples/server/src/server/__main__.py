"""Main entry point for example server"""

import asyncio
import os

import RNS

from rnmmp_server import APP_NAME, MailboxModel, MailboxService

from .auth import ReticulumAuth, get_allowed_identity_path
from .config import Config, load_config
from .file_store import FileStore
from .lxmf_ingest import LxmfIngest

rns_has_init = False


def _init_rns(config: Config) -> None:
    """Initialize RNS if it hasn't already been"""
    global rns_has_init
    if rns_has_init:
        return

    # Constructing the class runs stack initialization
    rns_config_path_str = None if config.rns_config_path is None else str(config.rns_config_path)
    RNS.Reticulum(configdir=rns_config_path_str)
    rns_has_init = True


def _init_mailbox(config: Config) -> MailboxModel:
    """Init the mailbox model"""
    store = FileStore(config)
    return MailboxModel(store)


def _init_identity(config: Config) -> RNS.Identity:
    """Init RNS and return the server identity"""

    _init_rns(config)

    identity_path = RNS.Reticulum.identitypath + "/" + APP_NAME

    identity = None
    if os.path.isfile(identity_path):
        identity = RNS.Identity.from_file(identity_path)
        if identity is None:
            RNS.log(
                (
                    "Could not load identity for rnmmp."
                    + f' The identity file at "{identity_path}" may be corrupt or unreadable.'
                ),
                RNS.LOG_ERROR,
            )
            RNS.exit(2)

    if identity is None:
        RNS.log("No valid saved identity found, creating new...", RNS.LOG_INFO)
        identity = RNS.Identity()
        identity.to_file(identity_path)

    return identity


async def main() -> None:
    """Main asyncio entry point"""

    config = load_config()
    mailbox = _init_mailbox(config)
    identity = _init_identity(config)

    auth_checker = ReticulumAuth(
        auth_disabled=config.disable_auth,
        allowed_identity_hashes=config.allowed_identities,
        identity_allowed_file=get_allowed_identity_path(),
    )

    service = MailboxService(
        mailbox,
        identity,
        auth_checker.is_authorized,
    )

    lxmf_router = LxmfIngest(config, identity, mailbox)

    do_periodic_announce = config.announce_period > 0

    try:
        service.announce()
        lxmf_router.announce()

        while True:
            if do_periodic_announce:
                service.announce()
                lxmf_router.announce()
            await asyncio.sleep(config.announce_period if config.announce_period > 0 else 60)
    finally:
        service.close()


asyncio.run(main())
