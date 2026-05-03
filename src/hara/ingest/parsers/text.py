"""Direct text parser for .md/.txt sources (no Docling needed)."""

from __future__ import annotations

from pathlib import Path

from hara.utils.exceptions import IngestError

_ENCODINGS = ("utf-8", "latin-1")


def parse_text(path: Path) -> tuple[str, int]:
    if not path.exists():
        raise IngestError(f"text file not found: {path}")
    last_exc: Exception | None = None
    for enc in _ENCODINGS:
        try:
            text = path.read_text(encoding=enc)
            return text, len(text)
        except UnicodeDecodeError as e:
            last_exc = e
            continue
    raise IngestError(f"failed to decode {path}: {last_exc}") from last_exc
