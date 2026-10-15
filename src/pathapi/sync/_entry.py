"""One source/target pair of a sync: the steps `PathSyncer._sync()` takes for it."""

from __future__ import annotations

import errno as _errno
import logging as _logging
import typing as _ty

from .. import _names
from .._filestat import FileStat
from ..glob import _entries
from ..path import Path
from ..protocols import Copy, Move
from ..protocols import supports as _supports
from ..protocols._removal import _listing
from ._compare import (
    _default_checksum,
    _default_checksums_match,
    _is_local,
    _quick_check_in_sync,
)
from ._events import PathAndStat, SyncEvent
from ._links import (
    _link_is_directory,
    _link_text,
    _same_link,
    _supports_symlinks,
    _unresolved_link,
)
from ._paths import _is_junction, _is_mount, _paths_overlap
from ._replace import (
    _LIVE_TEMPS,
    _ONLY_COPY_ATTR,
    _only_copy,
    _replace,
    _temp_sibling,
)

if _ty.TYPE_CHECKING:
    from ._syncer import PathSyncer

_logger = _logging.getLogger("pathapi.sync")


class _Entry:
    """The sync of one `source` onto one `target`, for the length of one call
    of `PathSyncer._sync()`. `policy` is the resolved `ignore_error`; `swept`
    and `listings` are shared by the whole `sync()` call."""

    __slots__ = (
        "syncer",
        "source",
        "target",
        "dry_run",
        "policy",
        "root",
        "swept",
        "listings",
        "_absent",
        "_windows",
        "_source_children_cache",
        "_target_entries_cache",
    )

    def __init__(
        self,
        syncer: "PathSyncer",
        source,
        target,
        dry_run,
        policy,
        root,
        swept,
        listings,
    ):
        self.syncer = syncer
        self.source = source
        self.target = target
        self.dry_run = dry_run
        self.policy = policy
        self.root = root
        self.swept = swept
        self.listings = listings

    def _event(self, event: SyncEvent, do=None, *, always_run: bool = False):
        """Report `event` for this pair, running `do` first; what the hook
        returns (an error it tolerated, which ends the pair)."""
        return self.syncer.hook(
            self.source,
            self.target,
            event,
            self.dry_run,
            do,
            self.policy,
            always_run=always_run,
        )

    def _tolerated(self, error, event: SyncEvent, source=None) -> bool:
        """Whether the policy tolerates `error` for this pair."""
        return self.syncer._tolerate(
            self.policy,
            error,
            self.source if source is None else source,
            self.target,
            event,
            self.dry_run,
        )

    def _sweep_parents(self) -> None:
        self.syncer._sweep(
            self.source.path.parent,
            self.target.path.parent,
            self.dry_run,
            self.policy,
            self.swept,
            self.listings,
        )

    def run(self):
        # Stat first, then fire SyncStart: the hook is declared to receive
        # PathAndStat entries, and the arguments of a combined call were
        # evaluated before start() replaced the raw paths.
        try:
            self._start()
        except Exception as error:
            if self._tolerated(error, SyncEvent.SyncStart):
                return
            raise
        self._event(SyncEvent.SyncStart)

        if self.root and self._refuses_root():
            return

        source = self.source
        if not source.exists():
            finished = self._missing()
        elif source.is_symlink():
            finished = self._symlink()
        elif source.is_file():
            finished = self._file()
        elif source.is_dir():
            finished = self._directory()
        else:
            # Not a file, directory or symlink: a FIFO, socket or device (or a stat without
            # a file type). Nothing sensible can be copied; treating it as a directory
            # would replace a same-named target file with an empty directory.
            self._event(SyncEvent.Skipped)
            return
        if not finished:
            self._event(SyncEvent.Synced)

    def _start(self) -> None:
        source, target = self.source, self.target
        self.source = source = (
            PathAndStat(source, follow_symlink=self.syncer.follow_symlinks)
            if not isinstance(source, PathAndStat)
            else source
        )
        # `follow_symlinks` describes the SOURCE traversal. Below the root a target entry
        # is always lstat'd, so a link inside the destination is replaced, not followed
        # out of it; the root target is the caller's and keeps their setting.
        self.target = (
            PathAndStat(
                target,
                follow_symlink=self.syncer.follow_symlinks if self.root else False,
            )
            if not isinstance(target, PathAndStat)
            else target
        )

    def _refuses_root(self) -> bool:
        """Whether the root pair is refused (and the refusal tolerated)."""
        source, target = self.source, self.target
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
        if error is None:
            return False
        if not self._tolerated(error, SyncEvent.SyncStart):
            raise error
        return True

    def _missing(self) -> bool:
        """A source that does not exist. True when the pair is done."""
        # A name the directory lists is never "missing": a link that
        # cannot be resolved is an error for this entry, and the target
        # entry of that name stays as it is.
        unresolved = _unresolved_link(self.source)
        if unresolved is not None:
            error, link = unresolved
            if not self._tolerated(error, SyncEvent.SyncStart, link):
                raise error
            return True
        if self.syncer.remove_missing:
            target = self.target
            if self._event(
                SyncEvent.RemovedMissing,
                lambda: target.path.rm(recursive=True, missing_ok=True),
            ):
                return True
        return False

    def _symlink(self) -> bool:
        """A source that is a symlink. True when the pair is done."""
        source, target, syncer = self.source, self.target, self.syncer
        if syncer.symlink_mode == "reject":
            error = NotImplementedError("symlink sync not implemented yet")
            if not self._tolerated(error, SyncEvent.Symlink):
                raise error
            return True

        # An identical link is left alone: recreating it gave it a new
        # identity, left a window with no entry, and reported a change
        # on every run.
        if target.is_symlink() and _same_link(source.path, target.path):
            return False
        # Decided here and not inside create_symlink(), which a dry
        # run never calls: both runs refuse a target that cannot
        # hold links, before an existing entry is touched.
        if not _supports_symlinks(target.path):
            error = NotImplementedError(
                f"symlink_to() not supported by {type(target.path).__name__}"
            )
            if not self._tolerated(error, SyncEvent.Symlink):
                raise error
            return True
        if syncer._keeps_directory(source, target, self.dry_run, self.policy):
            return True
        if target.exists() or target.is_symlink():
            self._sweep_parents()
        return bool(self._event(SyncEvent.Symlink, self._create_symlink))

    def _create_symlink(self) -> None:
        source, target = self.source, self.target
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

    def _file(self) -> bool:
        """A source that is a regular file. True when the pair is done."""
        source, target, syncer = self.source, self.target, self.syncer
        synced = False
        if target.is_file():
            # Reported against this file pair, once: outside a hook an
            # error here reached the policy only through every ancestor
            # directory's hooks (and never for a root file pair).
            try:
                synced = self._same_content()
            except Exception as error:
                if self._tolerated(error, SyncEvent.Compare):
                    return True
                raise
        elif syncer._keeps_directory(source, target, self.dry_run, self.policy):
            return True
        if not synced:
            if target.is_file() and _supports(target.path, Move):
                self._sweep_parents()
            if self._event(SyncEvent.Copy, self._copy_file):
                return True
        return False

    def _same_content(self) -> bool:
        source, target = self.source, self.target
        # quick_check: a cheap metadata-only pre-check for non-local pairs (see the class
        # docstring); a match skips checksumming, a mismatch always falls through to a
        # real checksum comparison and never concludes `changed` on its own.
        if (
            self.syncer.quick_check
            and (not _is_local(source.path) or not _is_local(target.path))
            and _quick_check_in_sync(source, target)
        ):
            return True
        checksum = self.syncer.checksum
        if checksum is _default_checksum:
            # Through the paired native-versus-streaming policy (see the class docstring):
            # only this branch can coordinate "both native or both streamed".
            return _default_checksums_match(source, target)
        return checksum(target) == checksum(source)

    def _copy_file(self) -> None:
        source, target = self.source, self.target
        if target.is_file() and _supports(target.path, Move):
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
            if _supports(source.path, Copy):
                source.path.copy(target.path, overwrite=True)
            else:
                # A source that is only readable copies by streaming over the target.
                source.path.copy(target.path)
        else:
            if target.is_symlink():
                target.path.unlink()
            elif target.exists():
                target.path.rm(recursive=target.is_dir())
            source.path.copy(target.path)
        self.syncer._copy_mtime(source, target.path)

    def _directory(self) -> bool:
        """A source that is a directory. True when the pair is done."""
        source, target, syncer = self.source, self.target, self.syncer
        root = self.root
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
            not syncer.follow_mounts
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
            if not self._tolerated(error, SyncEvent.TypeMismatch):
                raise error
            return True
        if (
            (target.is_symlink() and not root)
            or binding
            or (target.exists() and not target.is_dir() and not target.is_symlink())
        ):
            if self._event(
                SyncEvent.TypeMismatch,
                target.path.rmdir if binding else target.path.unlink,
            ):
                return True

            target._stat = None

        # A directory that did not exist (or only now exists, or only would in a dry
        # run) has no children: nothing to list or remove, every child target is missing.
        self._absent = not target.exists()
        if self._absent:
            if self._event(SyncEvent.CreatedDirectory, lambda: target.path.mkdir()):
                return True

        self._source_children_cache = None
        self._windows = _names.is_windows_flavoured(target.path)
        self._target_entries_cache = None

        if syncer.remove_missing and not self._absent:
            self._event(
                SyncEvent.CheckTargetChildren,
                self._check_target_children,
                always_run=True,
            )
        self._event(SyncEvent.SyncChildren, self._sync_children, always_run=True)
        return False

    def _target_entries(self):
        # The existing target directory's children, listed once with their own
        # (non-following) stat for the removal check, the stat of each synced child and
        # the sweep. A failure is raised to the first caller only.
        if self._target_entries_cache is None:
            self._target_entries_cache = ()
            listing = list(_listing(self.target.path))
            self.listings[str(self.target.path)] = listing
            self._target_entries_cache = self.syncer._children(
                self.target, self._windows, listing, follow=False
            )
        return self._target_entries_cache

    def _source_children(self):
        if self._source_children_cache is None:
            self._source_children_cache = self.syncer._children(
                self.source, self._windows
            )
        return self._source_children_cache

    def _unsafe_name(self, name, source_entry, target_entry, event) -> bool:
        # A listed name that is not a single component inside `target` (`..`, or a
        # backslash or colon on a Windows target) is never joined onto it; it is
        # reported, not silently skipped.
        if _names.is_safe_child_name(name, windows=self._windows):
            return False
        error = ValueError(
            f"refusing unsafe child name {name!r} under {self.target.path}"
        )
        if not self.syncer._tolerate(
            self.policy, error, source_entry, target_entry, event, self.dry_run
        ):
            raise error
        return True

    def _check_target_children(self) -> None:
        source, target = self.source, self.target
        entries = self._target_entries()
        self.syncer._sweep(
            source.path,
            target.path,
            self.dry_run,
            self.policy,
            self.swept,
            self.listings,
        )
        source_names = {name for name, _ in self._source_children()}
        for name, child in entries:
            self._event(
                SyncEvent.CheckTargetChild,
                lambda name=name, child=child: self._check_target_child(
                    name, child, source_names
                ),
                always_run=True,
            )

    def _check_target_child(self, name, child, source_names) -> None:
        if self._unsafe_name(name, self.source, child, SyncEvent.RemovedMissing):
            return
        if name not in source_names:
            # The event describes the entry removed, not
            # the directory being checked.
            self.syncer.hook(
                PathAndStat.from_stat(_entries._child(self.source.path, name), None),
                child,
                SyncEvent.RemovedMissing,
                self.dry_run,
                lambda child=child: child.path.rm(recursive=True),
                self.policy,
            )

    def _target_child(self, name, listed):
        # A name the listing did not report is stat'd by `start()`,
        # not assumed missing: a listing can omit an entry, and a
        # case-insensitive target answers to another spelling.
        path = _entries._child(self.target.path, name)
        if self._absent:
            return PathAndStat.from_stat(path, None)
        return listed.get(name, path)

    def _sync_children(self) -> None:
        target = self.target
        try:
            listed = {}
            if not self._absent:
                try:
                    listed = dict(self._target_entries())
                except Exception as error:
                    # Each child is stat'd on its own and reports
                    # its own failure.
                    _logger.debug("could not list %s: %s", target.path, error)
            for name, child in self._source_children():
                if self._unsafe_name(name, child, target, SyncEvent.SyncChild):
                    continue
                self._event(
                    SyncEvent.SyncChild,
                    # Propagate the resolved policy into the recursive
                    # call so a per-call override applies to the whole
                    # subtree, not just this level.
                    lambda child=child, name=name: self.syncer._sync(
                        child,
                        self._target_child(name, listed),
                        self.dry_run,
                        self.policy,
                        False,
                        self.swept,
                        self.listings,
                    ),
                    always_run=True,
                )
        finally:
            self.listings.pop(str(target.path), None)
