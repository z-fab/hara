"""Two-stage chunking used by the unstructured ingest pipeline.

Stage 1 (semantic split): Markdown ATX headings (``#``, ``##``, ...) act as
chapter boundaries. Each chunk inherits the cumulative heading path so the
Planner can reason about where in the document it came from.

Stage 2 (size split): if a heading-bounded chunk is still longer than
``chunk_size``, it is sliced into windows of ``chunk_size`` chars with
``chunk_overlap`` chars of overlap (sliding window). Overlap helps the
embedder/retriever keep context across boundaries.
"""

from __future__ import annotations

import re

from hara.connectors.vector.base import TextChunk

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def chunk_text(
    text: str,
    *,
    file_id: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
) -> list[TextChunk]:
    """Split ``text`` into a list of :class:`TextChunk` records.

    Each chunk carries:
        - ``file_id``: passed through unchanged.
        - ``content``: the chunk's text.
        - ``metadata.section``: ``" > "``-joined heading path (outer to
          inner). Empty string when the document has no markdown headings
          — Chroma rejects empty list metadata, so we encode "no section"
          as ``""`` rather than ``[]``. Aligns with the langchain
          ``MarkdownHeaderTextSplitter`` style.
        - ``metadata.chunk_index``: position within the file.
    """
    if not text.strip():
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    sections = _split_by_headings(text)

    chunks: list[TextChunk] = []
    idx = 0
    for heading_path, body in sections:
        section = " > ".join(heading_path) if heading_path else ""
        for piece in _split_by_size(body, chunk_size, chunk_overlap):
            chunks.append(
                TextChunk(
                    file_id=file_id,
                    content=piece,
                    metadata={
                        "section": section,
                        "chunk_index": idx,
                    },
                )
            )
            idx += 1
    return chunks


def _split_by_headings(text: str) -> list[tuple[tuple[str, ...], str]]:
    """Walk the text and return ``[(heading_path, body), ...]``.

    Headings are tracked as a stack: a level-N heading replaces everything
    at level N or deeper. The body associated with each heading is the
    text from that heading up to the next heading.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [((), text)]

    sections: list[tuple[tuple[str, ...], str]] = []
    stack: list[tuple[int, str]] = []  # (level, title)

    # Pre-text (before the first heading) is its own no-heading section.
    pre = text[: matches[0].start()].strip()
    if pre:
        sections.append(((), pre))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        # Pop deeper-or-equal entries
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = tuple(t for _, t in stack)

        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            sections.append((path, body))
    return sections


def _split_by_size(body: str, size: int, overlap: int) -> list[str]:
    if len(body) <= size:
        return [body]
    chunks: list[str] = []
    start = 0
    step = size - overlap
    while start < len(body):
        end = min(start + size, len(body))
        chunks.append(body[start:end])
        if end == len(body):
            break
        start += step
    return chunks
