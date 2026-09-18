"""Smoke tests for the rnmmp_client package"""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import rnmmp_client

    assert rnmmp_client.__version__
