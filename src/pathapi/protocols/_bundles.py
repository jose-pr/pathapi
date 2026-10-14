"""What a helper reads from a path, as one type: a pure path plus the
protocols the helper calls. Used in annotations."""

from __future__ import annotations

import typing as _ty

from .io import BinaryOpen
from .pure import Named
from .tree import Listing


class Walkable(Named, Listing, _ty.Protocol):
    """A path that can be listed and stat'ed: what `glob` and `walk` read."""

    __slots__ = ()


class Readable(Walkable, BinaryOpen, _ty.Protocol):
    """A path whose files can be listed and read: a `PathSyncer` source."""

    __slots__ = ()
