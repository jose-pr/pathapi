from __future__ import annotations

import typing as _ty

from .path import Path, Pathname

__all__ = ["TreePath"]


class TreePath(Path):
    """A `Path` whose names are POSIX segments below the root of a tree that a
    backend serves (a remote directory, the members of an archive).

    Joined and normalized like `PurePosixPath`: empty and "." segments
    collapse and an absolute argument restarts the join. Relative and rooted
    spellings name the same node, because the tree has no working directory.
    A subclass supplies the constructor, `_rebuild()` and the backend it
    carries; this class holds the names only."""

    __slots__ = ("_segments",)

    @classmethod
    def _family(cls) -> type:
        """The class directly under `TreePath` that `cls` derives from: its
        instances can donate a backend to a join."""
        return next(c for c in cls.__mro__ if TreePath in c.__bases__)

    @classmethod
    def _join(cls, segments: "_ty.Iterable[object]") -> "tuple[str, TreePath | None]":
        """`(text, donor)`: the joined text of `segments` and the first of them
        that belongs to this class's family, whose backend the result keeps.
        `NotImplementedError` for another `Path` (so `/` answers
        `NotImplemented`), `TypeError` for what is not a path at all."""
        path = ""
        donor = None
        family = cls._family()
        for segment in segments:
            if isinstance(segment, family):
                text = segment.as_posix()
                if donor is None:
                    donor = segment
            elif isinstance(segment, Path):
                raise NotImplementedError()
            elif isinstance(segment, Pathname):
                text = "/".join(segment.segments)
            elif isinstance(segment, str):
                text = segment
            else:
                raise TypeError(
                    "argument should be a str or a Pathname, "
                    f"not {type(segment).__name__!r}"
                )
            if text.startswith("/") or not path:
                path = text
            elif text:
                path = f"{path}/{text}"
        return path, donor

    @staticmethod
    def _parse(path: str) -> tuple:
        """Segments of a joined path string: `("", "")` for the root,
        `("", name, ...)` for another rooted path, `(name, ...)` for a
        relative one and `()` for the empty path."""
        names = [name for name in path.split("/") if name and name != "."]
        if path.startswith("/"):
            return ("", *names) if names else ("", "")
        return tuple(names)

    def _rebuild(self, text: str) -> _ty.Self:
        """A path of this class and backend for the joined text `text`."""
        raise NotImplementedError

    def __str__(self) -> str:
        return self.as_posix()

    def __copy__(self):
        return self._rebuild(self.as_posix())

    def __deepcopy__(self, memo):
        # A backend is a connection or a handle, not part of the value.
        inst = memo[id(self)] = self.__copy__()
        return inst

    @property
    def segments(self):
        return self._segments

    @property
    def parts(self):
        return self.segments, self._backend

    @property
    def parent(self):
        segments = self.segments
        if not segments or segments == ("", ""):
            return self
        if len(segments) == 2 and segments[0] == "":
            return self._rebuild("/")
        return self._rebuild("/".join(segments[:-1]))

    def with_segments(self, *segments: str):
        return self._rebuild("/".join(segments))

    def relative_to(self, other, *, walk_up: bool = False):
        """`self` below `other`, as a relative path on the same backend.

        `other` is a path of this class on the same tree or a `str`. Names are
        compared as written, with no `..` applied. `ValueError` when `self`
        is not below `other`, when the two differ in being rooted, or when
        the trees differ. With `walk_up`, a path beside `other` gets a `..`
        per level climbed; a `..` in `other` cannot be walked."""
        if isinstance(other, self._family()):
            if not self._same_filesystem(other):
                raise ValueError(
                    f"{str(self)!r} and {str(other)!r} are not in the same tree"
                )
        else:
            other = self.with_segments(other)
        rooted = self.segments[:1] == ("",)
        other_rooted = other.segments[:1] == ("",)
        names = [name for name in self.segments if name]
        other_names = [name for name in other.segments if name]
        for step in range(len(other_names) + 1):
            keep = len(other_names) - step
            if rooted == other_rooted and names[:keep] == other_names[:keep]:
                return self.with_segments(*[".."] * step, *names[keep:])
            if not walk_up:
                raise ValueError(
                    f"{str(self)!r} is not in the subpath of {str(other)!r}"
                )
            if keep and other_names[keep - 1] == "..":
                raise ValueError(f"'..' segment in {str(other)!r} cannot be walked")
        raise ValueError(f"{str(self)!r} and {str(other)!r} have different anchors")
