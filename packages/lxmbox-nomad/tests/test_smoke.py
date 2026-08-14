"""Smoke tests for the lxmbox_nomad package."""


def test_package_imports() -> None:
    """The package imports and exposes a version string."""
    import lxmbox_nomad

    assert lxmbox_nomad.__version__
