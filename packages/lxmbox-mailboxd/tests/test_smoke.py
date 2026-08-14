"""Smoke tests for the lxmbox_mailboxd package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_mailboxd

    assert lxmbox_mailboxd.__version__
