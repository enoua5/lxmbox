"""Smoke tests for the lxmbox_qt package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_qt

    assert lxmbox_qt.__version__
