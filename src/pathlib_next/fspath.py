from __future__ import annotations

import contextvars as _contextvars
import functools as _func
import ntpath as _ntpath
import os as _os
import pathlib as _path
import posixpath as _posixpath
import re as _re
import shutil as _shutil
import stat as _stat
import sys as _sys
import types as _types
import typing as _ty

from . import path as _proto
from . import _permissions
from . import glob as _glob
from ._filestat import FileStat as _FileStat
from .exceptions import NonRelativePatternError

__all__ = ["LocalPath", "PosixPathname", "WindowsPathname"]

# pathlib.Path.stat()/chmod() only accept follow_symlinks= on 3.10+; below
# that, LocalPath (which inherits them directly from pathlib.Path via MRO,
# see class LocalPath below) needs a shim.
_HAS_FOLLOW_SYMLINKS = _sys.version_info >= (3, 10)

#: True while `rglob()` calls `glob()` for itself: the audit event of that
#: inner call belongs to pathlib's `rglob()` only from 3.13 and is raised there.
_IN_RGLOB: "_contextvars.ContextVar[bool]" = _contextvars.ContextVar(
    "pathlib_next_in_rglob", default=False
)


@_func.cache
def _is_case_sensitive(flavour: _os.path) -> bool:
    return flavour.normcase("Aa") == "Aa"


def _translate_segment(part: str, not_sep: str) -> str:
    """One pattern segment to a regex in which `*`/`?` never match `sep`
    (CPython 3.13's `fnmatch._translate(part, not_sep + "*", not_sep)`)."""
    import fnmatch as _fnmatch

    # fnmatch.translate() wraps its output; slice that off a bracket
    # expression's translation to reuse its range/negation handling.
    prefix, suffix = _fnmatch.translate("_").split("_")
    out = []
    i, n = 0, len(part)
    while i < n:
        c = part[i]
        i += 1
        if c == "*":
            if not out or out[-1] != f"{not_sep}*":
                out.append(f"{not_sep}*")
        elif c == "?":
            out.append(not_sep)
        elif c == "[":
            j = i
            if j < n and part[j] == "!":
                j += 1
            if j < n and part[j] == "]":
                j += 1
            while j < n and part[j] != "]":
                j += 1
            if j >= n:
                out.append("\\[")
            else:
                out.append(
                    _fnmatch.translate(part[i - 1 : j + 1])[len(prefix) : -len(suffix)]
                )
                i = j + 1
        else:
            out.append(_re.escape(c))
    return "".join(out)


@_func.lru_cache(maxsize=512)
def _compile_full_match(pattern: str, sep: str, case_sensitive: bool):
    """Compile a `full_match()` pattern string the way CPython 3.13 does:
    `glob.translate(pattern, recursive=True, include_hidden=True, seps=sep)`."""
    esc = _re.escape(sep)
    not_sep = f"[^{esc}]"
    results = []
    parts = pattern.split(sep)
    last = len(parts) - 1
    for idx, part in enumerate(parts):
        if part == "*":
            results.append(f"{not_sep}+{esc}" if idx < last else f"{not_sep}+")
        elif part == "**":
            if idx < last:
                if parts[idx + 1] != "**":
                    results.append(f"(?:.+{esc})?")
            else:
                results.append(".*")
        else:
            if part:
                results.append(_translate_segment(part, not_sep))
            if idx < last:
                results.append(esc)
    flags = 0 if case_sensitive else _re.IGNORECASE
    return _re.compile(f"(?s:{''.join(results)})\\Z", flags).match


class _BaseFSPathname(_path.PurePath, _proto.Pathname):
    __slots__ = ()

    @property
    def _parser(self) -> _os.path:
        try:
            # 3.13+: renamed to `parser`, already a module.
            return self.parser
        except AttributeError:
            pass
        flavour = self._flavour
        if isinstance(flavour, _types.ModuleType):
            # 3.12: `_flavour` is already a module (ntpath/posixpath).
            return flavour
        # 3.9-3.11: `_flavour` is a `_WindowsFlavour`/`_PosixFlavour` object
        # with no `normcase`/`pathsep`/etc — bridge to the equivalent module.
        return _ntpath if flavour.sep == "\\" else _posixpath

    @property
    def _path_separators(self) -> _ty.Sequence[str]:
        parser = self._parser
        return (parser.sep,) + ((parser.altsep,) if parser.altsep else ())

    @property
    def _is_case_sensitive(self) -> bool:
        return _is_case_sensitive(self._parser)

    @property
    def segments(self):
        return self.parts

    def with_segments(self, *args: str | _proto.FsPathLike):
        return type(self)(*args)

    # match()/full_match(): stdlib's own wherever it has the promised signature.
    # `PurePath` follows this class in the MRO, so an unconditional definition would
    # displace stdlib's on versions that have it: hence the version gate.

    if _sys.version_info < (3, 12):

        def match(self, path_pattern, *, case_sensitive=None):
            """Return True if this path matches the given pattern (stdlib's
            `PurePath.match`). `case_sensitive=` is 3.12+ in CPython; this
            shim accepts it on older versions too."""
            if case_sensitive is None:
                return _path.PurePath.match(self, path_pattern)
            return self._match_case(_os.fspath(path_pattern), case_sensitive)

        def _match_case(self, pattern: str, case_sensitive: bool) -> bool:
            # CPython 3.9-3.11's PurePath.match, with the flavour's casefold
            # replaced by the requested sensitivity.
            import fnmatch as _fnmatch

            fold = (lambda s: s) if case_sensitive else str.lower
            pat = self.with_segments(fold(pattern))
            pat_parts = list(pat.parts)
            if not pat_parts:
                raise ValueError("empty pattern")
            if pat.drive and pat.drive != fold(self.drive):
                return False
            if pat.root and pat.root != self.root:
                return False
            parts = [fold(part) for part in self.parts]
            if pat.drive or pat.root:
                if len(pat_parts) != len(parts):
                    return False
                pat_parts = pat_parts[1:]
            elif len(pat_parts) > len(parts):
                return False
            return all(
                _fnmatch.fnmatchcase(part, pat)
                for part, pat in zip(reversed(parts), reversed(pat_parts))
            )

    if _sys.version_info < (3, 13):

        def full_match(self, pattern, *, case_sensitive=None):
            """Return True if this path matches the glob-style `pattern`
            against the whole path, with "**" matching any number of
            segments -- a port of CPython 3.13's `PurePath.full_match`. The
            generic `Pathname.full_match` splits on "/" and never sees a
            drive, a root or a backslash, so rooted and Windows patterns
            failed before 3.13."""
            if not isinstance(pattern, _path.PurePath):
                pattern = self.with_segments(pattern)
            if case_sensitive is None:
                case_sensitive = self._is_case_sensitive
            sep = "\\" if isinstance(pattern, _path.PureWindowsPath) else "/"
            path_str = str(self)
            pattern_str = str(pattern)
            match = _compile_full_match(
                "" if pattern_str == "." else pattern_str, sep, case_sensitive
            )
            return match("" if path_str == "." else path_str) is not None


class PosixPathname(_path.PurePosixPath, _BaseFSPathname):
    """Pure (no I/O) POSIX-flavour path, implementing the `Pathname`
    protocol on top of `pathlib.PurePosixPath`."""

    __slots__ = ()


class WindowsPathname(_path.PureWindowsPath, _BaseFSPathname):
    """Pure (no I/O) Windows-flavour path, implementing the `Pathname`
    protocol on top of `pathlib.PureWindowsPath`."""

    __slots__ = ()


class LocalPath(
    _path.WindowsPath if _os.name == "nt" else _path.PosixPath,
    _proto.Path,
    _BaseFSPathname,
):
    """The real local filesystem path: `pathlib.WindowsPath`/`PosixPath`
    with this library's `Path` mixed in via MRO. Behaves exactly like
    `pathlib.Path` for anything not explicitly overridden here (see
    `docs/divergences.md`).

    A class mixing a concrete stdlib path with `Path` but not this class is
    given every function defined here (`Path.__init_subclass__`), so each one
    names stdlib's implementation explicitly instead of calling `super()`."""

    __slots__ = ()

    def _scandir(self):
        # On 3.11+ stdlib's `_scandir()` (ahead of ours in the MRO) returns raw
        # `os.DirEntry`; walk()/glob() expect `(name, FileStat|None)`, so ours is
        # asserted. The entry's cached lstat (matching walk()'s default) is reused.
        for entry in _os.scandir(self):
            try:
                stat = _FileStat.from_stat(entry.stat(follow_symlinks=False))
            except OSError:
                stat = None
            yield entry.name, stat

    def _rename_compatible(self, target) -> bool:
        if isinstance(target, _path.PurePath):
            return True
        # A URI path for a local file: the local path is its `filepath`.
        try:
            return isinstance(getattr(target, "filepath", None), _path.PurePath)
        except Exception:
            return False

    def is_junction(self) -> bool:
        """Whether this is a Windows junction. `os.path.isjunction()` on
        3.12+, the reparse tag directly before that.

        lstat() reports a junction (IO_REPARSE_TAG_MOUNT_POINT) as a plain
        directory -- CPython only rewrites the mode to S_IFLNK for real
        symlinks -- so a recursive removal must test for it or it would walk
        into one and delete the target's files, as `shutil.rmtree` guards.
        """
        if _os.name != "nt":
            return False
        isjunction = getattr(_os.path, "isjunction", None)
        if isjunction is not None:
            try:
                return bool(isjunction(self))
            except (OSError, ValueError):
                return False
        try:
            st = _os.lstat(self)
        except OSError:
            return False
        return getattr(st, "st_reparse_tag", 0) == getattr(
            _stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
        )

    def is_mount(self) -> bool:
        """Whether this is a mount point -- a bind mount included, which is
        how POSIX spells what a junction does on Windows."""
        try:
            return bool(_os.path.ismount(self))
        except (OSError, ValueError):
            return False

    def walk(self, top_down=True, on_error=None, follow_symlinks=False):
        # 3.12+ stdlib `Path.walk()` would win the MRO, but it uses `self._scandir()` as a
        # context manager (`with scandir_it:`) and ours is a plain generator: route
        # explicitly to our own `walk()`.
        return _proto.Path.walk(
            self, top_down=top_down, on_error=on_error, follow_symlinks=follow_symlinks
        )

    def _symlink_target(self, target):
        # pathlib stores the text it is given: "./t/" stays "./t/". Windows
        # cannot resolve a relative target that spells its separators "/", so
        # there the path class turns the text into its own form.
        if isinstance(target, str) and _os.name == "nt":
            return self.with_segments(target)
        return target

    def _symlink_to(
        self, target: _proto.Path | str, target_is_directory: bool = False
    ) -> None:
        # `symlink_to` is in _OPERATION_NAMES, so the generic `Path.symlink_to()` (which
        # owns `force=`) resolves on LocalPath and delegates link creation here; stdlib's
        # is reached explicitly. target_is_directory is meaningful here (Windows, via os.symlink).
        return _path.Path.symlink_to(self, target, target_is_directory)

    def stat(self, *, follow_symlinks=True):
        # pathlib.Path.stat() (next in MRO via WindowsPath/PosixPath) only
        # accepts follow_symlinks= on 3.10+; below that, lstat() is the
        # equivalent for follow_symlinks=False.
        if _HAS_FOLLOW_SYMLINKS:
            return _path.Path.stat(self, follow_symlinks=follow_symlinks)
        return _path.Path.stat(self) if follow_symlinks else _path.Path.lstat(self)

    def chmod(self, mode: int | str, *, follow_symlinks: bool = True):
        # Same follow_symlinks= 3.10+ gap as stat() above; lchmod() is the
        # equivalent (NotImplementedError itself where os.lchmod is missing).
        mode = _permissions.parse_mode(mode)
        if _HAS_FOLLOW_SYMLINKS:
            return _path.Path.chmod(self, mode, follow_symlinks=follow_symlinks)
        return (
            _path.Path.chmod(self, mode)
            if follow_symlinks
            else _path.Path.lchmod(self, mode)
        )

    if _sys.version_info < (3, 13):
        # `is_dir()`/`is_file()` gained `follow_symlinks=` in 3.13 and the `Stat` protocol
        # promises it. Following keeps stdlib's implementation; the non-following form
        # is 3.13's (any OSError/ValueError is False).

        def is_dir(self, *, follow_symlinks=True):
            if follow_symlinks:
                return _path.Path.is_dir(self)
            try:
                return _stat.S_ISDIR(self.stat(follow_symlinks=False).st_mode)
            except (OSError, ValueError):
                return False

        def is_file(self, *, follow_symlinks=True):
            if follow_symlinks:
                return _path.Path.is_file(self)
            try:
                return _stat.S_ISREG(self.stat(follow_symlinks=False).st_mode)
            except (OSError, ValueError):
                return False

    def _chown(
        self,
        uid: int | str | None,
        gid: int | str | None,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        # shutil.chown() accepts names as well as ids and takes None for "leave unchanged",
        # the canonical form Chmod.chown() normalizes to; os.chown's -1 never appears here.
        if not hasattr(_os, "chown"):
            # shutil.chown() exists on Windows while os.chown does not, and there is no `pwd`
            # for it to consult, so an int would leak AttributeError and a name LookupError:
            # docs/divergences.md promises NotImplementedError.
            raise NotImplementedError("chown()")
        if not follow_symlinks:
            if not hasattr(_os, "lchown"):
                raise NotImplementedError("chown(follow_symlinks=False)")
            if isinstance(uid, str) or isinstance(gid, str):
                # os.lchown takes numeric ids only; resolving a name would
                # mean duplicating shutil's lookup, and guessing wrong here
                # writes the wrong owner silently.
                raise NotImplementedError(
                    "chown(follow_symlinks=False) requires numeric uid/gid"
                )
            return _os.lchown(
                self, -1 if uid is None else uid, -1 if gid is None else gid
            )
        return _shutil.chown(self, uid, gid)

    def _utime(
        self, *, atime: "float | None" = None, mtime: "float | None" = None
    ) -> None:
        # `os.utime` takes both times together: the one left alone is read
        # from the file as it is.
        if atime is None or mtime is None:
            current = _os.stat(self)
            atime = current.st_atime if atime is None else atime
            mtime = current.st_mtime if mtime is None else mtime
        _os.utime(self, times=(atime, mtime))

    def glob(
        self,
        pattern: "str | _proto.FsPathLike | None",
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

        Same semantics as Path.glob(), including `pattern=None` (expand the
        pattern this path carries) and `native=` (follow the running
        interpreter, or one rule on every version); every separator of this
        flavour splits the pattern, and a pattern with a drive or root
        raises `NonRelativePatternError` like pathlib.

        Raises the audit event `pathlib.Path.glob` with `(self, pattern)`, as
        pathlib does. `pattern=None` has no pathlib counterpart and raises none.
        """
        if pattern is not None and not _IN_RGLOB.get():
            _sys.audit("pathlib.Path.glob", self, pattern)
        if pattern is None:
            return _proto.Path.glob(
                self,
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
        pattern = _os.fspath(pattern)
        if pattern:
            anchored = self.with_segments(pattern)
            if anchored.drive or anchored.root:
                raise NonRelativePatternError("Non-relative patterns are unsupported")
            for sep in self._path_separators:
                pattern = pattern.replace(sep, "/")
            if any(self._parser.splitdrive(part)[0] for part in pattern.split("/")):
                # "sub/C:/x": joining "C:" would re-anchor outside self, and
                # no child can be named that, so nothing matches (pathlib).
                return iter(())
        return _proto.Path.glob(
            self,
            pattern,
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            recursive=recursive,
            dironly=dironly,
            recurse_symlinks=recurse_symlinks,
            native=native,
            on_error=on_error,
            bound_loops=bound_loops,
        )

    def rglob(
        self,
        pattern: "str | _proto.FsPathLike | None",
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
        """Equivalent to `glob(f"**/{pattern}", recursive=True)`, as
        `Path.rglob()`; raises the audit event `pathlib.Path.rglob` with
        `(self, pattern)`, and from 3.13 the `pathlib.Path.glob` event of the
        `glob()` it stands for, as pathlib does. `pattern=None` raises none."""
        if pattern is None:
            return _proto.Path.rglob(
                self,
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
        _sys.audit("pathlib.Path.rglob", self, pattern)
        if _sys.version_info >= (3, 13):
            _sys.audit(
                "pathlib.Path.glob", self, self._parser.join("**", _os.fspath(pattern))
            )
        token = _IN_RGLOB.set(True)
        try:
            return _proto.Path.rglob(
                self,
                pattern,
                case_sensitive=case_sensitive,
                include_hidden=include_hidden,
                recursive=recursive,
                dironly=dironly,
                recurse_symlinks=recurse_symlinks,
                native=native,
                on_error=on_error,
                bound_loops=bound_loops,
            )
        finally:
            _IN_RGLOB.reset(token)
