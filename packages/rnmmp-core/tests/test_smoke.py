"""Smoke tests for the rnmmp_core package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import rnmmp_core

    assert rnmmp_core.__version__
