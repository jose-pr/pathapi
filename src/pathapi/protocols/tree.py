"""Listing a directory and removing what is in it."""

from __future__ import annotations

import errno as _errno
import typing as _ty

from .. import _errorpolicy, _names, _stubs
from .. import glob as _glob
from .._errorpolicy import check_follow
from .._filestat import FileStat
from ..exceptions import NonRelativePatternError
from ..glob import _entries
from ._errors import _os_error
from ._removal import _InvalidPolicy, _follow_policy, _is_junction, _listing
from .fs import Stat

__all__ = ["Listing", "Remove"]


class Listing(Stat, _ty.Protocol):
    """Listing the entries of a directory: `iterdir()` is the primitive;
    `_scandir()`, `glob()`, `rglob()`, `walk()` and iteration derive from it."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("iterdir",)

    def __iter__(self):
        return self.iterdir()

    @_stubs.primitive
    def iterdir(self) -> "_ty.Iterator[_ty.Self]":
        """Yield path objects of the directory contents.

        The children are yielded in arbitrary order, and the
        special entries '.' and '..' are not included.
        """
        return _stubs._unimplemented("iterdir")

    def _scandir(self) -> "_ty.Iterator[_ty.Tuple[str, _ty.Optional[FileStat]]]":
        """Yield (name, stat_or_None) for each directory entry. Used by
        `walk()`/`glob()` instead of `iterdir()` so schemes whose listing
        call already returns metadata (an HTTP index, SFTP, FTP, an object
        store) can answer `is_dir()` on the results without a further
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


class Remove(Listing, _ty.Protocol):
    """Removing a file, an empty directory or a whole tree: `unlink()` and
    `rmdir()` are the primitives; `rm()` derives from them and from
    `_scandir()`."""

    __slots__ = ()

    __primitives__: _ty.ClassVar[tuple[object, ...]] = ("unlink", "rmdir")

    @_stubs.primitive
    def unlink(self, missing_ok=False):
        """
        Remove this file or link.
        If the path is a directory, use rmdir() instead.
        """
        return _stubs._unimplemented("unlink")

    @_stubs.primitive
    def rmdir(self):
        """
        Remove this directory.  The directory must be empty.
        """
        return _stubs._unimplemented("rmdir")

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
