"""Smoke tests for the rnmmp_server package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import rnmmp_server

    assert rnmmp_server.__version__
