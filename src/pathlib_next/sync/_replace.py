"""Writing a target through a temporary sibling and swapping it in."""

from __future__ import annotations

import pathlib as _pathlib
import re as _re
import uuid as _uuid

from ..path import Path
from .._filestat import FileStat
from ._paths import _child_name

#: The longest name of one directory entry, in UTF-8 bytes, that NTFS and the
#: usual POSIX filesystems accept (NTFS counts UTF-16 units, never more).
_NAME_MAX = 255

#: A temporary sibling is `.NAME.<12 hex digits>.pathlib-next-tmp`, with NAME
#: cut short when the whole would not fit in `_NAME_MAX`. Only a name matching
#: all of this is the library's own.
_LEFTOVER = _re.compile(r"\..+\.[0-9a-f]{12}\.pathlib-next-tmp")

#: A leftover is stale once it has not been modified for this long (seconds).
#: A transfer that is running writes to its file continuously, so a younger
#: one may belong to a sync running now, in this process or in another.
_STALE_AFTER = 24 * 60 * 60

#: Temporary files (`str(path)`) this process is writing now: never stale,
#: whatever the store reports as their modification time.
_LIVE_TEMPS: "set[str]" = set()

#: Set on an exception raised while the previous version of a file is gone
#: and the new one exists only under its temporary name.
_ONLY_COPY_ATTR = "_pathlib_next_sync_only_copy"


def _is_leftover(name: str) -> bool:
    return _LEFTOVER.fullmatch(name) is not None


def _encoded_length(name: str) -> int:
    return len(name.encode("utf-8", "surrogatepass"))


def _temp_sibling(path: Path) -> Path:
    # Hidden and unique, in the same directory so a rename stays on one
    # filesystem/connection. The embedded name is cut (never the random part
    # or the suffix) so that a name the target accepts still gets a sibling.
    tail = f".{_uuid.uuid4().hex[:12]}.pathlib-next-tmp"
    room = _NAME_MAX - 1 - len(tail)
    name = _child_name(path)[:room]
    while _encoded_length(name) > room:
        name = name[:-1]
    return path.with_name(f".{name}{tail}")


def _only_copy(error: BaseException, temp: Path, target: Path) -> BaseException:
    """`error`, marked so that the clean-up keeps `temp`, and, for an
    `Exception`, replaced by an `OSError` naming `temp` (the kind follows the
    errno, as `OSError()` maps it). Used once the previous `target` is gone,
    when `temp` is the only copy of the new version."""
    kept = error
    if isinstance(error, Exception):
        detail = (
            f"{target} was removed to make room for the new version, which is "
            f"kept at {temp}: {error}"
        )
        errno = getattr(error, "errno", None)
        kept = OSError(errno, detail) if errno else OSError(detail)
    try:
        setattr(kept, _ONLY_COPY_ATTR, True)
    except Exception:
        pass
    return kept


def _replace(source: Path, target: Path) -> None:
    """Rename `source` over the existing `target`. `os.replace` locally
    (atomic on POSIX and Windows); elsewhere `rename()`, which replaces on
    backends with POSIX rename semantics, and otherwise refuses -- then the
    old target is removed first, after the new content is complete. If the
    second rename then fails `source` is the only copy: it is kept, and the
    error names it."""
    if isinstance(source, _pathlib.Path):
        source.replace(target)
        return
    try:
        source.rename(target)
    except OSError:
        if FileStat.from_path(target, follow_symlink=False) is None:
            raise
        target.unlink()
        try:
            source.rename(target)
        except BaseException as error:
            kept = _only_copy(error, source, target)
            if kept is error:
                raise
            raise kept from error
