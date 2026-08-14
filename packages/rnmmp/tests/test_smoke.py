"""Smoke tests for the rnmmp package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import rnmmp

    assert rnmmp.__version__
