"""Whether a name from a listing can be joined onto a directory safely."""

from __future__ import annotations

from pathlib import PureWindowsPath as _PureWindowsPath

__all__ = ["is_safe_child_name", "is_windows_flavoured"]


def is_windows_flavoured(path: object) -> bool:
    """Whether names joined onto `path` are interpreted with Windows rules.

    True for a `pathlib.PureWindowsPath` (so `LocalPath` on Windows) and for
    a path whose `filepath` is one (`FileUri` on Windows). Everything else --
    `MemPath`, remote schemes, POSIX local paths -- treats `\\` and `:` as
    ordinary filename characters.
    """
    if isinstance(path, _PureWindowsPath):
        return True
    try:
        filepath = getattr(path, "filepath", None)
    except Exception:
        return False
    return isinstance(filepath, _PureWindowsPath)


_WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)


def _windows_rewrites(name: str) -> bool:
    """Whether Windows would store the non-empty `name` under another name or
    hand it to a device. It drops a trailing dot or space, and older versions
    open a reserved device name whatever follows its first dot (`nul.txt`)."""
    return (
        name[-1] in " ."
        or name.partition(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES
    )


def _is_safe_name(name: object, windows: bool, rewrites: bool) -> bool:
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:
        return False
    if "\0" in name:
        return False
    if windows:
        if "\\" in name or ":" in name:
            return False
        if rewrites and _windows_rewrites(name):
            return False
    return True


def is_safe_child_name(name: object, *, windows: bool = False) -> bool:
    """Whether `name` is a single path component that stays inside its parent.

    Use this before joining a name that came from somewhere untrusted (a
    remote listing, an archive member, a sync source) onto a destination.
    Always rejected: a non-`str`, `""`, `"."`, `".."`, and any name
    containing `/` or NUL. With `windows=True` (see `is_windows_flavoured`)
    also rejected: `\\`, `:` (a drive prefix such as `D:x`, or an NTFS
    alternate data stream), a trailing dot or space (Windows drops it, so
    `"report."` and `"report"` name one file), and the reserved device names
    `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9` and `LPT1`-`LPT9`, in any case
    and with or without an extension (`"nul.txt"`).
    """
    return _is_safe_name(name, windows, True)
