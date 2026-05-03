"""Walk a path and classify each file by extension into a target connector.

The scanner is intentionally pure (no I/O beyond ``Path.is_file``/glob): it
returns a list of :class:`ScannedFile` records that downstream pipelines
consume. Hidden files (``.foo``) are skipped silently.

Classification rules (§9 of the spec):

- ``.csv`` -> ``target="sql"``
- ``.pdf``, ``.docx``, ``.md``, ``.txt`` -> ``target="vector"``
- everything else -> ``target="ignored"`` with a reason; if ``strict`` is
  set, an :class:`IngestError` is raised instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hara.utils.exceptions import IngestError

Target = Literal["sql", "vector", "ignored"]

_SQL_EXTENSIONS = {".csv"}
_VECTOR_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}

IGNORED_REASONS = {
    "unknown_extension": "extension not recognised",
    "hidden": "hidden file",
}


@dataclass(frozen=True)
class ScannedFile:
    """A file the scanner found, with its dispatch decision pre-computed."""

    absolute_path: Path
    relative_path: str  # POSIX-style, relative to the --from root
    target: Target
    ignore_reason: str | None = None


def scan(root: Path, *, strict: bool = False) -> list[ScannedFile]:
    """Return every relevant file under ``root``.

    ``root`` may be a single file or a directory. Results are sorted by
    ``relative_path`` so callers (e.g., the Rich summary) see a stable
    order regardless of the underlying filesystem.

    Raises :class:`IngestError` if ``root`` does not exist, or if ``strict``
    is true and any unknown-extension file is encountered.
    """
    root = root.resolve()
    if not root.exists():
        raise IngestError(f"path does not exist: {root}")

    if root.is_file():
        base = root.parent
        candidates = [root]
    else:
        base = root
        candidates = [p for p in root.rglob("*") if p.is_file()]

    results: list[ScannedFile] = []
    for path in candidates:
        rel_parts = path.relative_to(base).parts
        rel = path.relative_to(base).as_posix()

        # Hidden files (any path component starting with '.') are ignored
        # silently — common case is editor lockfiles like .DS_Store / ~$file.
        if any(part.startswith(".") for part in rel_parts):
            continue

        ext = path.suffix.lower()
        if ext in _SQL_EXTENSIONS:
            results.append(ScannedFile(absolute_path=path, relative_path=rel, target="sql"))
        elif ext in _VECTOR_EXTENSIONS:
            results.append(ScannedFile(absolute_path=path, relative_path=rel, target="vector"))
        else:
            if strict:
                raise IngestError(f"strict mode: unsupported file {rel!r} (extension {ext!r})")
            results.append(
                ScannedFile(
                    absolute_path=path,
                    relative_path=rel,
                    target="ignored",
                    ignore_reason=IGNORED_REASONS["unknown_extension"],
                )
            )

    results.sort(key=lambda s: s.relative_path)
    return results
