"""`Path.info`: the file type of one path from at most two stats."""

from __future__ import annotations

import stat as _stat
import typing as _ty

if _ty.TYPE_CHECKING:
    from ..path import Path


class _PathInfo:
    """`Path.info`: the file type of one path, from at most one stat that
    follows links and one that does not, both kept on this object only."""

    __slots__ = ("_path", "_followed", "_unfollowed")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._followed = self._unfollowed = _UNSET

    def _stat(self, follow_symlinks: bool):
        """The stat, or None where there is no such entry."""
        if follow_symlinks:
            if self._followed is _UNSET:
                # An entry that is missing or is not a link is the same stat
                # either way.
                plain = self._unfollowed
                if plain is _UNSET or (
                    plain is not None and _stat.S_ISLNK(plain.st_mode)
                ):
                    self._followed = self._ask(True)
                else:
                    self._followed = plain
            return self._followed
        if self._unfollowed is _UNSET:
            self._unfollowed = self._ask(False)
        return self._unfollowed

    def _ask(self, follow_symlinks: bool):
        try:
            return self._path.stat(follow_symlinks=follow_symlinks)
        except (OSError, ValueError):
            return None

    def exists(self, *, follow_symlinks: bool = True) -> bool:
        return self._stat(follow_symlinks) is not None

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        stat = self._stat(follow_symlinks)
        return stat is not None and _stat.S_ISDIR(stat.st_mode)

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        stat = self._stat(follow_symlinks)
        return stat is not None and _stat.S_ISREG(stat.st_mode)

    def is_symlink(self) -> bool:
        stat = self._stat(False)
        return stat is not None and _stat.S_ISLNK(stat.st_mode)

    def __repr__(self) -> str:
        return f"<{type(self._path).__name__}.info>"


_UNSET = object()
