"""Smoke tests for the lxmbox_popr package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_popr

    assert lxmbox_popr.__version__
