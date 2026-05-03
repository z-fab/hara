"""PDF parser via Docling.

Docling exports to Markdown so heading structure (Stage 1 of the chunker)
is preserved. Optional — installed via the ``docling`` extra; the import
is deferred so ``import hara`` works without the extra.
"""

from __future__ import annotations

from pathlib import Path

from hara.utils.exceptions import IngestError


def parse_pdf(path: Path) -> tuple[str, int]:
    """Convert ``path`` to Markdown using Docling, returning ``(text, char_count)``."""
    try:
        from docling.document_converter import DocumentConverter  # noqa: PLC0415
    except ImportError as e:
        raise IngestError("docling extra not installed; run `pip install hara[docling]`") from e

    if not path.exists():
        raise IngestError(f"PDF not found: {path}")

    converter = DocumentConverter()
    try:
        result = converter.convert(str(path))
        text = result.document.export_to_markdown()
    except Exception as e:
        raise IngestError(f"failed to parse PDF {path}: {e}") from e

    return text, len(text)
