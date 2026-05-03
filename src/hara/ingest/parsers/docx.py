"""DOCX parser via Docling. Same dependency as PDF; separate module by convention."""

from __future__ import annotations

from pathlib import Path

from hara.utils.exceptions import IngestError


def parse_docx(path: Path) -> tuple[str, int]:
    try:
        from docling.document_converter import DocumentConverter  # noqa: PLC0415
    except ImportError as e:
        raise IngestError("docling extra not installed; run `pip install hara[docling]`") from e

    if not path.exists():
        raise IngestError(f"DOCX not found: {path}")

    converter = DocumentConverter()
    try:
        result = converter.convert(str(path))
        text = result.document.export_to_markdown()
    except Exception as e:
        raise IngestError(f"failed to parse DOCX {path}: {e}") from e

    return text, len(text)
