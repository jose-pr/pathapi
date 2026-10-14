"""Test-suite building blocks for verifying custom Path
implementations satisfy the library's filesystem contract.

Not imported by `pathapi/__init__.py` -- this module requires pytest,
which is a test-only dependency. Import it explicitly. Example::

    import pytest

    from pathapi import LocalPath as MyPath  # your Path subclass here
    from pathapi.testing import PathContract, populate_fixture_tree


    class TestMyPath(PathContract):
        @pytest.fixture
        def root(self, tmp_path):
            root = MyPath(tmp_path)
            populate_fixture_tree(root)
            return root

`root` must be a **fresh, function-scoped** directory holding exactly the
standard tree (`populate_fixture_tree()` builds it through the path's own
API): the write tests create fixed names and assert their absence first, so
two contract classes sharing one root fail each other.

Rules pathlib guarantees are asserted with pathlib's exception types. Where
pathlib itself differs by OS (reading or unlinking a directory raises
`IsADirectoryError` on Linux and `PermissionError` on Windows/macOS), the
contract accepts that documented set. A backend that genuinely cannot meet a
rule sets the matching capability attribute to False on its test class; the
affected tests then report as skipped, never as passed. Every attribute
defaults to True except `supports_question_mark_names` and the five that
default to `None`, meaning "ask `supports()`" (`supports_listing`, `supports_mkdir`,
`supports_delete`, `supports_utime`, `supports_rename`):

- `ReadPathContract.supports_listing` -- `iterdir()`, `glob()`, `walk()`
  (a listing's stat whose `size_known` is False is held to its type only).
- `ReadPathContract.supports_empty_directories` -- `empty_dir/` lists as
  empty (git trees cannot hold an empty directory, so a placeholder file
  lives there).
- `ReadPathContract.distinguishes_file_types` -- listing a file raises
  `NotADirectoryError` and reading a directory raises (plain HTTP cannot
  tell: one URL serves an index page or a file).
- `ReadPathContract.supports_pickle` -- a path pickles, and the copy is equal
  to it and hashes alike.
- `PathContract.supports_rename` -- `rename()`.
- `PathContract.supports_append` -- `open("a")`/`open("ab")`.
- `PathContract.supports_exclusive_create` -- `open("x")`.
- `PathContract.enforces_directory_hierarchy` -- `mkdir()` and writes below
  a missing parent raise `FileNotFoundError`, writing a file over a
  directory raises, and `rename()` onto a non-empty directory raises (object
  stores, whose directories are key prefixes, cannot).
- `PathContract.supports_mkdir` -- `mkdir()`, and so every test that
  creates a directory (a transport with no directories, such as TFTP, has
  none).
- `PathContract.supports_delete` -- `unlink()`, `rmdir()` and `rm()`, and
  replacing a file in `copy(overwrite=True)`.
- `PathContract.supports_move` -- `move()`, by `rename()` or by copying and
  then deleting; False for a backend that can do neither.
- `PathContract.supports_unusual_names` -- names holding a space, `#`, `%`
  (also followed by two hex digits), `+`, `&`, `=` and mixed case are stored
  and listed exactly as written.
- `PathContract.supports_question_mark_names` -- the same for names holding
  `?`. False by default: a Windows file system cannot hold such a name, so
  a correct local backend there would fail; set it True for a URI scheme or
  a remote store.
- `PathContract.supports_utime` -- `_utime()` sets an existing file's
  modification time, and `touch()` of that file moves it. A backend that
  keeps no settable time (an object store, an archive) is correct without
  it: it leaves `_utime()` to raise `NotImplementedError` and sets this False.

A test that needs an operation checks the matching switch first, so a
backend without that operation needs no expected-failure marks. The tree
itself is yours to build: `populate_fixture_tree()` uses `mkdir()` and
`write_text()`, so a backend without them seeds `root` another way.
"""

from __future__ import annotations

from ._fixtures import (
    DIRECTORY_ERRORS,
    FIXTURE_TREE,
    NOT_EMPTY_ERRNOS,
    populate_fixture_tree,
)
from ._read import PurePathContract, ReadPathContract
from ._contract import PathContract

__all__ = [
    "DIRECTORY_ERRORS",
    "FIXTURE_TREE",
    "NOT_EMPTY_ERRNOS",
    "PathContract",
    "PurePathContract",
    "ReadPathContract",
    "populate_fixture_tree",
]
