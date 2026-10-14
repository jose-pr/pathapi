"""`PathSyncer`: make one tree of paths match another."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import logging as _logging
import time as _time
import typing as _ty

from .. import _errorpolicy, _names
from ..path import Path
from ..protocols import Utime
from ..protocols._bundles import Readable
from ..protocols import supports as _supports
from ..protocols._removal import _listing, _write_batch_of
from .._filestat import FileStat
from ..glob import _entries
from ._compare import (
    _default_checksum,
    _default_checksums_match,
    _is_local,
    _quick_check_in_sync,
)
from ._events import PathAndStat, SyncEvent
from ._links import (
    _as_entry,
    _implements,
    _link_is_directory,
    _link_text,
    _same_link,
    _supports_symlinks,
    _unresolved_link,
)
from ._offer import _offer_error
from ._paths import _is_junction, _is_mount, _paths_overlap
from ._replace import (
    _LIVE_TEMPS,
    _ONLY_COPY_ATTR,
    _STALE_AFTER,
    _is_leftover,
    _only_copy,
    _replace,
    _temp_sibling,
)

_logger = _logging.getLogger("pathlib_next.sync")


class _OnPathSyncerError(_ty.Protocol):
    def __call__(
        self,
        error: Exception,
        source: PathAndStat,
        target: PathAndStat,
        event: SyncEvent,
    ) -> bool: ...


class PathSyncer(object):
    """One-way checksum-driven tree sync: copies/creates in `target`
    whatever differs from `source` (by `checksum`), optionally removing
    files in `target` that are missing from `source`. Works across any two
    `Path` implementations (e.g. `MemPath` -> `LocalPath`, or between two
    remote backends) -- see `sync()`.

    The default `checksum` policy prefers each side's backend-native
    digest (`protocols.checksum.NativeChecksum.checksum()`, e.g.
    an SFTP path's `check-file-handle` support) over streaming the file
    through `open("rb")`, but only when BOTH sides can produce a digest
    under the same algorithm -- native or streamed. If either side can't
    (missing the protocol, or it raises `NotImplementedError` for the
    requested algorithm), both sides fall back to streaming rather than
    comparing a native digest to a streamed one. A custom `checksum`
    callable disables this native-preferring behavior entirely (it is
    called exactly as before, once per side, compared with `==`).

    `quick_check=True` (the default) adds a cheap metadata-only
    pre-check -- the classic rsync "quick check" heuristic -- for any pair
    where at least one side is non-local (`is_local()`; a side without
    an `is_local()` method at all, e.g. plain `LocalPath`/`MemPath`, is
    treated as local): if `st_size` AND `st_mtime` already match (from the
    listing/stat metadata `PathAndStat` already carries -- no extra round
    trip), the pair is treated as in sync WITHOUT calling `checksum` at
    all, native or streamed. Times match at the resolution of the coarser
    side: in whole seconds when either has no fractional part (SFTP, FTP and
    the object stores report seconds), to a microsecond otherwise. A mismatch
    on either falls through to a real
    checksum comparison rather than being treated as "changed" -- mtime can
    be unreliable across backends/clock skew, so a false "needs copy" from
    a mismatch is merely wasteful, while a false "in sync" would be a
    correctness regression. An `st_mtime` of 0 on either side means
    "unknown" and never matches. Local-to-local pairs always skip this
    pre-check (local reads are already cheap, and
    `copy(preserve_metadata=True)` doesn't
    guarantee mtime propagation on every path, see `docs/divergences.md`).
    Set `quick_check=False` to disable the pre-check entirely and always
    checksum.

    `preserve_mtime=True` (the default) gives each file the sync copies the
    modification time the source reported, where the target's class can set one
    (`LocalPath`, `MemPath`, and `FTPPath` on a server that lists `MFMT`;
    a class of another distribution says so through `_utime()`), so that the next run's quick check can match. A source
    that reports no time (0), and a target that cannot set one, leave the
    time the copy gave the file; neither is an error. Directories and links are
    not touched. `preserve_mtime=False` leaves every copy with the time it was
    written at.

    `follow_symlinks` (default `True`) controls whether a symlink source is
    resolved during traversal (content synced as if it weren't a link) or
    reported as a symlink (`is_symlink()` true). When it's `False` and a
    symlink source is reached, `symlink_mode` decides what happens:
    `"preserve"` (default) creates a matching symlink on `target` with the
    same raw, unresolved target string `readlink()` returned (dangling
    links and relative targets included -- never resolved/validated);
    `"reject"` raises `NotImplementedError` instead. If `target` can't
    create symlinks at all
    (most backends -- `LocalPath` and the SFTP backend of another distribution
    implement `symlink_to()`), `"preserve"` mode raises `NotImplementedError` too,
    through the same `ignore_error`/`hook()` machinery as every other
    branch -- decided before an existing target entry is touched, and by a
    dry run as by a real one. Replacing
    an existing entry creates the new link under a temporary sibling name
    first, so a runtime refusal also leaves the entry in place. A target
    link that already has the same raw target string (and, on a Windows
    target, the same file/directory link kind) is left alone. A directory
    link is created as one (`target_is_directory`), which Windows requires.
    A symlink or a Windows junction found inside the target, below its
    root, is replaced by a real directory and never listed, written or
    deleted through. A mount point there (a mounted volume, a bind mount) is
    part of the tree and is synced into, as `rsync` does. With
    `follow_mounts=False` it is an error for that entry instead:
    `OSError(EXDEV)` naming it, offered to `ignore_error` with event
    `SyncEvent.TypeMismatch`, and nothing behind it is listed, written or
    removed, in a dry run as in a real one. The root target is used as given
    either way.

    `remove_missing=False` never deletes a non-empty target directory: when
    the source entry became a file or a symlink, replacing that directory
    raises `IsADirectoryError` through `ignore_error` (event
    `SyncEvent.TypeMismatch`) and the directory is left untouched. With
    `remove_missing=True`, or for an empty directory, it is replaced.

    A changed file is written to a temporary sibling and renamed over the
    existing target where the target backend implements `rename()`, so a
    failed or interrupted transfer keeps the previous version. A backend
    without `rename()` is overwritten in place, with the source opened
    before the target is truncated. A source entry that is not a regular
    file, directory or symlink (FIFO, socket, device) is skipped with a
    `SyncEvent.Skipped` event and its target left untouched.

    The temporary sibling is `.NAME.<12 hex digits>.pathlib-next-tmp`, NAME
    cut so that the whole stays within 255 bytes. A backend whose `rename()`
    refuses an existing target has the old file removed first; if the second
    rename then fails, the new version stays under its temporary name and
    the error names it. A name of exactly that form is the library's own: it
    is never a sync source and never removed as "missing". A stale one is
    removed from a target directory when a changed file or link in it is
    next written (and by `remove_missing=True`): stale means no sync of this
    process is writing it and its modification time is known and over a day
    old. A younger one, or one whose age the store does not report, stays.

    Errors: `ignore_error` is consulted once per error, with the
    `PathAndStat` entries that failed and the event that failed
    (`SyncEvent.Compare` for a failing quick check or checksum). A name the
    source directory lists is never "missing": a link whose target cannot be
    resolved (dangling, a loop, an unmounted volume) is an error for that
    entry (event `SyncEvent.SyncStart`), and the same-named target entry is
    left as it is, whatever `remove_missing` is. An error it declines propagates
    without being offered again by enclosing directories. A dry run takes
    the same decisions as a real run (a directory that would be created is
    treated as empty) without changing anything. Every error the policy
    tolerates is logged at WARNING (with the traceback) on the
    `pathlib_next.sync` logger and reported to `hook` as `SyncEvent.Error`.

    `hook` receives `PathAndStat` entries for every event, and the `dry_run`
    value of the call for every event, including the structural ones
    (`SyncStart`, `CheckTargetChild(ren)`, `SyncChild(ren)`).
    """

    __slots__ = (
        "checksum",
        "_hook",
        "remove_missing",
        "follow_symlinks",
        "symlink_mode",
        "ignore_error",
        "quick_check",
        "preserve_mtime",
        "follow_mounts",
    )
    EVENT_LOG_FORMAT = "[%s] Source:%s Target:%s DryRun:%s"

    def __init__(
        self,
        checksum: _ty.Callable[[PathAndStat], _ty.Any] | None = None,
        /,
        remove_missing: bool = False,
        follow_symlinks: bool = True,
        symlink_mode: '_ty.Literal["preserve", "reject"]' = "preserve",
        hook: _ty.Callable[[PathAndStat, PathAndStat, SyncEvent, bool], None] = None,
        ignore_error: _OnPathSyncerError | bool = False,
        quick_check: bool = True,
        preserve_mtime: bool = True,
        follow_mounts: bool = True,
    ) -> None:
        # `None` resolves to `_default_checksum`, which `sync()` recognizes (via `is`) to
        # decide native versus streaming across BOTH sides at once. A caller-supplied
        # callable is used as-is: `checksum(target) == checksum(source)`.
        if checksum is None:
            checksum = _default_checksum
        self.checksum = checksum
        self.remove_missing = remove_missing
        self._hook = hook
        self.follow_symlinks = follow_symlinks
        if symlink_mode not in ("preserve", "reject"):
            raise ValueError(
                f"symlink_mode must be 'preserve' or 'reject', got {symlink_mode!r}"
            )
        self.symlink_mode = symlink_mode
        self.ignore_error = _ty.cast(
            _OnPathSyncerError, _errorpolicy.as_error_handler(ignore_error)
        )
        self.quick_check = quick_check
        self.preserve_mtime = preserve_mtime
        self.follow_mounts = follow_mounts

    def log(self, msg: str, *args: object):
        # Overridable hook: a subclass routes sync progress elsewhere (the class has
        # `__slots__`, so an instance cannot be assigned one). `*args` go to the logger
        # lazily, so formatting is skipped unless something listens at INFO.
        _logger.info(msg, *args)

    def hook(
        self,
        source: PathAndStat,
        target: PathAndStat,
        event: SyncEvent,
        dry_run: bool,
        do: _ty.Callable[[], None] = None,
        ignore_error: _OnPathSyncerError = None,
        *,
        always_run: bool = False,
    ):
        # `ignore_error` passes a per-call policy down; None keeps the instance's.
        # `always_run` runs `do` in a dry run too (the traversal steps, which only read
        # and recurse) while still reporting `dry_run`.
        if ignore_error is None:
            ignore_error = self.ignore_error
        if do and (always_run or not dry_run):
            try:
                do()
            except Exception as e:
                if self._tolerate(ignore_error, e, source, target, event, dry_run):
                    return e
                raise
        if self._hook:
            self._hook(source, target, event, dry_run)
        self.log(self.EVENT_LOG_FORMAT, event, source, target, dry_run)

    def _copy_mtime(self, source: PathAndStat, target: Path) -> None:
        """Give the file just copied to `target` the modification time the
        source reported, where `target`'s class can set one. Not an error of
        the sync when it cannot: the next run compares the content."""
        stat = source.stat
        mtime = getattr(stat, "st_mtime", 0)
        utime = getattr(target, "_utime", None)
        if not (
            self.preserve_mtime
            and utime is not None
            and mtime
            and getattr(stat, "size_known", True)
            and _supports(target, Utime)
        ):
            return
        try:
            utime(mtime=mtime)
        except NotImplementedError:
            pass
        except Exception as error:
            _logger.debug(
                "could not set the modification time of %s: %s", target, error
            )

    def _tolerate(
        self,
        policy: _OnPathSyncerError,
        error: Exception,
        source,
        target,
        event: SyncEvent,
        dry_run: bool,
    ) -> bool:
        """Offer `error` to `policy` once (see `_offer_error`). A tolerated
        error is logged at WARNING and reported to the hook as
        `SyncEvent.Error`, so `ignore_error=True` never loses it silently."""
        source, target = _as_entry(source), _as_entry(target)
        if not _offer_error(policy, error, source, target, event):
            return False
        _logger.warning(
            "[%s] ignored %s: %s Source:%s Target:%s",
            event,
            type(error).__name__,
            error,
            source,
            target,
            exc_info=(type(error), error, error.__traceback__),
        )
        if self._hook:
            self._hook(source, target, SyncEvent.Error, dry_run)
        self.log(self.EVENT_LOG_FORMAT, SyncEvent.Error, source, target, dry_run)
        return True

    def _keeps_directory(
        self, source, target, dry_run: bool, policy: _OnPathSyncerError
    ) -> bool:
        """True when a non-directory source must not replace the target:
        the target is a real, non-empty directory and `remove_missing` is
        False. The refusal is an `IsADirectoryError` offered to `policy`
        (raised if declined); the directory is never touched."""
        if self.remove_missing or target.is_symlink() or not target.is_dir():
            return False
        try:
            if next(iter(target.path.iterdir()), None) is None:
                return False
            raise IsADirectoryError(
                _errno.EISDIR,
                "refusing to replace a non-empty directory with a "
                f"{'symlink' if source.is_symlink() else 'file'} "
                "(remove_missing is False)",
                str(target.path),
            )
        except Exception as error:
            if self._tolerate(
                policy, error, source, target, SyncEvent.TypeMismatch, dry_run
            ):
                return True
            raise

    def _children(
        self,
        entry: PathAndStat,
        windows: bool = False,
        listing: "list | None" = None,
        follow: "bool | None" = None,
    ) -> "list[tuple[str, PathAndStat]]":
        """`(listed name, child)` for each entry of the directory `entry`,
        from `listing` when the caller has already taken it. The child is
        built from the name as listed, described by a stat that follows
        links when `follow` (default: `follow_symlinks`) says so; one that is
        not a safe child name for a destination read with `windows` rules
        gets no stat, and the caller refuses it."""
        if follow is None:
            follow = self.follow_symlinks
        children: "list[tuple[str, PathAndStat]]" = []
        for name, stat in _listing(entry.path) if listing is None else listing:
            child = _entries._child(entry.path, name)
            if not _names.is_safe_child_name(name, windows=windows):
                children.append((name, PathAndStat.from_stat(child, None)))
            elif _is_leftover(name):
                # The library's own temporary file, not an entry of the
                # tree: never copied, never a reason to remove anything.
                # `_sweep()` removes the stale ones.
                continue
            elif not follow:
                # `None` means "stat unknown" (FTP's NLST fallback), not "missing": ask the path
                # before remove_missing can treat the entry as gone. So does a stat that knows
                # the type and not the size (GitLab blobs, an HTTP index without sizes).
                if stat is None or not getattr(stat, "size_known", True):
                    stat = FileStat.from_path(child, follow_symlink=False)
                children.append((name, PathAndStat.from_stat(child, stat)))
            else:
                children.append((name, self._followed(stat, child)))
        return children

    def _sweep(
        self,
        source_dir: Path,
        target_dir: Path,
        dry_run: bool,
        policy: _OnPathSyncerError,
        swept: "set[str]",
        listings: "dict[str, list]",
    ) -> None:
        """Remove the stale temporary files of earlier syncs from
        `target_dir`, once per call of `sync()`. Only a file whose name is
        the library's own pattern, that no sync of this process is writing
        and that has not been modified for `_STALE_AFTER` seconds goes: a
        younger one may belong to a transfer running now, in another
        process, and a store that reports no modification time never
        qualifies. Best effort: a listing or removal that fails is logged,
        not raised."""
        if str(target_dir) in swept:
            return
        swept.add(str(target_dir))
        # The directory's own `_sync` listed it already when it is a
        # target directory being synced; a file's parent is listed here.
        listing = listings.get(str(target_dir))
        if listing is None:
            try:
                listing = list(_listing(target_dir))
            except Exception as error:
                _logger.warning(
                    "could not list %s for stale temporary files: %s",
                    target_dir,
                    error,
                )
                return
        for name, stat in listing:
            if not (_is_leftover(name) and _names.is_safe_child_name(name)):
                continue
            child = _entries._child(target_dir, name)
            if stat is None:
                try:
                    stat = FileStat.from_path(child, follow_symlink=False)
                except OSError:
                    continue
            if (
                stat is None
                or stat.is_dir()
                or str(child) in _LIVE_TEMPS
                or not stat.st_mtime
                or _time.time() - stat.st_mtime <= _STALE_AFTER
            ):
                continue
            if not dry_run:
                try:
                    child.unlink()
                except Exception as error:
                    _logger.warning("could not remove stale %s: %s", child, error)
                    continue
            self.hook(
                PathAndStat.from_stat(_entries._child(source_dir, name), None),
                PathAndStat.from_stat(child, stat),
                SyncEvent.RemovedMissing,
                dry_run,
                None,
                policy,
            )

    def _followed(self, listed: "FileStat | None", child: Path) -> PathAndStat:
        """`child` described by the stat that following links asks for. The
        listing's own stat answers for anything that is not a link; `None`
        (unknown, never "missing") and a link need the stat of what they
        resolve to. A listed link that cannot be followed (a loop, an offline
        volume) gets no stat, like a dangling one, so that `_sync` reports
        that entry alone instead of the whole listing failing."""
        try:
            if listed is not None:
                listed = FileStat.from_stat(listed)
                if listed.size_known and not listed.is_symlink():
                    return PathAndStat.from_stat(child, listed)
            return PathAndStat(child, follow_symlink=True)
        except FileNotFoundError:
            return PathAndStat.from_stat(child, None)
        except OSError:
            if FileStat.from_path(child, follow_symlink=False) is None:
                raise
            return PathAndStat.from_stat(child, None)

    def sync(
        self,
        source: "Readable | PathAndStat",
        target: Path | PathAndStat,
        /,
        dry_run: bool = False,
        ignore_error: _OnPathSyncerError | bool | None = None,
    ):
        """Sync `source` onto `target`.

        `ignore_error` overrides the instance-level policy for this call
        only. It accepts a bool or a callable with the same
        `(error, source, target, event)` arity as the constructor's; `None`
        (the default) means "use the policy given to `__init__`".

        The root call is checked before anything is touched: a `source`
        that does not exist raises `FileNotFoundError`, and a `source` and
        `target` of the same implementation where one contains the other
        raise `ValueError`. Below the root, only a name the source listing
        no longer holds takes the `remove_missing` path; a listed link that
        cannot be resolved is an error for its entry. All go through
        `ignore_error`; a tolerated root error ends the call without
        changes, a tolerated entry error skips that entry. Child names that
        would not stay a single component inside `target` (`..`, a
        separator, drive, trailing dot or device name on a Windows target)
        raise `ValueError` the same way, decided on the name as the source
        or the target listed it, and symlinks found inside `target` are
        replaced, never written, listed or deleted through. So is a Windows
        junction below the root, which is another directory's second name:
        `rmdir()` removes the junction and nothing behind it. A mount point
        is part of the tree and is synced into. The root `target` the
        caller named is used as given.
        """
        _ignore_error = (
            self.ignore_error
            if ignore_error is None
            else _errorpolicy.as_error_handler(ignore_error)
        )
        root = target.path if isinstance(target, PathAndStat) else target
        # One commit for the whole tree where the target pays for each write;
        # a dry run writes nothing.
        with _contextlib.nullcontext() if dry_run else _write_batch_of(root):
            return self._sync(source, target, dry_run, _ignore_error, True, set(), {})

    def _sync(
        self,
        source: "Readable | PathAndStat",
        target: Path | PathAndStat,
        dry_run: bool,
        _ignore_error: _OnPathSyncerError,
        root: bool,
        swept: "set[str]",
        listings: "dict[str, list]",
    ):
        checksum = self.checksum

        def start():
            nonlocal source, target
            source = (
                PathAndStat(source, follow_symlink=self.follow_symlinks)
                if not isinstance(source, PathAndStat)
                else source
            )
            # `follow_symlinks` describes the SOURCE traversal. Below the root a target entry
            # is always lstat'd, so a link inside the destination is replaced, not followed
            # out of it; the root target is the caller's and keeps their setting.
            target = (
                PathAndStat(
                    target, follow_symlink=self.follow_symlinks if root else False
                )
                if not isinstance(target, PathAndStat)
                else target
            )

        # Stat first, then fire SyncStart: the hook is declared to receive
        # PathAndStat entries, and the arguments of a combined call were
        # evaluated before start() replaced the raw paths.
        try:
            start()
        except Exception as error:
            if self._tolerate(
                _ignore_error, error, source, target, SyncEvent.SyncStart, dry_run
            ):
                return
            raise
        self.hook(source, target, SyncEvent.SyncStart, dry_run, None, _ignore_error)

        if root:
            error = None
            if not source.exists():
                # A typo, unmounted share or HTTP 404 must not read as "an
                # empty source" and wipe the target under remove_missing.
                error = FileNotFoundError(
                    _errno.ENOENT, "sync source does not exist", str(source.path)
                )
            elif _paths_overlap(source.path, target.path):
                error = ValueError(
                    f"cannot sync {source.path} onto {target.path}: "
                    "source and target overlap"
                )
            if error is not None:
                if not self._tolerate(
                    _ignore_error, error, source, target, SyncEvent.SyncStart, dry_run
                ):
                    raise error
                return

        if not source.exists():
            # A name the directory lists is never "missing": a link that
            # cannot be resolved is an error for this entry, and the target
            # entry of that name stays as it is.
            unresolved = _unresolved_link(source)
            if unresolved is not None:
                error, link = unresolved
                if not self._tolerate(
                    _ignore_error, error, link, target, SyncEvent.SyncStart, dry_run
                ):
                    raise error
                return
            if self.remove_missing:
                if self.hook(
                    source,
                    target,
                    SyncEvent.RemovedMissing,
                    dry_run,
                    lambda: target.path.rm(recursive=True, missing_ok=True),
                    _ignore_error,
                ):
                    return
        elif source.is_symlink():
            if self.symlink_mode == "reject":
                error = NotImplementedError("symlink sync not implemented yet")
                if not self._tolerate(
                    _ignore_error, error, source, target, SyncEvent.Symlink, dry_run
                ):
                    raise error
                return

            def create_symlink():
                # Raw, unresolved target string, never resolved against
                # source's parent -- a relative target stays relative.
                raw_target = _link_text(source.path)
                # Windows cannot traverse a file-type link to a directory,
                # and os.symlink only guesses the kind when the link target
                # already exists at the destination.
                is_directory = _link_is_directory(source.path)

                if not target.exists() and not target.is_symlink():
                    target.path.symlink_to(raw_target, is_directory)
                    return
                # Type mismatch or a stale link: create the new link under a temporary sibling
                # first, so a refusal at run time (no symlink privilege on Windows, a server
                # permission error) fails before the existing entry is removed.
                temp = _temp_sibling(target.path)
                temp.symlink_to(raw_target, is_directory)
                # Once the old entry is removed the temporary link is the
                # only one: a failure after that keeps it.
                removed = keep = False
                try:
                    if target.is_file() or target.is_symlink():
                        target.path.unlink()
                    else:
                        target.path.rm(recursive=target.is_dir())
                    removed = True
                    try:
                        temp.rename(target.path)
                    except NotImplementedError:
                        target.path.symlink_to(raw_target, is_directory)
                    removed = False
                except BaseException as error:
                    if not removed:
                        raise
                    keep = True
                    kept = _only_copy(error, temp, target.path)
                    if kept is error:
                        raise
                    raise kept from error
                finally:
                    try:
                        if not keep and FileStat.from_path(temp, follow_symlink=False):
                            temp.unlink()
                    except Exception:
                        pass

            # An identical link is left alone: recreating it gave it a new
            # identity, left a window with no entry, and reported a change
            # on every run.
            if not (target.is_symlink() and _same_link(source.path, target.path)):
                # Decided here and not inside create_symlink(), which a dry
                # run never calls: both runs refuse a target that cannot
                # hold links, before an existing entry is touched.
                if not _supports_symlinks(target.path):
                    error = NotImplementedError(
                        f"symlink_to() not supported by {type(target.path).__name__}"
                    )
                    if not self._tolerate(
                        _ignore_error, error, source, target, SyncEvent.Symlink, dry_run
                    ):
                        raise error
                    return
                if self._keeps_directory(source, target, dry_run, _ignore_error):
                    return
                if target.exists() or target.is_symlink():
                    self._sweep(
                        source.path.parent,
                        target.path.parent,
                        dry_run,
                        _ignore_error,
                        swept,
                        listings,
                    )
                if self.hook(
                    source,
                    target,
                    SyncEvent.Symlink,
                    dry_run,
                    create_symlink,
                    _ignore_error,
                ):
                    return
        elif source.is_file():
            synced = False
            if target.is_file():

                def compare():
                    # quick_check: a cheap metadata-only pre-check for non-local pairs (see the class
                    # docstring); a match skips checksumming, a mismatch always falls through to a
                    # real checksum comparison and never concludes `changed` on its own.
                    if (
                        self.quick_check
                        and (not _is_local(source.path) or not _is_local(target.path))
                        and _quick_check_in_sync(source, target)
                    ):
                        return True
                    if checksum is _default_checksum:
                        # Through the paired native-versus-streaming policy (see the class docstring):
                        # only this branch can coordinate "both native or both streamed".
                        return _default_checksums_match(source, target)
                    return checksum(target) == checksum(source)

                # Reported against this file pair, once: outside a hook an
                # error here reached the policy only through every ancestor
                # directory's hooks (and never for a root file pair).
                try:
                    synced = compare()
                except Exception as error:
                    if self._tolerate(
                        _ignore_error, error, source, target, SyncEvent.Compare, dry_run
                    ):
                        return
                    raise
            elif self._keeps_directory(source, target, dry_run, _ignore_error):
                return
            if not synced:

                def copy():
                    if target.is_file() and _implements(target.path, "rename"):
                        # Write the new content beside the target and rename
                        # it over: a failed or interrupted transfer leaves
                        # the previous version in place.
                        temp = _temp_sibling(target.path)
                        _LIVE_TEMPS.add(str(temp))
                        try:
                            source.path.copy(temp)
                            _replace(temp, target.path)
                        except BaseException as error:
                            # Once the previous version is gone the temp
                            # file is the only copy: it stays.
                            if not getattr(error, _ONLY_COPY_ATTR, False):
                                try:
                                    temp.unlink(missing_ok=True)
                                except Exception:
                                    pass
                            raise
                        finally:
                            _LIVE_TEMPS.discard(str(temp))
                    elif target.is_file():
                        # No rename: Path.copy() opens the source before it
                        # truncates the target, so a missing or unreadable
                        # source still leaves the target intact.
                        source.path.copy(target.path, overwrite=True)
                    else:
                        if target.is_symlink():
                            target.path.unlink()
                        elif target.exists():
                            target.path.rm(recursive=target.is_dir())
                        source.path.copy(target.path)
                    self._copy_mtime(source, target.path)

                if target.is_file() and _implements(target.path, "rename"):
                    self._sweep(
                        source.path.parent,
                        target.path.parent,
                        dry_run,
                        _ignore_error,
                        swept,
                        listings,
                    )
                if self.hook(
                    source, target, SyncEvent.Copy, dry_run, copy, _ignore_error
                ):
                    return
        elif source.is_dir():
            # A symlink, junction, FIFO, socket or device inside the destination is replaced
            # by a real directory, as writing or removing through a link would act on its
            # target (unlink() / rmdir() remove the link itself). The root is used as given.
            binding = (
                not root
                and target.is_dir()
                and not target.is_symlink()
                and _is_junction(target.path)
            )
            if (
                not self.follow_mounts
                and not root
                and not binding
                and target.is_dir()
                and not target.is_symlink()
                and _is_mount(target.path)
            ):
                # Another filesystem under this name: what is inside belongs
                # to whoever mounted it, and the caller asked to stay out.
                error = OSError(
                    _errno.EXDEV,
                    "refusing to sync into a mount point (follow_mounts is False)",
                    str(target.path),
                )
                if not self._tolerate(
                    _ignore_error,
                    error,
                    source,
                    target,
                    SyncEvent.TypeMismatch,
                    dry_run,
                ):
                    raise error
                return
            if (
                (target.is_symlink() and not root)
                or binding
                or (target.exists() and not target.is_dir() and not target.is_symlink())
            ):
                if self.hook(
                    source,
                    target,
                    SyncEvent.TypeMismatch,
                    dry_run,
                    target.path.rmdir if binding else target.path.unlink,
                    _ignore_error,
                ):
                    return

                target._stat = None

            # A directory that did not exist (or only now exists, or only would in a dry
            # run) has no children: nothing to list or remove, every child target is missing.
            target_absent = not target.exists()
            if target_absent:
                if self.hook(
                    source,
                    target,
                    SyncEvent.CreatedDirectory,
                    dry_run,
                    lambda: target.path.mkdir(),
                    _ignore_error,
                ):
                    return

            source_children = None
            windows_target = _names.is_windows_flavoured(target.path)
            target_entries_cache = None

            def target_entries():
                # The existing target directory's children, listed once with their own
                # (non-following) stat for the removal check, the stat of each synced child and
                # the sweep. A failure is raised to the first caller only.
                nonlocal target_entries_cache
                if target_entries_cache is None:
                    target_entries_cache = ()
                    listing = list(_listing(target.path))
                    listings[str(target.path)] = listing
                    target_entries_cache = self._children(
                        target, windows_target, listing, follow=False
                    )
                return target_entries_cache

            def unsafe_name(name, source_entry, target_entry, event):
                # A listed name that is not a single component inside `target` (`..`, or a
                # backslash or colon on a Windows target) is never joined onto it; it is
                # reported, not silently skipped.
                if _names.is_safe_child_name(name, windows=windows_target):
                    return False
                error = ValueError(
                    f"refusing unsafe child name {name!r} under {target.path}"
                )
                if not self._tolerate(
                    _ignore_error, error, source_entry, target_entry, event, dry_run
                ):
                    raise error
                return True

            def get_source_children():
                nonlocal source_children
                if source_children is None:
                    source_children = self._children(source, windows_target)
                return source_children

            if self.remove_missing and not target_absent:

                def checkchildren():
                    entries = target_entries()
                    self._sweep(
                        source.path,
                        target.path,
                        dry_run,
                        _ignore_error,
                        swept,
                        listings,
                    )
                    source_names = {name for name, _ in get_source_children()}
                    for name, child in entries:

                        def checkchild():
                            if unsafe_name(
                                name,
                                source,
                                child,
                                SyncEvent.RemovedMissing,
                            ):
                                return
                            if name not in source_names:
                                # The event describes the entry removed, not
                                # the directory being checked.
                                self.hook(
                                    PathAndStat.from_stat(
                                        _entries._child(source.path, name), None
                                    ),
                                    child,
                                    SyncEvent.RemovedMissing,
                                    dry_run,
                                    lambda child=child: child.path.rm(recursive=True),
                                    _ignore_error,
                                )

                        self.hook(
                            source,
                            target,
                            SyncEvent.CheckTargetChild,
                            dry_run,
                            checkchild,
                            _ignore_error,
                            always_run=True,
                        )

                self.hook(
                    source,
                    target,
                    SyncEvent.CheckTargetChildren,
                    dry_run,
                    checkchildren,
                    _ignore_error,
                    always_run=True,
                )

            def target_child(name, listed):
                # A name the listing did not report is stat'd by `start()`,
                # not assumed missing: a listing can omit an entry, and a
                # case-insensitive target answers to another spelling.
                path = _entries._child(target.path, name)
                if target_absent:
                    return PathAndStat.from_stat(path, None)
                return listed.get(name, path)

            def sync_children():
                try:
                    listed = {}
                    if not target_absent:
                        try:
                            listed = dict(target_entries())
                        except Exception as error:
                            # Each child is stat'd on its own and reports
                            # its own failure.
                            _logger.debug("could not list %s: %s", target.path, error)
                    for name, child in get_source_children():
                        if unsafe_name(name, child, target, SyncEvent.SyncChild):
                            continue
                        self.hook(
                            source,
                            target,
                            SyncEvent.SyncChild,
                            dry_run,
                            # Propagate the resolved policy into the recursive
                            # call so a per-call override applies to the whole
                            # subtree, not just this level.
                            lambda child=child, name=name: self._sync(
                                child,
                                target_child(name, listed),
                                dry_run,
                                _ignore_error,
                                False,
                                swept,
                                listings,
                            ),
                            _ignore_error,
                            always_run=True,
                        )
                finally:
                    listings.pop(str(target.path), None)

            self.hook(
                source,
                target,
                SyncEvent.SyncChildren,
                dry_run,
                sync_children,
                _ignore_error,
                always_run=True,
            )
        else:
            # Not a file, directory or symlink: a FIFO, socket or device (or a stat without
            # a file type). Nothing sensible can be copied; treating it as a directory
            # would replace a same-named target file with an empty directory.
            self.hook(source, target, SyncEvent.Skipped, dry_run, None, _ignore_error)
            return

        self.hook(source, target, SyncEvent.Synced, dry_run, None, _ignore_error)
