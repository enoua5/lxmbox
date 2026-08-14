"""Smoke tests for the lxmbox_mgmt package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_mgmt

    assert lxmbox_mgmt.__version__
