"""Tests for hash + slug helpers used across the ingest pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.utils.identifiers import sha256_file, slugify_relative_path


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "data.csv"
    p.write_bytes(b"a,b,c\n1,2,3\n")
    return p


def test_sha256_file_is_stable(sample_file: Path) -> None:
    h1 = sha256_file(sample_file)
    h2 = sha256_file(sample_file)
    assert h1 == h2
    assert len(h1) == 64  # hexdigest SHA-256


def test_sha256_file_changes_with_content(tmp_path: Path) -> None:
    p1 = tmp_path / "a.bin"
    p2 = tmp_path / "b.bin"
    p1.write_bytes(b"hello")
    p2.write_bytes(b"world")
    assert sha256_file(p1) != sha256_file(p2)


def test_slug_drops_extension_and_lowercases() -> None:
    assert slugify_relative_path(Path("DADOS.CSV")) == "dados"


def test_slug_preserves_subfolder_using_underscore() -> None:
    assert slugify_relative_path(Path("2024/dados.csv")) == "2024_dados"


def test_slug_handles_accents_and_spaces() -> None:
    assert slugify_relative_path(Path("Produção 2024.csv")) == "producao-2024"


def test_slug_handles_nested_subfolders() -> None:
    assert slugify_relative_path(Path("regiao_norte/2023/dados.csv")) == "regiao_norte_2023_dados"


def test_slug_collisions_avoided_when_subfolder_differs() -> None:
    a = slugify_relative_path(Path("2023/dados.csv"))
    b = slugify_relative_path(Path("2024/dados.csv"))
    assert a != b
