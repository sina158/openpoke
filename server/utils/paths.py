"""Where OpenPoke keeps its runtime files."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    """Return the runtime data directory.

    Defaults to ``server/data``; ``OPENPOKE_DATA_DIR`` moves it (tests and evals point it at a temp dir).
    """
    root = Path(os.environ.get("OPENPOKE_DATA_DIR") or _DEFAULT_DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root
