"""The permission and ownership arguments of `chmod()` and `chown()`."""

from __future__ import annotations

import operator as _operator
import typing as _ty

__all__ = ["UNCHANGED", "parse_mode", "parse_owner"]


#: A `st_mode` is 16 bits: four of file type, twelve of permissions.
_MODE_MASK = 0o177777


def parse_mode(mode: _ty.Union[int, str]) -> int:
    """Normalize a permission `mode` to an int, parsing `str` as **octal**.

    `chmod("0755")` is the spelling everyone actually writes a mode in --
    `chmod(1)`, Ansible, Dockerfiles, every shell script -- and stdlib
    refuses it (`TypeError: 'str' object cannot be interpreted as an
    integer`). This library accepts it, which makes the base explicit and
    non-negotiable rather than leaving it to each call site.

    **Why base 8 is mandatory here, and never a plain `int()`:** `"0755"`
    parsed as decimal is 755, which is `0o1363` -- a different *and valid*
    mode. Nothing would raise; the file would just end up with permissions
    nobody intended. That is exactly why stdlib declines strings, so the
    only safe way to accept them is to parse them one way, in one place.

    Accepts an optional `0o`/`0O` prefix. Anything outside `[0-7]` raises
    `ValueError` rather than being coerced -- a mode is not a number that
    happens to be written in octal, it is octal.

    An `int` passes through (including `0o755`, which *is* an int by the time
    it gets here -- the literal is resolved by the parser, so `chmod(0o755)`
    and `chmod("0755")` agree). A `bool` is `TypeError`, and a number that
    cannot be a mode (negative, or wider than the 16 bits of a `st_mode`) is
    `ValueError`, for an `int` and a `str` alike. The file-type bits of a
    `st_mode` are in range, as `os.chmod` accepts them; `stat.S_IMODE()` drops
    them.
    """
    if isinstance(mode, bool):
        raise TypeError("a mode is an int or an octal str, not bool")
    if isinstance(mode, str):
        text = mode.strip()
        if text[:2].lower() == "0o":
            text = text[2:]
        if not text or any(character not in "01234567" for character in text):
            raise ValueError(f"invalid octal mode: {mode!r}")
        value = int(text, 8)
    else:
        value = _operator.index(mode)
    if not 0 <= value <= _MODE_MASK:
        raise ValueError(f"mode out of range (0 to {_MODE_MASK:#o}): {mode!r}")
    return value


#: Canonical "leave this ownership field unchanged" sentinel for `chown()`.
#: `None` is the API-level spelling; `-1` is accepted too because that is
#: `os.chown`'s own sentinel and callers coming from it reach for it.
UNCHANGED = None


#: `uid_t` and `gid_t` are 32 bits wide.
_ID_MAX = 0xFFFFFFFF


def parse_owner(
    uid: _ty.Union[int, str, None], gid: _ty.Union[int, str, None]
) -> "_ty.Tuple[_ty.Optional[int], _ty.Optional[int]]":
    """Normalize a `chown()` uid/gid pair to canonical `int | None`.

    `None` means "leave unchanged". `-1` is accepted as an alias for it,
    since that is how `os.chown` spells the same thing and callers arriving
    from the stdlib reach for it out of habit.

    The point of centralizing this is that **every backend spells
    "unchanged" differently** -- `os.chown` wants `-1`, SFTP `setstat` wants
    the field omitted from the attrs entirely, and other middlewares want
    `None`. If each scheme translated the caller's input itself, that is
    three chances for the semantics to disagree. Normalizing once on `Path`
    means a backend's `_chown()` receives an already-canonical pair and only
    has to convert to its own wire spelling.

    A `str` is passed through as a *name* (`shutil.chown` accepts user and
    group names, and it is useful not to force a caller to resolve them) --
    backends that cannot resolve names should say so rather than guess.
    """

    def _one(value):
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, bool):
            raise TypeError("an owner is an id, a name or None, not bool")
        value = _operator.index(value)
        if value == -1:
            return None
        if not 0 <= value <= _ID_MAX:
            raise ValueError(f"id out of range (0 to {_ID_MAX}): {value!r}")
        return value

    return _one(uid), _one(gid)
