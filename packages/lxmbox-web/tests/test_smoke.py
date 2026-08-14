"""Smoke tests for the lxmbox_web package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_web

    assert lxmbox_web.__version__
