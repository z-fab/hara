"""FastAPI app for HARA.

Optional extra: ``pip install hara[api]``. Core HARA does not import this
package — `hara chat` and the rest of the CLI work without FastAPI installed.

Public API:

- :func:`create_app` — build a FastAPI app from a :class:`Settings`.

Spec §6.
"""

from __future__ import annotations

from hara.api.app import create_app

__all__ = ["create_app"]
