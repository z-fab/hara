"""Verify `python -m hara` invokes the CLI."""

from __future__ import annotations

import subprocess
import sys

from hara import __version__


def test_python_m_hara_help_runs() -> None:
    """`python -m hara --help` must succeed and mention the project tagline."""
    result = subprocess.run(
        [sys.executable, "-m", "hara", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Hybrid Agent" in result.stdout


def test_python_m_hara_version_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "hara", "version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
