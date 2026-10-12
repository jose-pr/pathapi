"""`Path`: a pathname with I/O, built from the protocols."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import os as _os
import pathlib as _pathlib
import shutil as _shutil
import stat as _stat
import sys as _sys
import time as _time
import types as _types
import typing as _ty

from .. import _errorpolicy, _names, _stubs
from .._errorpolicy import check_follow, copy_error_predicate
from ..protocols import BinaryOpen, Chmod, Stat
from ..protocols.io import _ASK_STAT
from .. import glob as _glob
from ..glob import _entries
from .._filestat import FileStat
from ..exceptions import NonRelativePatternError
from ._info import _PathInfo
from ._pure import Pathname
from ._relation import (
    _INNER,
    _OTHER,
    _OUTER,
    _SAME,
    _drop_second_name,
    _relation,
    _same_file,
    contains,
)
from ._removal import (
    _InvalidPolicy,
    _follow_policy,
    _is_junction,
    _listing,
    _write_batch_of,
)
from ._transfer import _copy_mode, _handing, _same_identity, _stat_or_none, _take_handed


def _os_error(exc_type: type, code: int, path: object) -> OSError:
    """`exc_type(code, strerror, str(path))` -- an OSError shaped like the
    ones `os` raises. `FileNotFoundError(path)` left `errno` and `filename`
    as None, so `e.errno == errno.ENOENT` never matched."""
    return exc_type(code, _os.strerror(code), str(path))


# Operations that must come from pathlib_next even when a concrete `pathlib`
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


class Path(Pathname, Chmod, Stat, BinaryOpen):
    """Base class for manipulating paths with I/O."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs):
        """Guarantee pathlib_next operation precedence in every subclass.

        Concrete path classes are routinely built by mixing a `pathlib`
        class with this one -- our own `LocalPath` does it, and the
        documented downstream recipe (`class X(PosixPathname, Path)`) does
        it transitively. Python's MRO then resolves a name to whichever
        base declares it first, which for those classes can be `pathlib`
        rather than `pathlib_next` -- and which one wins changes with the
        interpreter version, because stdlib `pathlib` keeps gaining and
        changing methods (see `_OPERATION_NAMES`).

        Rather than making every downstream implementer rediscover this and
        hand-write forwarding methods, re-assert our implementations here
        for any subclass that would otherwise inherit a non-pathlib_next
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
        take pathlib_next's arguments.
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
            # Only stdlib `pathlib` is displaced; a mixin, a user base or pathlib_next
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

    @property
    def info(self) -> "_PathInfo":
        """What this path is, as an object shaped like `pathlib.types.PathInfo`
        (Python 3.14): `exists()`, `is_dir()`, `is_file()` (each with
        `follow_symlinks=True`) and `is_symlink()`. It asks `stat()` at most
        once with and once without following links and remembers the answers
        on that object only: a new `info` is a new question. `LocalPath` has
        pathlib's own on 3.14."""
        return _PathInfo(self)

    def _copy_from(self, source, follow_symlinks=True, preserve_metadata=False):
        """Copy `source` -- any path with `info`, `iterdir()`, `readlink()` and
        `open()`, such as a `pathlib.Path` -- to this path, creating files and
        directories as pathlib 3.14 does: an existing file is overwritten, an
        existing directory raises. Python 3.14's `pathlib.Path.copy(target)`
        hands the work to `target._copy_from()`; `preserve_metadata` copies the
        permission bits and the modification time where this class can set
        them."""
        info = source.info
        link = not follow_symlinks and info.is_symlink()
        if link:
            self.symlink_to(str(source.readlink()), info.is_dir())
        elif info.is_dir():
            children = source.iterdir()
            self.mkdir()
            for child in children:
                self.joinpath(child.name)._copy_from(
                    child, follow_symlinks, preserve_metadata
                )
        else:
            with source.open("rb") as reader, self.open("wb") as writer:
                _shutil.copyfileobj(reader, writer)
        read = getattr(source, "stat", None)
        if preserve_metadata and not link and read is not None:
            stat = read()
            for apply in (
                lambda: self.chmod(_stat.S_IMODE(stat.st_mode)),
                lambda: self._utime(mtime=stat.st_mtime),
            ):
                try:
                    apply()
                except NotImplementedError:
                    pass

    def samefile(self, other_path: str | _ty.Self):
        """Return whether other_path is the same or not as this file, by
        comparing (st_dev, st_ino) when the backend's stat() provides them.
        Raises NotImplementedError otherwise (e.g. our own FileStat doesn't
        carry st_dev/st_ino) -- LocalPath gets a real implementation from
        pathlib.Path via MRO instead of this one.
        """
        # with_segments(), never the bare constructor or `_coerce_target()`:
        # a str is a path on this backend. `type(self)(str)` gave a MemPath
        # a fresh, empty backend, and a URI parse drops this path's host.
        other = (
            other_path
            if isinstance(other_path, Path)
            else self.with_segments(other_path)
        )
        st1 = self.stat()
        st2 = other.stat()
        ident1 = (getattr(st1, "st_dev", None), getattr(st1, "st_ino", None))
        ident2 = (getattr(st2, "st_dev", None), getattr(st2, "st_ino", None))
        if None in ident1 or None in ident2:
            raise NotImplementedError(
                "samefile() requires stat() to provide st_dev/st_ino"
            )
        return ident1 == ident2

    def __iter__(self):
        return self.iterdir()

    @_stubs.notimplemented
    def iterdir(self) -> "_ty.Iterator[_ty.Self]":
        """Yield path objects of the directory contents.

        The children are yielded in arbitrary order, and the
        special entries '.' and '..' are not included.
        """
        ...

    def _scandir(self) -> "_ty.Iterator[_ty.Tuple[str, _ty.Optional[FileStat]]]":
        """Yield (name, stat_or_None) for each directory entry. Used by
        `walk()`/`glob()` instead of `iterdir()` so schemes whose listing
        call already returns metadata (HttpPath, DavPath, SftpPath, FtpPath,
        S3Path) can answer `is_dir()` on the results without a further
        round trip per entry. Default: falls back to `iterdir()` + one
        `stat()` per child (no round-trip savings, but no subclass is
        required to implement this) -- see `docs/guides/extending.md`.
        """
        for entry in self.iterdir():
            try:
                # follow_symlink=False to match walk()'s own default -- an
                # explicit walk(follow_symlinks=True) always re-stats each
                # entry itself regardless of what's yielded here.
                stat = FileStat.from_path(entry, follow_symlink=False)
            except OSError:
                stat = None
            yield entry.name, stat

    def glob(
        self,
        pattern: str | _ty.Self | None,
        *,
        case_sensitive: bool = None,
        include_hidden: bool = True,
        recursive: bool = None,
        dironly: bool = None,
        recurse_symlinks: bool = False,
        native: bool = True,
        on_error: "_ty.Callable[[OSError], None]" = None,
        bound_loops: bool = False,
    ):
        """Iterate over this subtree and yield all existing files (of any
        kind, including directories) matching the given relative pattern.

        Pathlib semantics: hidden entries are matched (`include_hidden=False`
        filters them out and skips hidden directories), a trailing separator
        selects directories only, "**" does not descend into a directory
        symlink by default, and a trailing "**" also selects files on Python 3.13+
        (directories only before). A missing or non-directory parent selects
        nothing. An empty pattern raises `ValueError`, an absolute one
        `NonRelativePatternError` (a `NotImplementedError` and a
        `ValueError`).

        `recurse_symlinks=True` lets "**" descend through symlinks to
        directories, as pathlib 3.13 does. Where that would loop it stops: a
        link to a directory the descent is already inside (compared by
        `st_dev`/`st_ino`) is an entry but is not entered, and where the stat
        carries no identity no more than 40 links are followed in one
        descent. A backend without symlinks is not affected.

        `pattern=None` expands the pattern THIS PATH CARRIES
        (`LocalPath("/etc/*.conf").glob(None)`) instead of applying one to a
        directory: the path is split at its first wildcard and globbed from
        there. `""` still raises, as pathlib does. Every keyword means what
        it does for a pattern argument -- `recursive=None` auto-enables
        recursion for a carried "**", and `native=` applies. `include_hidden`
        keeps THIS method's default (True); the module-level
        `glob.glob()` this delegates to defaults it to False,
        stdlib-`glob`-style, so the two spellings differ on hidden names
        unless the keyword is passed.

        `native=True` (the default) follows the running interpreter on the
        two rules pathlib changed mid-series: a trailing "/" is ignored
        before 3.11, and a component that merely contains "**" ("a**")
        raises `ValueError` before 3.13. `native=False` applies one rule on
        every version -- trailing "/" always selects directories only, "a**"
        is always a plain wildcard -- so a pattern answers the same
        everywhere, which is what a cross-backend or cross-version caller
        usually wants.

        `on_error(error)` is called when a directory cannot be listed, the
        same contract as `walk()` and `os.walk`: raising from it propagates
        (an unreadable directory becomes an error), returning treats that
        directory as empty. Without it such a listing is skipped silently --
        pathlib's behaviour, and the reason a config loader could not tell a
        missing layer from an unreadable one. `error.filename` names the
        directory even when the backend left it empty.

        `bound_loops=True` skips a directory whose `(st_dev, st_ino)` is
        already on the current descent path, which is what a loop is: that
        bounds a Windows junction loop, which no symlink check can see (a
        junction reports `is_symlink() == False`, so `recurse_symlinks=False`
        does not help and pathlib itself loops until the recursion limit). A
        directory reachable under two sibling names is entered under both. A
        backend whose stat carries no identity is walked unbounded.

        A "**" component auto-enables recursion. Pass `recursive=False`
        explicitly to treat "**" as a plain "*" instead.
        Note for remote schemes (http/sftp): a recursive glob walks the
        whole remote subtree, one request/roundtrip per directory.
        """
        if pattern is None:
            # The pattern is the path itself; `glob()` splits at the first
            # wildcard, so an absolute one is fine here (unlike a pattern
            # argument, which must stay relative to self).
            return _glob.glob(
                self,
                recursive=recursive,
                include_hidden=include_hidden,
                case_sensitive=case_sensitive,
                dironly=bool(dironly),
                native=native,
                on_error=on_error,
                bound_loops=bound_loops,
                recurse_symlinks=recurse_symlinks,
            )
        # Validates eagerly (like pathlib 3.13+); the returned selection is
        # lazy. The pattern is never joined onto self: `self / pattern` let an
        # absolute pattern escape self and re-parsed "?" as a URI query.
        parts, trailing_sep = _glob.parse_pattern(pattern, native=native)
        if recursive is None:
            recursive = _glob.RECURSIVE in parts
        return _glob.select(
            self,
            parts,
            dironly=trailing_sep if dironly is None else dironly,
            recursive=recursive,
            include_hidden=include_hidden,
            case_sensitive=case_sensitive,
            native=native,
            on_error=on_error,
            bound_loops=bound_loops,
            recurse_symlinks=recurse_symlinks,
        )

    def rglob(
        self,
        pattern: str | None,
        *,
        case_sensitive: bool = None,
        include_hidden: bool = True,
        recursive: bool = True,
        dironly: bool = None,
        recurse_symlinks: bool = False,
        native: bool = True,
        on_error: "_ty.Callable[[OSError], None]" = None,
        bound_loops: bool = False,
    ):
        """Equivalent to `glob(f"**/{pattern}", recursive=True)`.

        `pattern=None` and `native=` mean what they do on `glob()`; with
        `None` this is `glob(None, recursive=True)`, since a carried pattern
        brings its own anchor and nothing can be prefixed to it."""
        if pattern is None:
            return self.glob(
                None,
                case_sensitive=case_sensitive,
                include_hidden=include_hidden,
                recursive=recursive,
                dironly=dironly,
                recurse_symlinks=recurse_symlinks,
                native=native,
                on_error=on_error,
                bound_loops=bound_loops,
            )
        if not (isinstance(pattern, str) and not pattern):
            # Reject an absolute pattern before "**/" hides its anchor; glob() validates
            # without listing. A pattern with no component ("." or "./") is allowed.
            try:
                self.glob(pattern, recursive=False, native=native)
            except NonRelativePatternError:
                raise
            except ValueError:
                pass
        return self.glob(
            f"**/{pattern}",
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            recursive=recursive,
            dironly=dironly,
            recurse_symlinks=recurse_symlinks,
            native=native,
            on_error=on_error,
            bound_loops=bound_loops,
        )

    def walk(
        self,
        top_down=True,
        on_error: _ty.Callable[[OSError | ValueError], None] = None,
        follow_symlinks=False,
    ):
        """Walk the directory tree from this directory, similar to os.walk().

        Uses `_scandir()` rather than `iterdir()` + a `stat()` per entry --
        for schemes whose listing already carries type metadata (HTTP/DAV
        indexes, SFTP `listdir_attr`, FTP MLSD, S3 list pages), this turns a
        remote-tree walk from O(entries) round trips into O(dirs). The
        pre-seeded stat is trusted only when `follow_symlinks` is False
        (matching `walk()`'s own default and the lstat-like semantics of
        those listing calls); an explicit `follow_symlinks=True` always
        re-`stat()`s each entry so a symlink is still resolved.

        Without `follow_symlinks` a symlink to a directory is listed in
        `filenames` and not entered, and so is a Windows junction
        (`is_junction()`): like a symlink it is a second NAME for another
        tree, which `pathlib.Path.walk()` does not enter either (a junction
        that points at its own parent would otherwise be walked until the
        path is too long). Nothing protects a walk with `follow_symlinks=True`
        from a loop.

        A listed name that is not one path component (`""`, `.`, `..`, or one
        containing `/` or NUL; see `providers.is_safe_child_name()`) is left out
        of `dirnames` and `filenames`. `on_error`, when given, is called with
        a `ValueError` for it, `error.filename` naming the directory.
        """
        paths: "list[_ty.Self|tuple[_ty.Self, list[str], list[str]]]" = [self]

        while paths:
            path = paths.pop()
            if isinstance(path, tuple):
                yield path
                continue
            try:
                # Materialized inside the try: `_scandir()` is a generator, so a failing
                # listing may raise at the first `next()`, and `on_error` must see it.
                entries = list(path._scandir())
            except OSError as error:
                if on_error is not None:
                    on_error(error)
                continue

            dirnames: "list[str]" = []
            filenames: "list[str]" = []
            for name, stat in entries:
                if not _names.is_safe_child_name(name):
                    # A listing is untrusted input: "..", "a/b" or "" would
                    # build a child outside `path` and send the walk there.
                    if on_error is not None:
                        error = ValueError(
                            f"refusing unsafe child name {name!r} under {path}"
                        )
                        error.filename = str(path)
                        on_error(error)
                    continue
                try:
                    if stat is None or follow_symlinks:
                        stat = FileStat.from_path(
                            _entries._child(path, name), follow_symlink=follow_symlinks
                        )
                    is_dir = stat.is_dir() if stat is not None else False
                    if is_dir and not follow_symlinks:
                        is_dir = not _is_junction(_entries._child(path, name))
                except OSError:
                    # Carried over from os.path.isdir().
                    is_dir = False

                if is_dir:
                    dirnames.append(name)
                else:
                    filenames.append(name)

            if top_down:
                yield path, dirnames, filenames
            else:
                paths.append((path, dirnames, filenames))

            paths += [_entries._child(path, d) for d in reversed(dirnames)]

    def touch(self, mode=None, exist_ok=True):
        """
        Create this file, if it doesn't exist.

        Raises FileExistsError if exist_ok is False and the file already
        exists (pathlib parity). An existing file is never truncated.

        Differences from `pathlib.Path.touch`, which creates through
        `os.open()` (so the process umask applies) and bumps an existing
        file's mtime:

        - `mode` is applied with `chmod()` only when passed explicitly, and
          then verbatim: a remote server's umask is unknowable. The default
          (`None`) leaves a new file with the permissions the backend gives
          it, instead of chmod'ing it to a world-writable 0o666.
        - An existing file's access and modification times are set to the
          current time through `_utime()`; a backend that cannot set a time
          leaves the file as it is, since rewriting the content to bump it
          is not a touch. `LocalPath` and `FileUri` use pathlib's own touch.
        """
        # stat() directly, not exists(): exists() reads *any* OSError (a
        # timeout, a dropped connection) as "missing", which let a transient
        # failure fall through to a truncating open("w").
        try:
            self.stat()
        except FileNotFoundError:
            pass
        else:
            if not exist_ok:
                raise _os_error(FileExistsError, _errno.EEXIST, self)
            now = _time.time()
            try:
                self._utime(atime=now, mtime=now)
            except NotImplementedError:
                pass
            return
        try:
            with self.open("x"):
                ...
        except FileExistsError:
            # Created since the stat() above: an existing file, not ours to
            # truncate.
            if not exist_ok:
                raise
            return
        except NotImplementedError:
            # _open() doesn't support "x" (optional per the mode contract),
            # so the stat() above is the only guard before the truncating
            # "w" -- a small TOCTOU window.
            with self.open("w"):
                ...
        if mode is not None:
            try:
                self.chmod(mode)
            except NotImplementedError:
                pass

    @_stubs.notimplemented
    def _mkdir(self, mode: int): ...

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        """
        Create a new directory at this given path.
        """
        try:
            self._mkdir(mode)
        except FileNotFoundError:
            if not parents or self.parent == self:
                raise
            # Parents may already exist (e.g. a sibling branch created them)
            # -- mirror CPython: exist_ok=True for parents, original
            # exist_ok only for the final retry of self.
            self.parent.mkdir(parents=True, exist_ok=True)
            self.mkdir(mode, parents=False, exist_ok=exist_ok)
        except FileExistsError:
            if not exist_ok or not self.is_dir():
                raise

    @_stubs.notimplemented
    def unlink(self, missing_ok=False):
        """
        Remove this file or link.
        If the path is a directory, use rmdir() instead.
        """

    @_stubs.notimplemented
    def rmdir(self):
        """
        Remove this directory.  The directory must be empty.
        """

    def rm(
        self,
        /,
        recursive=False,
        missing_ok=False,
        ignore_error: bool | _ty.Callable[[Exception, _ty.Self], bool] = False,
        *,
        follow_symlinks: "bool | None | _ty.Callable[[_ty.Self], bool | None]" = False,
        follow_binds: "bool | None | _ty.Callable[[_ty.Self], bool | None]" = False,
    ):
        """Remove this file or directory, optionally recursively and ignoring errors.

        `follow_symlinks` applies to a symlink met during a recursive
        removal, `follow_binds` to a binding -- a directory that is another
        tree's second NAME rather than part of this one (`is_dir_binding()`:
        a Windows junction, a mount point, a bind mount). Both take:

        - `False` (default) -- remove the entry itself, never what is behind
          it: `unlink()` for a symlink, `rmdir()` for a binding. This is what
          `rm -r` does, and on a live mount the `rmdir()` fails rather than
          emptying someone else's filesystem.
        - `True` -- remove the contents behind it, then the entry. What a
          walker that cannot tell the difference does by accident.
        - `None` -- leave it in place. The enclosing directory is then not
          empty, so its own removal reports that.
        - a callable `policy(path) -> bool | None` -- asked per entry with
          the same three answers, so one tree can keep one mount and follow
          another.

        Path components BEFORE the final one are followed as usual; these
        decide what happens to the entry itself, so they apply to the path
        `rm()` is called on as to one met on the way down: a symlink named
        directly is removed, emptied first (`True`) or left (`None`), and
        without `recursive` it is only ever unlinked. The name matches
        `stat()`/`walk()`/`copy()`'s `follow_symlinks=` rather than
        inventing a second vocabulary for the same idea.

        A policy that is not `True`, `False`, `None` or a callable raises
        `ValueError` before anything is removed; so does a callable's answer
        that is not one of the three, whatever `ignore_error` says. A listed
        name that is not one path component (`""`, `.`, `..`, or one
        containing `/` or NUL; see `providers.is_safe_child_name()`) is never
        joined onto the directory: it raises `ValueError`, offered to
        `ignore_error` as `(error, directory)` once per entry, and nothing is
        removed for it.
        """
        # Same bool-or-callable normalization as copy()/PathSyncer, via the
        # shared helper. A supplied callable keeps rm()'s own `(error, path)`
        # arity -- arities differ per call site by design, see the helper.
        _onerror = _errorpolicy.as_error_handler(ignore_error)
        check_follow("follow_symlinks", follow_symlinks)
        check_follow("follow_binds", follow_binds)

        # An error the handler declined, or raised itself, on its way out:
        # each enclosing directory's `except` catches it again, and consulting
        # the handler there reported one failure once per ancestor.
        declined = []

        def _handle(error, path):
            if isinstance(error, _InvalidPolicy):
                # A mistake of the caller, not a removal that failed.
                raise error
            if any(error is seen for seen in declined):
                raise error
            try:
                swallowed = _onerror(error, path)
            except BaseException as raised:
                declined.append(raised)
                raise
            if not swallowed:
                declined.append(error)
                raise error

        def _remove_tree(path, *, link=False):
            try:
                entries = list(_listing(path))
            except Exception as error:
                _handle(error, path)
                return

            for name, child_stat in entries:
                child = None
                try:
                    # Checked before any child is built from the name; only the one-component
                    # rule, as a listed name is not rewritten by the platform that listed it.
                    if not _names.is_safe_child_name(name):
                        raise ValueError(
                            f"refusing unsafe child name {name!r} under {path}"
                        )
                    child = _entries._child(path, name)
                    if child_stat is None:
                        child_stat = FileStat.from_path(child, follow_symlink=False)
                    if child_stat is not None and child_stat.is_symlink():
                        # A link, whatever it points at. The non-following
                        # stat says "link", so this branch is reached before
                        # the directory one.
                        policy = _follow_policy(follow_symlinks, child)
                        if policy == "ignore":
                            continue
                        if policy == "follow" and child.is_dir():
                            _remove_tree(child, link=True)
                        else:
                            child.unlink()
                    elif child_stat is not None and child_stat.is_dir():
                        if child.is_dir_binding():
                            # A binding: what is inside belongs to the tree
                            # it names, not to this one.
                            policy = _follow_policy(follow_binds, child)
                            if policy == "ignore":
                                continue
                            if policy == "follow":
                                _remove_tree(child)
                            else:
                                # `rmdir()` on a live mount fails (EBUSY) --
                                # the right answer, and far better than
                                # emptying someone else's filesystem.
                                child.rmdir()
                        else:
                            _remove_tree(child)
                    else:
                        child.unlink()
                except Exception as error:
                    _handle(error, path if child is None else child)

            try:
                # `rmdir()` on a symlink is ENOTDIR on POSIX: the link is
                # unlinked once what is behind it has been removed.
                path.unlink() if link else path.rmdir()
            except Exception as error:
                _handle(error, path)

        try:
            stat = FileStat.from_path(self, follow_symlink=False)
        except Exception as error:
            _handle(error, self)
            return
        if stat is None:
            if not missing_ok:
                _handle(_os_error(FileNotFoundError, _errno.ENOENT, self), self)
        elif stat.is_symlink():
            policy = _follow_policy(follow_symlinks, self)
            if policy == "ignore":
                return
            try:
                if policy == "follow" and recursive and self.is_dir():
                    _remove_tree(self, link=True)
                else:
                    self.unlink()
            except Exception as error:
                _handle(error, self)
        elif stat.is_dir():
            binding = self.is_dir_binding()
            policy = _follow_policy(follow_binds, self) if binding else "follow"
            if policy == "ignore":
                return
            if recursive and (not binding or policy == "follow"):
                _remove_tree(self)
            else:
                try:
                    self.rmdir()
                except Exception as error:
                    _handle(error, self)
        else:
            try:
                self.unlink()
            except Exception as error:
                _handle(error, self)

    def _coerce_target(self, target: str) -> "Path":
        """Turn a `str` destination (`copy()`, `move()`) into a
        path on the same backend. The default keeps per-instance state (a
        `MemPath`'s in-memory filesystem) via `with_segments()`; the bare
        constructor used before gave every str destination a fresh, empty
        backend, so `move("/c.txt")` wrote there and then deleted the source.
        `UriPath` overrides this to parse the string as a URI."""
        return self.with_segments(target)

    def _rename_compatible(self, target: "Path") -> bool:
        """Whether `rename()` may be attempted onto `target` at all. `move()`
        skips straight to copy + delete when it is not. Default: only onto a
        path of this very class, since `rename()` runs inside this class's
        own store and would otherwise be handed a path of another store (a
        `MemPath` subclass given a `LocalPath` renamed within memory).
        `UriPath` decides per target in `rename()` (raising
        NotImplementedError); `LocalPath` requires a local target, since
        `os.rename()` would otherwise take a remote path's `__fspath__()` as
        a local name."""
        return type(target) is type(self)

    def _write_batch(self) -> "_ty.ContextManager[object]":
        """A context manager that `copy(recursive=True)` and `PathSyncer` enter
        around the writes they make to this path's tree, so a backend that
        pays for every write (a zip rewrites its file) can commit them
        together when the block ends. It must read nothing and raise nothing;
        the default does nothing."""
        return _contextlib.nullcontext()

    def _same_filesystem(self, other: "Path") -> bool:
        """Whether `other`'s segments are resolved in the same namespace as
        this path's: the same host, store or tree. Only when they are do
        `==` and `is_relative_to()` between the two say anything about files,
        so `copy()`, `move()` and `PathSyncer` ask this before refusing a
        transfer as "the same file" or "overlapping".

        Called only with `type(other) is type(self)`. Must be symmetric and
        must not do I/O. Default: both share one `_backend` object (or neither
        has one), which makes equal paths of a type that says nothing the
        same file. A type whose instances can front different hosts or stores
        MUST override this, or a transfer between two of them that are spelled
        alike is refused."""
        return getattr(self, "_backend", None) is getattr(other, "_backend", None)

    def _node_key(self) -> "tuple[object, tuple[str, ...]] | None":
        """Where this path's node lives, for the same-file and nesting checks
        of `copy()` and `move()`: `(namespace, names)`. `namespace` is an
        object two paths share (compared with `is`) exactly when they resolve
        names in one tree; `names` is the node's position in it, normalized
        so that every spelling of one node gives the same tuple and the
        root gives `()`.

        Default `None`: two paths of one type that `_same_filesystem()`
        places together are compared with `==` and `is_relative_to()`. A type
        whose segments spell one node in several ways (a relative and an
        absolute form, a `..` segment, an alias scheme) overrides this, and
        its paths are then compared across classes. Must not do I/O."""
        return None

    def is_junction(self) -> bool:
        """Whether this path is a Windows junction (pathlib 3.12 parity).

        A junction is NOT a symlink: it is a second NAME for a directory,
        the Windows equivalent of a Linux bind mount. `is_symlink()` is
        False for one, `readlink()` does not describe it, and a
        non-following stat reports a plain directory -- which is exactly why
        a symlink check cannot protect a recursive walk from it.

        Default False; `LocalPath`/`FileUri` answer for real. A remote
        scheme has no such concept unless it says otherwise.
        """
        return False

    def is_mount(self) -> bool:
        """Whether this path is a mount point (pathlib parity).

        The POSIX half of the same idea: a bind mount makes one tree visible
        under a second name, with no link anywhere to say so. Default False;
        `LocalPath`/`FileUri` answer for real.
        """
        return False

    def is_dir_binding(self) -> bool:
        """Whether this directory is another tree's second NAME rather than
        part of this one: a Windows junction or a mount point (bind or
        otherwise).

        The distinction that matters to anything walking a tree. A symlink
        announces itself -- `is_symlink()` is True and every walker here
        already declines to follow one. A binding does not: it looks like an
        ordinary directory, and its contents belong to whoever mounted or
        junctioned it. `rm(recursive=True)` removes the binding itself
        rather than the contents behind it.

        An implementation that cannot answer (stdlib's `is_mount()` raises
        on Windows before 3.12) counts as "not a binding" rather than
        breaking the operation that asked.
        """
        for test in (self.is_junction, self.is_mount):
            try:
                if test():
                    return True
            except (NotImplementedError, OSError, ValueError):
                continue
        return False

    @_stubs.notimplemented
    def rename(self, target: "_ty.Self | str"):
        """Rename this file or directory to the given target."""
        ...

    @_stubs.notimplemented
    def _symlink_to(
        self, target: "_ty.Self", target_is_directory: bool = False
    ) -> None:
        """Create a symlink at this path pointing at `target`.

        The backend primitive behind `symlink_to()`, in the same shape as
        `_mkdir`/`_open`: implement only this, and the generic conveniences
        (`force=`) come for free. `target` has already been normalized to a
        path object of this class by `symlink_to()` -- accepting `str` and
        turning it into a path is the wrapper's job, not the primitive's.

        Implementations take the raw target string from the path object the
        way their own backend needs it (`Uri.path` for wire protocols,
        `os.fspath()`/`as_posix()` locally); it never carries a scheme or
        host prefix, and a relative target stays relative.
        """
        ...

    def _symlink_target(self, target: "_ty.Self | str") -> "_ty.Self":
        """Normalize a `symlink_to()` target argument to a path object.

        A `str` target is the **literal link target** -- whatever it says
        is what gets stored, verbatim and unresolved, exactly as
        `pathlib.Path.symlink_to()` does. The default builds it with
        `with_segments()`, which keeps per-instance state (a `MemPath`'s
        backend) but normalizes the text as this class parses a path;
        override it wherever that would reinterpret the string instead of
        taking it literally (`LocalPath` hands it on untouched, `UriPath`
        does not parse it as a URI; see `UriPath._symlink_target`).
        """
        return self.with_segments(target) if isinstance(target, str) else target

    def symlink_to(
        self,
        target: "_ty.Self | str",
        target_is_directory: bool = False,
        *,
        force: bool = False,
    ) -> None:
        """Make this path a symlink pointing to `target`.

        Signature-compatible with `pathlib.Path.symlink_to()`; `force` is a
        pathlib_next extension (see `docs/divergences.md`).

        With `force=True`, an existing entry at *this* path is removed
        first. No filesystem or transport offers an atomic "replace a
        symlink" operation, so this is an unlink-then-symlink sequence and
        is therefore **not** atomic: between the two steps the path does not
        exist, and a concurrent writer can win the race. It is a
        convenience, not a locking primitive.

        Only a non-directory entry is removed -- an existing *directory* at
        the link path is left alone and the underlying `FileExistsError`
        (or the backend's equivalent) propagates. Silently deleting a
        directory tree is never what `force=` on a symlink call is asking
        for.
        """
        # Through `_symlink_target()`, not `type(self)(target)`: for a URI-backed path
        # the constructor re-parses the string as URI syntax, cutting the target at "?"
        # or "#" and percent-decoding it.
        target = self._symlink_target(target)
        if force:
            try:
                self.unlink(missing_ok=True)
            except (IsADirectoryError, PermissionError, OSError) as error:
                # A directory at the link path (IsADirectoryError on POSIX; PermissionError or
                # an OSError on Windows and remote backends) is not removed by force=: the
                # symlink attempt below raises the error that describes the conflict.
                if not self.is_dir():
                    raise error
        return self._symlink_to(target, target_is_directory)

    def copy(
        self,
        target: "Path | str",
        *,
        overwrite=False,
        follow_symlinks=True,
        preserve_metadata=True,
        recursive=False,
        ignore_error=None,
        progress: "_ty.Callable[[_ty.Self, int, _ty.Optional[int]], None]" = None,
        on_error: "bool | _ty.Callable[[Exception, _ty.Self], bool] | None" = None,
    ):
        """Copy this file's content to `target`.

        `follow_symlinks`/`preserve_metadata` are named to match CPython
        3.14's Path.copy() (added after this method); `overwrite` is our
        own extension (3.14 has no equivalent -- it always raises if the
        destination exists). `preserve_metadata` defaults to True here
        (unlike 3.14's False): st_mode is propagated, but not
        timestamps or xattrs -- full metadata preservation is not implemented.
        `ignore_error` accepts a bool or a callable. `True` ignores every
        error; `False` and `None` (the default) fail on the first error. A
        callable is invoked as `ignore_error(error)` and, as it always has
        here, is a *notification* hook: the error is suppressed regardless of
        what it returns, so handlers like `errors.append` (returning None)
        keep working. Only errors from *child* copies during a
        `recursive=True` copy are routed here.

        `on_error` is `rm()`'s predicate: a callable `(error, path) -> bool`
        (or a bool) asked once per child error, with `path` the source entry
        that failed. `True` skips the entry and goes on, `False` raises the
        error, and a predicate that raises ends the copy with its own
        exception. An error it declined is not offered again by the
        directories above. `ignore_error` and `on_error` are two spellings of
        one policy: passing both raises `TypeError` before anything is copied.

        A child whose listed name is not one safe component for `target` (a
        drive such as `C:x` or a backslash on a Windows target, which land
        outside it; a trailing dot, which Windows stores under another name)
        is refused with `ValueError` and reported through `ignore_error` or
        `on_error` like any child failure. `PathSyncer` and
        `unpack_archive()` apply the same rule; an archive listing keeps
        such names, as they are ordinary filenames on POSIX.

        `progress`, when given, is called as `progress(path, bytes_copied,
        total_size)` for every chunk written during each *file* copy (`path`
        is the source `Path` being streamed -- `self` for a single-file
        copy, or the relevant child during a `recursive=True` copy).
        `bytes_copied` increases monotonically per file and reaches
        `total_size` (or `None` if the size couldn't be determined) at the
        end of that file. Directories themselves don't get a progress call
        (only the files inside them do). With `progress=None` (the
        default), behavior is unchanged -- no per-chunk overhead. Native
        backend transfers that bypass the generic streaming copy (e.g.
        `SftpPath`'s asyncssh concurrent fan-out) do not invoke `progress`;
        see `docs/divergences.md`'s "Deliberate extensions" section.
        """
        offering = copy_error_predicate(ignore_error, on_error)
        if isinstance(target, str):
            target = self._coerce_target(target)
        src = self
        handed = _take_handed(src, target)
        known = None if handed is None else handed.source_stat

        if not follow_symlinks and known is None and src.is_symlink():
            # pathlib 3.14: copy the link itself, not what it points at.
            # Copying the target's content (and chmod'ing it with the link's
            # own 0o777) silently defeated the flag.
            return src._copy_symlink(target, overwrite=overwrite)

        # One stat of the source answers its type, its mode and, beside the
        # target's, whether the two are one file; None when it cannot be had.
        src_stat = known if known is not None else _stat_or_none(src, follow_symlinks)
        target_absent = handed is not None and handed.target_absent

        if recursive and src_stat is not None and _stat.S_ISDIR(src_stat.st_mode):
            if contains(src, target):
                # Checked before anything is created: the new directory would
                # be listed and copied into itself without end.
                raise OSError(
                    _errno.EINVAL,
                    "Cannot copy a directory into itself",
                    str(target),
                )
            # The writes below may be committed together by a backend that
            # pays for each one (see `_write_batch`).
            with _write_batch_of(target):
                # Listed before the target exists, as shutil.copytree does.
                children = list(src.iterdir())
                if not target_absent and target.exists():
                    if not target.is_dir():
                        raise FileExistsError(target)
                    if not overwrite:
                        raise FileExistsError(target)
                else:
                    target.mkdir()
                windows_target = _names.is_windows_flavoured(target)
                for child in children:
                    try:
                        # A listed name is untrusted: one that is not a single component inside
                        # `target` is refused (see the child-name paragraph of the docstring).
                        if not _names.is_safe_child_name(
                            child.name, windows=windows_target
                        ):
                            raise ValueError(
                                f"refusing unsafe child name {child.name!r} "
                                f"under {target}"
                            )
                        child.copy(
                            target / child.name,
                            overwrite=overwrite,
                            follow_symlinks=follow_symlinks,
                            preserve_metadata=preserve_metadata,
                            recursive=True,
                            ignore_error=ignore_error,
                            progress=progress,
                            # Passed on only when given, so a `copy()` override
                            # without the keyword still works as a child.
                            **({} if offering is None else {"on_error": offering}),
                        )
                    except Exception as e:
                        if offering is not None:
                            if not offering(e, child):
                                raise
                        elif callable(ignore_error):
                            # A notify-and-suppress hook: its return value is not
                            # consulted, so `errors.append` works.
                            ignore_error(e)
                        elif not ignore_error:
                            raise
                if preserve_metadata:
                    # After the children: a read-only directory could not take
                    # them, and its mode is only settled once they are in.
                    _copy_mode(src, target, follow_symlinks, src_stat)
                return

        # What needs no I/O first; the stats below settle the rest.
        if _same_file(src, target, identity=False):
            raise OSError(
                _errno.EINVAL, "Source and target are the same file", str(target)
            )
        # stat(), not exists(): exists() reads a transient error (a 503, a
        # timeout) as "missing", and overwrite=False must never be decided by
        # a failure. Only FileNotFoundError means the target is absent.
        try:
            target_stat = None if target_absent else target.stat()
        except FileNotFoundError:
            target_stat = None
        target_exists = target_stat is not None
        if target_stat is not None:
            if _same_identity(src_stat, target_stat):
                raise OSError(
                    _errno.EINVAL, "Source and target are the same file", str(target)
                )
            if _stat.S_ISDIR(target_stat.st_mode):
                raise IsADirectoryError(target)
            if not overwrite:
                raise FileExistsError(target)

        # Open the source before the target is touched at all: a missing or
        # unreadable source must leave an existing target intact and must not
        # leave a new empty one behind.
        with src.open("rb") as input:
            if target_exists:
                target.unlink(missing_ok=True)
            created = False
            try:
                with target.open("wb") as output:
                    created = True
                    BinaryOpen._copy_stream(
                        src,
                        input,
                        output,
                        progress=(
                            None
                            if progress is None
                            else lambda copied, total: progress(src, copied, total)
                        ),
                        total_size=(
                            _ASK_STAT
                            if src_stat is None
                            else (
                                getattr(src_stat, "st_size", None)
                                if getattr(src_stat, "size_known", True)
                                else None
                            )
                        ),
                    )
            except BaseException:
                # A half-written target is not a copy of anything; do not
                # leave it looking like one.
                if created:
                    try:
                        target.unlink(missing_ok=True)
                    except Exception:
                        pass
                raise

        if preserve_metadata:
            _copy_mode(src, target, follow_symlinks, src_stat)

    def _into(self, target_dir: "Path | str") -> "Path":
        """`target_dir / self.name`, the target of `copy_into()` and
        `move_into()`. A `str` directory is a path on this backend, as for
        `copy()`'s `str` target."""
        name = self.name
        if not name:
            raise ValueError("%r has an empty name" % (self,))
        if isinstance(target_dir, str):
            target_dir = self._coerce_target(target_dir)
        return target_dir / name

    def copy_into(
        self,
        target_dir: "Path | str",
        *,
        overwrite=False,
        follow_symlinks=True,
        preserve_metadata=True,
        recursive=False,
        ignore_error=None,
        progress: "_ty.Callable[[_ty.Self, int, _ty.Optional[int]], None]" = None,
        on_error: "bool | _ty.Callable[[Exception, _ty.Self], bool] | None" = None,
    ) -> "Path":
        """Copy this file or directory into the directory `target_dir`, under
        its own name, and return the new path (pathlib 3.14 parity).

        `copy(target_dir / self.name, ...)` with the same keywords and the
        same defaults as `copy()`: an existing target needs `overwrite=True`
        and a directory source needs `recursive=True`. `ValueError` for a
        path with no name."""
        target = self._into(target_dir)
        self.copy(
            target,
            overwrite=overwrite,
            follow_symlinks=follow_symlinks,
            preserve_metadata=preserve_metadata,
            recursive=recursive,
            ignore_error=ignore_error,
            progress=progress,
            **({} if on_error is None else {"on_error": on_error}),
        )
        return target

    def move_into(self, target_dir: "Path | str", *, overwrite=False) -> "Path":
        """Move this file or directory into the directory `target_dir`, under
        its own name, and return the new path (pathlib 3.14 parity).

        `move(target_dir / self.name, overwrite=overwrite)`: an existing
        target needs `overwrite=True`. `ValueError` for a path with no name."""
        target = self._into(target_dir)
        self.move(target, overwrite=overwrite)
        return target

    def replace(self, target: "Path | str") -> "Path":
        """Rename this file or directory to `target`, replacing what is
        there, and return `target` as a path (pathlib parity).

        A file replaces a file and a directory replaces an empty directory;
        a directory or a file standing in the way of the other kind raises
        as `os.replace()` does, and a directory that holds anything raises
        `OSError(ENOTEMPTY)` rather than being removed. Built on `move()`, so
        it works between any two paths `move()` accepts; `LocalPath` keeps
        stdlib's `os.replace()`."""
        if isinstance(target, str):
            target = self._coerce_target(target)
        if target.is_dir() and next(iter(target._scandir()), None) is not None:
            raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(target))
        self.move(target, overwrite=True)
        return target

    def _copy_symlink(self, target: "Path", *, overwrite=False):
        """`copy(follow_symlinks=False)` of a symlink: create a link at
        `target` with the same (unresolved) target text. Raises
        NotImplementedError when this backend cannot read links or the
        target's cannot create them -- never falls back to copying content."""
        readlink = getattr(self, "readlink", None)
        if not callable(readlink):
            raise NotImplementedError(f"copy(follow_symlinks=False) of {self!r}")
        link = readlink()
        if type(link) is not type(target):
            # Another backend: hand over the literal target text.
            text = getattr(link, "path", None)
            link = text if isinstance(text, str) else link.as_posix()
        if _same_file(self, target):
            raise OSError(
                _errno.EINVAL, "Source and target are the same file", str(target)
            )
        target_stat = FileStat.from_path(target, follow_symlink=False)
        if target_stat is not None:
            if target_stat.is_dir() and not target_stat.is_symlink():
                raise IsADirectoryError(target)
            if not overwrite:
                raise FileExistsError(target)
            target.unlink(missing_ok=True)
        target.symlink_to(link, target_is_directory=self.is_dir())

    def move(self, target: "Path|str", *, overwrite=False):
        """Move this file or directory to target, falling back to copy+unlink/rm if rename is unsupported.

        Nothing is removed until the move is known to be allowed: a missing
        source, a file onto a directory, a target that holds the source
        (`OSError` `ENOTEMPTY`) or lies inside it (`EINVAL`), and a second
        name of the same file that is a link (`EINVAL`) all raise first. The
        same file under another spelling (a case alias, `x.move(x)`) is
        renamed in place. Between two hard links of one file, an existing
        target needs `overwrite=True`, and the source name is then removed.
        """
        if isinstance(target, str):
            target = self._coerce_target(target)
        src = self
        # rename() only makes sense between paths the same backend can see.
        native = src._rename_compatible(target)

        # Everything that can fail cheaply is checked before the target is
        # touched: a missing source, or a file onto a directory, must not
        # delete the target and only then raise.
        held = _Held(src)
        src_stat = held.source
        if src_stat is None:
            raise FileNotFoundError(
                _errno.ENOENT, "No such file or directory", str(src)
            )
        # The same entry under another spelling (a case-only rename on a
        # case-insensitive filesystem, or `x.move(x)`) is renamed in place:
        # removing the "existing" target would delete the source itself.
        relation = _relation(src, target, follow=False, held=held)
        target_stat = None
        if relation != _SAME:
            target_stat = held.target(target)
            if target_stat is not None and not overwrite:
                raise FileExistsError(target)
            if relation == _INNER:
                raise OSError(
                    _errno.EINVAL, "Cannot move a directory into itself", str(target)
                )
            if target_stat is not None:
                if relation == _OUTER:
                    raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(target))
                if relation == _OTHER:
                    return _drop_second_name(src, src_stat, target, target_stat)
                if target_stat.is_dir() and not target_stat.is_symlink():
                    if not src_stat.is_dir():
                        raise IsADirectoryError(target)
                    target.rm(recursive=True, missing_ok=True)
                elif (
                    native
                    and type(target) is type(src)
                    and getattr(type(src), "replace", Path.replace) is not Path.replace
                ):
                    # A class with its own replace() (os.replace() swaps atomically and leaves the
                    # target alone on failure). Path.replace() is built on move(): calling it here
                    # would never end.
                    try:
                        return src.replace(target)
                    except OSError as error:
                        if error.errno != _errno.EXDEV:
                            raise
                        native = False
                else:
                    target.unlink(missing_ok=True)

        if native:
            try:
                return src.rename(target)
            except NotImplementedError:
                pass
            except OSError as error:
                # Another filesystem or drive: fall back to copy + delete, as
                # shutil.move and 3.14's Path.move do.
                if error.errno != _errno.EXDEV:
                    raise

        # A link is recreated, not read through, wherever the target can hold one, so
        # the result does not depend on rename() being available. The copy is handed
        # what is known (nothing at `target`, `src` not a link); a failed rename changed nothing.
        links = _holds_links(src, target)
        handing = _handing(
            src,
            target,
            held.source_raw if not src_stat.is_symlink() else None,
            relation != _SAME and target_stat is None,
        )
        if src_stat.is_symlink() and links:
            with handing:
                src.copy(target, overwrite=overwrite, follow_symlinks=False)
            src.unlink()
        elif src.is_dir() if src_stat.is_symlink() else src_stat.is_dir():
            with handing:
                src.copy(
                    target,
                    overwrite=overwrite,
                    recursive=True,
                    follow_symlinks=not links,
                )
            src.rm(recursive=True)
        else:
            with handing:
                src.copy(target, overwrite=overwrite)
            src.unlink()


class _Held:
    """The non-following stats `move()` takes of its source and its target,
    each at most once, for `move()` and `_relation()` to share. It lives for
    one call and is never kept on a path."""

    __slots__ = ("source", "source_raw", "_target", "_target_raw", "_taken")

    def __init__(self, src: Path):
        self.source_raw = self.source = None
        self._target = self._target_raw = None
        self._taken = False
        try:
            self.source_raw = src.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        self.source = FileStat.from_stat(self.source_raw)

    def target(self, path: Path):
        """The `FileStat` of `path` without following a link, None when it is
        missing; an `OSError` is raised again by the next call."""
        if not self._taken:
            try:
                self._target_raw = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                self._target_raw = None
            self._target = (
                None
                if self._target_raw is None
                else FileStat.from_stat(self._target_raw)
            )
            self._taken = True
        return self._target

    def same_file(self, src: Path, target: Path) -> bool:
        """`src.samefile(target)` from the stats in hand: for a class that does
        not answer `samefile()` itself and two paths that are not links (a
        link's own stat is not the file `samefile()` compares)."""
        if type(src).samefile is not Path.samefile or self.source.is_symlink():
            return src.samefile(target)
        there = self.target(target)
        if there is None:
            return False
        if there.is_symlink():
            return src.samefile(target)
        return _same_identity(self.source_raw, self._target_raw)


def _holds_links(src: Path, target: Path) -> bool:
    """Whether a symlink of `src` can be recreated at `target`: `src` reads
    links and the class of `target` implements `_symlink_to()`."""
    return (
        callable(getattr(src, "readlink", None))
        and getattr(type(target), "_symlink_to", None) is not Path._symlink_to
    )


PathLike = _ty.Union[str, Path]
