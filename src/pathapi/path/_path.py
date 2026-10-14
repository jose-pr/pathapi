"""`Path`: a pathname with I/O, built from the protocols."""

from __future__ import annotations

import pathlib as _pathlib
import sys as _sys
import types as _types
import typing as _ty

from ..protocols import (
    BinaryOpen,
    Chmod,
    Chown,
    Copy,
    Create,
    Hardlink,
    Identity,
    Listing,
    Move,
    Owned,
    Readlink,
    Remove,
    Resolve,
    Stat,
    Symlink,
    Utime,
)
from ._pure import Pathname

# Operations that must come from pathapi even when a concrete `pathlib`
# class precedes it in a subclass's MRO; `Path.__init_subclass__` applies
# them and says why.
_OPERATION_NAMES = (
    "copy",
    "move",
    "copy_into",
    "move_into",
    "exists",
    "rglob",
    "read_text",
    "write_text",
    "symlink_to",
)


def _local_primitives() -> "dict[str, _ty.Callable]":
    """The functions `LocalPath` defines, by name: what a class that mixes a
    concrete stdlib path with `Path` but not `LocalPath` is given for its
    local primitives (`stat`, `chmod`, `is_junction`, `is_mount`, `glob`,
    `walk`, `_scandir`, `_symlink_to`, `_chown`, ...). Taken from the class
    itself so the list cannot fall behind it. Empty while `LocalPath` is
    still being created."""
    module = _sys.modules.get(__name__.partition(".")[0] + ".fspath")
    local = getattr(module, "LocalPath", None)
    if local is None:
        return {}
    return {
        name: value
        for name, value in vars(local).items()
        if isinstance(value, _types.FunctionType)
        and not (name.startswith("__") and name.endswith("__"))
    }


def _inherits_foreign_default(cls: type, name: str) -> bool:
    """Whether `cls` gets `name` from stdlib `pathlib` or from this
    library's own generic defaults (`Path`, `Pathname`, the protocols)
    rather than from its own body or a mixin of its own."""
    if name in vars(cls):
        return False
    owner = next((base for base in cls.__mro__[1:] if name in vars(base)), None)
    if owner is None:
        return True
    module = getattr(owner, "__module__", "") or ""
    package = __name__.partition(".")[0]
    return (
        module == "pathlib"
        or module.startswith("pathlib.")
        or module == package + ".path"
        or module.startswith(package + ".path.")
        or module == package + ".protocols"
        or module.startswith(package + ".protocols.")
    )


class Path(
    Pathname,
    Move,
    Copy,
    Identity,
    Create,
    Symlink,
    Remove,
    Listing,
    Resolve,
    Readlink,
    Hardlink,
    Owned,
    Stat,
    Chmod,
    Chown,
    Utime,
    BinaryOpen,
):
    """Base class for manipulating paths with I/O."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs):
        """Guarantee pathapi operation precedence in every subclass.

        Concrete path classes are routinely built by mixing a `pathlib`
        class with this one -- our own `LocalPath` does it, and the
        documented downstream recipe (`class X(PosixPathname, Path)`) does
        it transitively. Python's MRO then resolves a name to whichever
        base declares it first, which for those classes can be `pathlib`
        rather than `pathapi` -- and which one wins changes with the
        interpreter version, because stdlib `pathlib` keeps gaining and
        changing methods (see `_OPERATION_NAMES`).

        Rather than making every downstream implementer rediscover this and
        hand-write forwarding methods, re-assert our implementations here
        for any subclass that would otherwise inherit a non-pathapi
        one. A subclass (or an intermediate mixin) that defines the method
        *itself* is always left alone -- this only displaces implementations
        coming from outside this library.

        `_OPERATION_NAMES` lists what is re-asserted, for two opposite
        reasons. Newer stdlib overrides ours: 3.14's `copy` and `move` expect
        the private `_copy_from` protocol, so such a class either raises
        `AttributeError` on non-local backends or silently succeeds on local
        ones with stdlib's metadata semantics (timestamps preserved, where
        ours preserves `st_mode`), so mtime-based syncs converge on 3.14 and
        not before; 3.14's `copy_into` and `move_into` call `copy()` and
        `move()` with stdlib's keywords and defaults. Older stdlib lacks our
        keywords: `exists(follow_symlinks=)` (3.12+), `read_text` and
        `write_text(newline=)` (3.13+), `rglob`'s extensions, and
        `symlink_to(force=)`, which no stdlib has. `replace` is not listed:
        `LocalPath` keeps stdlib's `os.replace`, which `move()` uses to swap a
        local file atomically.

        A class that mixes a concrete stdlib path with `Path` but not
        `LocalPath` is a local class too, and gets what `LocalPath` has for
        each name it defines (`_local_primitives`) wherever stdlib or a
        generic default would otherwise answer: the generic `is_junction()`
        and `is_mount()` say False, so `rm(recursive=True)` would walk into a
        junction, and stdlib's `stat()`, `chmod()` and `_scandir()` do not
        take pathapi's arguments.
        """
        super().__init_subclass__(**kwargs)
        primitives = _local_primitives() if issubclass(cls, _pathlib.Path) else {}
        if primitives:
            from ..fspath import _BaseFSPathname

            for name, ours in primitives.items():
                if name == "glob" and not issubclass(cls, _BaseFSPathname):
                    # LocalPath.glob needs the flavour helpers of
                    # _BaseFSPathname.
                    ours = Path.glob
                if _inherits_foreign_default(cls, name):
                    setattr(cls, name, ours)
        for name in _OPERATION_NAMES:
            # A class that defines the operation in its OWN body is always
            # authoritative -- never displace a deliberate override (this
            # also covers `LocalPath.copy`/`move`'s explicit routing).
            if name in vars(cls):
                continue
            # The owner is the base that supplies the implementation, not the module of
            # the resolved function: a downstream mixin may define it in its own module.
            owner = next((base for base in cls.__mro__[1:] if name in vars(base)), None)
            if owner is None:
                continue
            # Only stdlib `pathlib` is displaced; a mixin, a user base or pathapi
            # itself is deliberate and stays, which keeps this off third-party code.
            owner_module = getattr(owner, "__module__", "") or ""
            if owner_module != "pathlib" and not owner_module.startswith("pathlib."):
                continue
            ours = getattr(Path, name, None)
            if ours is None:
                continue
            setattr(cls, name, ours)

    def __new__(cls, *args: _ty.Any, **kwargs: _ty.Any) -> _ty.Self:
        if cls is Path:
            from ..fspath import LocalPath

            # `LocalPath.__new__` is `pathlib.Path.__new__`, which parses `args` into
            # `_drv/_root/_parts` before 3.12 (3.12+ does it in `PurePath.__init__`);
            # `Pathname.__new__(cls)` would skip that and drop `args`.
            return _ty.cast("_ty.Self", LocalPath.__new__(LocalPath, *args, **kwargs))
        return Pathname.__new__(cls)

    def is_hidden(self):
        """Return True if the final path component is a hidden file/directory."""
        return self.name.startswith(".")


PathLike = _ty.Union[str, Path]
