"""Stable identifier helpers used by the ingest pipeline.

Two primitives:

- :func:`sha256_file` — streaming SHA-256 hexdigest (used as dedup key in
  ``hara_ingested_files``). Streams in 64 KiB blocks to avoid loading large
  files into memory.
- :func:`slugify_relative_path` — converts a relative ``Path`` into a
  filesystem-safe identifier. Subfolders become parts of the slug separated
  by ``_`` so ``2023/dados.csv`` and ``2024/dados.csv`` end up distinct.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from slugify import slugify

_HASH_CHUNK_BYTES = 64 * 1024


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hexdigest of ``path``'s raw bytes.

    Streams the file in fixed-size blocks so the entire content is never
    held in memory at once — ingestable PDFs can be tens of MB.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(_HASH_CHUNK_BYTES)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def slugify_relative_path(relative_path: Path) -> str:
    """Convert a relative path into a deterministic, filesystem-safe slug.

    Strategy:
        1. Drop the file extension (``producao_2024.csv`` → ``producao_2024``).
        2. Replace OS path separators with ``_`` so subfolders contribute to
           the identifier (``2024/dados`` → ``2024_dados``). This means
           ``2023/dados.csv`` and ``2024/dados.csv`` produce distinct slugs,
           avoiding silent collisions.
        3. Pass each segment through python-slugify (which lowercases,
           strips accents, replaces non-word chars with ``-``).

    Note that slug **separators within a segment** stay as ``-`` (slugify's
    default) while **subfolder separators** are ``_``. Mixing the two is
    intentional: it lets a reader visually distinguish the two layers.
    """
    parts = relative_path.with_suffix("").parts
    # Custom regex keeps existing ``_`` inside a segment intact (so
    # ``regiao_norte`` stays ``regiao_norte``); other non-word characters
    # — including spaces and accented forms after transliteration — collapse
    # into the default ``-`` separator.
    slugged = [slugify(p, regex_pattern=r"[^-a-z0-9_]+") for p in parts]
    return "_".join(s for s in slugged if s)
