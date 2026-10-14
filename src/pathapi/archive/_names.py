from __future__ import annotations

from .._names import is_safe_child_name


def _is_safe_member_name(name: str) -> bool:
    """Whether archive member `name` is a usable relative path inside the
    archive: every part a real name, none of them escaping the root.

    Only the rules that hold on every platform are applied here, because an
    archive has no platform of its own: `\\`, `:` and trailing dots are
    ordinary filename characters on POSIX, and an archive written there may
    legitimately contain `C:drive.txt` or `a\\b`. Refusing to *join* such a
    name onto a destination that reads it differently is the destination's
    rule, and is applied per target where the joining happens --
    `Path.copy(recursive=True)`, `PathSyncer` and `unpack_archive()`
    each check `is_safe_child_name(..., windows=is_windows_flavoured(dest))`.
    A trailing `/` (a directory marker) is allowed."""
    if name.endswith("/"):
        name = name[:-1]
    return all(is_safe_child_name(part) for part in name.split("/"))


def _normalize_member_name(name: str) -> "str | None":
    """`name` as a normalized POSIX relative path, or None if it does not
    stay inside the archive root.

    A member name is a relative path, and writers spell it several ways for
    the same file: `./x` (`tar -C dir .`, `TarFile.add(arcname=".")`,
    `shutil.make_archive`), `a//b`, `a/./b`, `a/b/../c`. Normalizing it the
    way a URI reference resolves -- RFC 3986 dot-segment removal, with empty
    segments dropped -- gives one member one name, so a listing and a lookup
    agree however the archive was written, and the same spelling addresses
    the same member in a zip and in a tar.

    A name that would leave the root has no normalized form inside the
    archive and is rejected (None): absolute (`/abs`), or with more `..`
    than parts to spend them on. `..` that stays inside is resolved
    (`pkg/../ok.txt` is `ok.txt`). A drive-shaped part (`C:x`) is NOT
    rejected here: it is a legal POSIX filename, and only a destination
    that reads names with Windows rules is endangered by it -- see
    `_is_safe_member_name`. A trailing `/` (a directory marker) is kept, and
    the archive root itself normalizes to "".
    """
    if name.startswith("/"):
        return None
    directory = name.endswith("/")
    parts: "list[str]" = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None  # escapes the archive root
            parts.pop()
            continue
        parts.append(part)
    normalized = "/".join(parts)
    if not normalized:
        return ""  # the archive root ("", ".", "./", "a/..")
    if not _is_safe_member_name(normalized):
        return None
    return normalized + "/" if directory else normalized
