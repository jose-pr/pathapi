"""What the helpers that walk, match and sync need from a pure path."""

from __future__ import annotations

import typing as _ty

__all__ = ["Named"]


class Named(_ty.Protocol):
    """The pure half of a path as `glob`, `walk` and `sync` read it: a name,
    the segments it is made of, a parent, and the means of building and
    joining paths of the same kind. Structural only: `Pathname` and
    `pathlib.PurePath` satisfy it without naming it, and nothing subclasses it."""

    @property
    def name(self) -> str: ...

    @property
    def segments(self) -> _ty.Sequence[str]: ...

    @property
    def parent(self) -> _ty.Self: ...

    def with_segments(self, *segments: str) -> _ty.Self: ...

    def joinpath(self, *args: "str | _ty.Self") -> _ty.Self: ...

    def __truediv__(self, key: "str | _ty.Self") -> _ty.Self: ...

    def as_posix(self) -> str: ...
