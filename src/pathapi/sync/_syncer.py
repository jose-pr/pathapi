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
from ._compare import _default_checksum
from ._entry import _Entry
from ._events import PathAndStat, SyncEvent
from ._links import _as_entry
from ._offer import _offer_error
from ._replace import _LIVE_TEMPS, _STALE_AFTER, _is_leftover

_logger = _logging.getLogger("pathapi.sync")


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

    The temporary sibling is `.NAME.<12 hex digits>.pathapi-tmp`, NAME
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
    `pathapi.sync` logger and reported to `hook` as `SyncEvent.Error`.

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
        return _Entry(
            self, source, target, dry_run, _ignore_error, root, swept, listings
        ).run()
