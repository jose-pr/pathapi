"""Filename globbing over any pathlib_next `Path`.

Modelled on CPython's pathlib glob selectors, reworked to run over anything
that implements `_scandir()`/`iterdir()`/`is_dir()`/`name` like
`pathlib.Path` (`LocalPath`, `MemPath`, `FTPPath`, the archive paths).
"""

from __future__ import annotations

from ._patterns import (
    ANY_PATTERN,
    RECURSIVE,
    WILCARD_PATTERN,
    WILDCARD_PATTERN,
    compile_pattern,
    full_match,
    parse_pattern,
)
from ._selection import glob, select

__all__ = [
    "RECURSIVE",
    "full_match",
    "glob",
    "parse_pattern",
    "select",
]
