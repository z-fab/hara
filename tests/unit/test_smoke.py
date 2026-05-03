"""Smoke test: package imports cleanly."""

from __future__ import annotations


def test_package_importable() -> None:
    import hara  # noqa: PLC0415

    assert hara is not None


def test_version_string_present() -> None:
    from hara import __version__  # noqa: PLC0415

    assert isinstance(__version__, str)
    assert __version__.startswith("0.1.0")
