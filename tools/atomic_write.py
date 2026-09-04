"""Symlink-safe temp-file write, below the command layer.

`tools/policy_compile.py` and `tools/bom_cli.py` both write artifacts through
this; it lives on its own so a module below the command layer does not have to
import a command module to reach it.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["write_new_temp_file"]


def write_new_temp_file(directory: Path, content: str) -> Path:
    """Write `content` to a fresh file in `directory` and return its path.

    A predictable `.tmp` name plus `write_text` still follows a symlink an
    attacker pre-planted at that exact name — `write_text` opens (and follows)
    whatever is already there before this function's own `Path.replace` ever
    runs, so the atomic-replace step arrives too late to help.
    `tempfile.mkstemp` opens with `O_CREAT | O_EXCL` on an unpredictable
    name, so it fails on any existing path entry (including a symlink)
    instead of opening through it."""
    fd, name = tempfile.mkstemp(dir=directory, suffix=".tmp")
    temp_path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path
