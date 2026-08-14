"""Smoke tests for the lxmbox_cli package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_cli

    assert lxmbox_cli.__version__
