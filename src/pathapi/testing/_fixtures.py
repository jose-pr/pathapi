"""The standard fixture tree and the names the contracts share."""

from __future__ import annotations

import errno as _errno

#: The standard tree `ReadPathContract`/`PathContract` expect under `root`:
#: relative POSIX name -> file content, or `None` for a directory.
FIXTURE_TREE = {
    "a.txt": "a",
    "b.py": "b",
    ".hidden.txt": "hidden",
    "sub": None,
    "sub/c.py": "c",
    "sub/nested": None,
    "sub/nested/d.py": "d",
    "empty_dir": None,
}

#: What pathlib raises for a file operation (read, write, unlink) aimed at a
#: directory: `IsADirectoryError` on Linux, `PermissionError` on Windows and
#: (for unlink) macOS.
DIRECTORY_ERRORS = (IsADirectoryError, PermissionError)

#: errno values `rmdir()` of a non-empty directory may carry (POSIX allows
#: either; Windows maps ERROR_DIR_NOT_EMPTY to ENOTEMPTY).
NOT_EMPTY_ERRNOS = (_errno.ENOTEMPTY, _errno.EEXIST)


def populate_fixture_tree(root):
    """Create the standard contract tree (`FIXTURE_TREE`) under `root`, an
    existing empty directory, through the path's own `mkdir()` and
    `write_text()` -- works for any `Path` implementation (or a stdlib
    `pathlib.Path`). Returns `root`."""
    for name, content in FIXTURE_TREE.items():
        path = root.joinpath(*name.split("/"))
        if content is None:
            path.mkdir()
        else:
            path.write_text(content)
    return root


def _rel(root, path):
    """`path` relative to `root` as a POSIX string ("." for `root` itself).

    Built from `name`/`parent` only, so it holds for implementations whose
    `relative_to()` is missing or spells the root differently. A root URI
    with and without its trailing "/" (`http://host` vs `http://host/`)
    counts as the same directory."""
    names = []
    while path != root and str(path).rstrip("/") != str(root).rstrip("/"):
        parent = path.parent
        if parent == path or len(names) > 64:
            raise AssertionError(f"{path!r} is not below {root!r}")
        names.append(path.name)
        path = parent
    return "/".join(reversed(names)) or "."


#: Names that URL-shaped or shell-shaped code tends to cut, decode or fold, stored,
#: listed and read back exactly as written. Several pairs differ only after a cut
#: at `#` or a percent-decode, so a backend that applies either stores one name.
UNUSUAL_NAMES = (
    "with space.txt",
    "hash#one.txt",
    "hash#two.txt",
    "pct%41.txt",
    "pctA.txt",
    "100%.txt",
    "plus+amp&eq=.txt",
    "Mixed Case.TXT",
)

#: The same, holding `?`, which not every file system can store.
QUESTION_MARK_NAMES = ("q?one.txt", "q?two.txt", "q?x#y%41.txt")


def _payload():
    """About 290 KiB holding every byte value, runs of CR and LF, and a
    counter that makes a dropped, repeated or reordered chunk visible."""
    block = bytes(range(256)) + b"\r\n\r\n\n\r\x00\x00\x00"
    return b"".join(index.to_bytes(4, "big") + block for index in range(1100))
