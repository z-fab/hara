"""CSV -> Polars DataFrame parser used by the structured ingest pipeline.

We try UTF-8 first, then fall back to Latin-1 (CP-1252's parent) - most
real-world Brazilian datasets are UTF-8 these days, but legacy exports
from Excel/SAS/SPSS still arrive Latin-1 encoded.
"""

from __future__ import annotations

import csv as _stdlib_csv
from pathlib import Path

import polars as pl

from hara.utils.exceptions import IngestError

_ENCODINGS = ("utf8", "latin-1")


def _validate_field_counts(path: Path, encoding: str) -> None:
    """Walk the CSV with the stdlib reader and ensure every row has the same
    number of fields as the header. Polars (as of 1.x) silently fills ragged
    rows with nulls when ``truncate_ragged_lines=False``; we want a hard fail
    so the ingest pipeline surfaces malformed data.
    """
    py_encoding = "utf-8" if encoding == "utf8" else encoding
    with path.open("r", encoding=py_encoding, newline="") as fh:
        reader = _stdlib_csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return
        expected = len(header)
        for line_no, row in enumerate(reader, start=2):
            # Fully empty trailing line is fine; everything else must match.
            if not row:
                continue
            if len(row) != expected:
                raise IngestError(
                    f"malformed CSV {path}: line {line_no} has "
                    f"{len(row)} fields, expected {expected}"
                )


def parse_csv(path: Path) -> tuple[pl.DataFrame, int]:
    """Return ``(dataframe, row_count)``.

    Raises :class:`IngestError` when no encoding round-trip succeeds, the
    file is empty, or the parser hits a structural error (e.g., row with a
    different number of fields).
    """
    last_exc: Exception | None = None
    for enc in _ENCODINGS:
        try:
            df = pl.read_csv(
                path,
                encoding=enc,
                infer_schema_length=10_000,
                ignore_errors=False,
                # truncate_ragged_lines stays False so a malformed row raises
                # rather than silently dropping data.
                truncate_ragged_lines=False,
            )
        except UnicodeDecodeError as e:
            last_exc = e
            continue
        except Exception as e:
            # Polars surfaces encoding failures as ComputeError("invalid utf-8 ..."),
            # so we sniff the message to decide between "try next encoding" and
            # "this CSV is structurally broken".
            msg = str(e).lower()
            if "utf-8" in msg or "utf8" in msg:
                last_exc = e
                continue
            raise IngestError(f"failed to parse CSV {path}: {e}") from e

        if df.height == 0 and len(df.columns) == 0:
            raise IngestError(f"empty CSV: {path}")

        # Polars accepts ragged rows by padding with nulls; double-check.
        _validate_field_counts(path, enc)
        return df, df.height

    raise IngestError(f"failed to parse CSV {path}: {last_exc}") from last_exc
