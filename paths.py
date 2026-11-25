"""Portable path helpers (Parallels Mac share, USB copy, etc.)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def prefer_local_drive_path(path: Path | str) -> Path:
    """Prefer ``C:\\Mac\\Home\\...`` when ``resolve()`` yields ``\\\\Mac\\Home\\...``."""
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    text = str(resolved)
    if text.startswith('\\\\Mac\\Home\\'):
        alt = Path('C:' + text[4:])
        if alt.exists():
            return alt
    return resolved
