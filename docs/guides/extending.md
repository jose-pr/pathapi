# Extending

Subclass `Path` for any resource that is addressed by path: a
database-backed virtual filesystem, a key-value store, a remote server.
`MemPath` is the reference implementation. You implement a small method
surface; everything else
(`open`/`read_text`/`write_text`/`glob`/`walk`/`touch`/`rm`/`copy`/`move`/
`exists`/`is_dir`/`is_file`/...) is *derived* from the protocols in
`pathapi.protocols`. `FTPPath`, `ZipPath` and `TarPath` are built the same
way, on `pathapi.providers.TreePath`, and so are the classes of the
distributions that add SFTP, object stores, repository hosts and URIs.

Run the shared contract suite against your implementation -- see
[Testing your implementation](#testing-your-implementation) below.

## Compose a class from protocols

`Path` is a class statement over the protocols of `pathapi.protocols`: `Stat`, `Chmod`,
`Chown`, `Owned`, `Utime`, `Create`, `BinaryOpen`, `Listing`, `Remove`, `Readlink`, `Symlink`,
`Hardlink`, `Resolve`, `Identity`, `Copy` and `Move`. There is one protocol for each primitive a
backend can have without the others (an archive reads links and makes none), so `supports()` can
say exactly what a class does. Each has a few primitives (stubs that raise `NotImplementedError`)
and derives the rest. A backend that only reads is `Pathname`, `Listing`, `Stat` and
`BinaryOpen` with the primitives `stat()`, `iterdir()` and `_open()`, and no `Path`: `glob()`,
`walk()`, `read_text()`, `checksum.md5()` and a `PathSyncer` source work on it. List a protocol
before the ones it is built on (`Listing` before `Stat`).

`supports()` and `capabilities()` tell what a class has, without a call that raises:

```python
from pathapi import MemPath
from pathapi.protocols import Create, Readlink, supports

assert supports(MemPath, Create)
assert not supports(MemPath, Readlink)
```

| Protocol | Primitives |
| --- | --- |
| `Stat` | `stat()` |
| `Chmod` | `chmod()` |
| `Owned` | `_user_name()`, `_group_name()` (`owner()` and `group()` are derived) |
| `Chown` | `_chown()` |
| `Utime` | `_utime()` |
| `Create` | `_mkdir()` |
| `BinaryOpen` | `_open()` (one primitive for reading and writing: raise `NotImplementedError` for a mode you cannot honour) |
| `Listing` | `iterdir()` |
| `Remove` | `unlink()`, `rmdir()` |
| `Readlink` | `readlink()` |
| `Symlink` | `_symlink_to()` |
| `Hardlink` | `_hardlink_to()` |
| `Resolve` | `_cwd()` |
| `Move` | `rename()` (`move()` copies and removes without it) |
| `Identity`, `Copy` | none |

A class whose answer depends on its connection defines `_supports(protocol)`, returning
`True`, `False`, or `None` to leave the answer to the class. It may make at most one
request per backend, cached for the backend's lifetime, and it never raises: a failed
request answers `None`.

## Subclass `Path`

Required on the pure-path side (abstract on `Pathname`):

| Member | Contract |
| --- | --- |
| `segments` | property: the path components; a leading `""` marks an absolute path |
| `parts` | property: whatever "parts" means for your type |
| `parent` | property: the logical parent (the root is its own parent) |
| `with_segments(*segments)` | a same-type instance, keeping per-instance state such as a backend |
| `as_uri()` | a URI string identifying the path (a custom scheme is fine) |
| `relative_to(other)` | this path relative to `other`, `ValueError` when it is not below it (`MemPath` implements it); `NotImplementedError` where the question has no meaning |

`is_absolute()` is optional: it answers whether the path has a root, which is
right unless your rooted paths are not all absolute. Equality is not required
either: `Pathname` supplies `__eq__`/`__hash__` keyed on `(type(self),
tuple(self.segments))`, so paths work as dict keys and
`is_relative_to()`/`parents` membership work, and `<`, `<=`, `>`, `>=` compare
the same key (`sorted(path.iterdir())` works). Override `__eq__`/`__hash__`
together, and `_order_key()` with them, if your type needs another identity
(case-insensitive names, or one that distinguishes two backing stores).

On the I/O side, implement what the resource supports and leave the rest as
the inherited stubs. Derived operations either fall back (`move()` copies and
deletes when `rename()` is missing) or raise `NotImplementedError`:

| Method | Contract |
| --- | --- |
| `stat(*, follow_symlinks=True)` | a `FileStatLike`; `FileStat(st_mode=None, st_size=0, st_mtime=0, is_dir=False)` is ready-made. Raise `FileNotFoundError` for a missing path. |
| `iterdir()` | yield child instances; raise `FileNotFoundError`/`NotADirectoryError` like pathlib. Required for any listing: `copy(recursive=True)` and `for child in path` call it, and the default `_scandir()` is built on it. |
| `_scandir()` | optional addition: yield `(name, FileStat or None)` when the listing already carries metadata. `walk()`, `glob()`, `rm(recursive=True)` and `PathSyncer` use it instead of a `stat()` per child. The stat is non-following; `None` means "unknown", and `FileStat(is_dir=False, size_known=False)` means "a file, size and time unknown" (a listing that knows a type and not a size: `walk()`, `glob()` and `rm(recursive=True)` use it, `stat()` of the child asks again, `PathSyncer` re-stats it). Skip a name that is not one path component (`""`, `.`, `..`, or one containing `/` or NUL; `providers.is_safe_child_name()` decides): a server chooses its listing, and a joined `..` leaves the directory. |
| `_open(mode, buffering)` | a **binary** stream. `open()` validates the mode and passes a canonical `"r"`, `"w"`, `"x"` or `"a"`, optionally with `"+"` (never `"b"`/`"t"`); raise `NotImplementedError` for a mode you do not support. `read_text()`, `write_bytes()`, `copy()`, `touch()` all derive from it. |
| `_mkdir(mode)` | create this one directory: `FileExistsError` if it exists, `FileNotFoundError` if the parent is missing (`mkdir(parents=True)` relies on it). |
| `unlink(missing_ok=False)`, `rmdir()` | pathlib's exceptions: `IsADirectoryError`, `NotADirectoryError`, `OSError(errno.ENOTEMPTY)`. |
| `rename(target)` | return the new path. |
| `chmod(mode, *, follow_symlinks=True)` | normalize with `providers.parse_mode(mode)` so octal strings work. |
| `readlink()` | a path of this class whose text is the link text, unresolved; `OSError(EINVAL)` for a path that is not a link. `resolve()` is derived from it. |
| `_symlink_to(target, target_is_directory=False)` | `symlink_to(..., force=)` is derived; `target` is already a path object. |
| `_hardlink_to(target)`, `_cwd()` | `hardlink_to()` is derived; `_cwd()` is the directory a relative path is relative to, for `absolute()` and `resolve()`. |
| `_user_name(uid)`, `_group_name(gid)` | names for the ids of `stat()`; `owner()` and `group()` are derived. |
| `_chown(uid, gid, *, follow_symlinks=True)` | receives a canonical pair (`None` = unchanged); `chown()` is derived. |
| `_utime(*, atime=None, mtime=None)` | set an existing entry's times, in seconds since the epoch (`None` = leave that one alone); whole seconds, or no access time, is fine. Raise `NotImplementedError` (the default) when the backend cannot set a time: `touch()` of an existing file then leaves it as it is, and `PathSyncer(preserve_mtime=True)` leaves the copy's own time. |

A stream that commits when it is closed (an upload, a rewrite of an archive)
inherits `pathapi.providers.DiscardOnError`, first in its bases
(`class Upload(DiscardOnError, io.BytesIO)`), and defines `discard()`. The
mixin's `__exit__` calls `discard()` when the `with` block over the file ended
in an exception (any `BaseException`) and then closes, so a producer that fails
half way does not replace a complete file with the partial one: the target is
left as it was, and is not created if it was absent. `discard()` drops what the
stream holds so that the `close()` that follows commits nothing, and does
nothing more when it is called twice or after `close()`. A block that ended
normally, and a file closed with `close()` outside a `with` block, commit as
before; a stream (or its text wrapper) garbage collected unclosed is discarded
and emits a `ResourceWarning`, so close the file to commit it. In binary mode `open()` returns the stream `_open()` gave it as it is,
so a stream that defines `discard()` without the mixin is not discarded unless
its own `__exit__` does it; in text mode the wrapper `open()` builds calls any
`discard()` the stream has. A stream with no `discard()` is used as it is,
which is right for one that writes through as it goes (a local file,
`MemPath`). `write_bytes()`, `write_text()` and `copy()` open their target in a
`with` block, so they get the behaviour too.

`rm()` is derived from `stat()`, `_scandir()`, `unlink()` and `rmdir()`. An
implementation that overrides it to remove a tree natively still accepts
`follow_symlinks=` and `follow_binds=` (the shared contract calls both), checks
them with `pathapi.providers.check_follow(name, policy)` before it removes
anything, and forwards them to `super().rm()` wherever it falls back to the
generic walk. An override of `copy()` that copies a tree natively refuses a
target inside the source with `pathapi.providers.contains(source, target)`.

When a method accepts a `str` path, turn it into a path with
`self.with_segments(value)`, never `type(self)(value)`, which drops
per-instance state (for `MemPath`, the whole in-memory tree). `copy()` and
`move()` convert a `str` destination through `_coerce_target()`, which does
exactly that by default.

### One class, several hosts or stores

Path equality compares the type and the segments, nothing else: `/app.conf` on
one host equals `/app.conf` on another. `copy()`, `move()` and `PathSyncer`
refuse to write a file onto itself or a tree into itself, so before comparing
two paths of the same type they ask `_same_filesystem(other)`: are these
segments resolved in the same place?

| Method | Notes |
| --- | --- |
| `_same_filesystem(other) -> bool` | `other` is always the same type as `self`. Must be symmetric and do no I/O. |

The default answers yes unless the two paths hold different `_backend`
objects, so a type that says nothing keeps its guard against copying a file
onto itself. If instances of your class can point at different hosts, stores
or trees, override it -- otherwise a transfer between two of them that happen
to be spelled alike fails with `OSError: [Errno 22] Source and target are the
same file`, or `ValueError: ... source and target overlap` from `PathSyncer`:

<!-- example: skip: a method of the class being written, not a program -->
```python
def _same_filesystem(self, other):
    return self._connection is other._connection
```

`samefile()` is consulted first wherever `stat()` reports `st_dev` and
`st_ino`. `MemPath` compares its `MemPathBackend` by identity.

A type whose segments can spell one node in several ways (a relative and an
absolute form, a `..` segment, an alias scheme) also overrides
`_node_key()` (`MemPath` and the archive paths do). It returns
`(namespace, names)`: an object that two paths share exactly when they resolve
names in the same tree, and the node's normalized position in it (`()` for
the root). `copy()` and `move()` compare two paths
that both answer it, whatever their classes, so a file is never copied or
moved onto itself under another spelling, and a directory is never moved onto
its own ancestor or into itself. The default returns `None`, and two paths of
one type are then compared with `==`.

<!-- example: skip: a method of the class being written, not a program -->
```python
def _node_key(self):
    return self._connection, tuple(self._normalized_names())
```

`move()` calls `rename()` only onto a path of the very same class, and copies and deletes
otherwise: `rename()` runs inside your class's own store, so it is not handed a path
of another one. Override `_rename_compatible(target) -> bool` to widen that (a
class whose `rename()` raises `NotImplementedError` for a target it cannot reach
can accept any: `move()` then copies).

`MemPath` implements this surface over nested dicts (`MemPathBackend`: a
`dict` value is a directory, a `bytearray` a file); see
[Memory Path API](../api/mempath.md).

## Testing your implementation

To ensure custom implementations comply with `pathapi`'s expected behaviors, the library offers three contract levels in `pathapi.testing` which can be mixed into your `pytest` suite:

1. **`PurePathContract`**: Covers logical pure-path operations (joining, parents, stems, suffix checks, and glob matching) that do not require any physical I/O.
2. **`ReadPathContract`**: Extends `PurePathContract` to verify read-only I/O: `exists()`/`is_file()`/`is_dir()`, `read_text()`/`read_bytes()`, `open()` read modes, `iterdir()`, `stat()`, `glob()`/`rglob()` and `walk()`, plus the error paths with pathlib's exception types (a missing path raises `FileNotFoundError`, listing a file `NotADirectoryError`, reading a directory `IsADirectoryError` or, as pathlib does on Windows, `PermissionError`).
3. **`PathContract`**: Extends `ReadPathContract` to verify writes: `mkdir()`, `write_text()`/`write_bytes()`, `open()` write/append/exclusive modes, `unlink()`, `rmdir()`, `rm()` (also called with `follow_symlinks=` and `follow_binds=`), `copy()` (including `recursive=True`), `move()`, `rename()` and `touch()`, again with their error paths (a missing parent raises `FileNotFoundError`, `rmdir()` of a file `NotADirectoryError`, of a non-empty directory `OSError(ENOTEMPTY)`).

Both I/O levels need a `root` fixture pointing at a **fresh, function-scoped** directory holding the standard tree (`a.txt`, `b.py`, `.hidden.txt`, `sub/c.py`, `sub/nested/d.py`, `empty_dir/`). `populate_fixture_tree(root)` builds it through your path's own `mkdir()`/`write_text()`. The write tests create fixed names under `root` without cleaning up, so two contract classes must never share one.

### Example: Running the full contract

<!-- example: skip: a pytest module, run by tests/test_contract_helpers.py -->
```python
import pytest

from pathapi import LocalPath as MyPath  # your Path subclass here
from pathapi.testing import PathContract, populate_fixture_tree


class TestMyPath(PathContract):
    @pytest.fixture
    def root(self, tmp_path):
        root = MyPath(tmp_path)
        populate_fixture_tree(root)
        return root
```

This example runs verbatim in the project's own suite (`tests/test_contract_helpers.py`).

### Capability attributes

A backend that genuinely cannot meet a rule sets the matching class attribute to `False` on its test class. The affected tests then report as skipped, never as passed. Every attribute defaults to `True` except `supports_question_mark_names`; set one only for a documented gap.

| Attribute | Contract | Covers | Built-in classes that set it `False` |
| --- | --- | --- | --- |
| `supports_listing` | `ReadPathContract` | `iterdir()`, `glob()`, `walk()` | none (a class for a single resource sets it) |
| `supports_empty_directories` | `ReadPathContract` | `empty_dir/` lists as empty | none (a class over a git tree sets it: it cannot hold an empty directory) |
| `distinguishes_file_types` | `ReadPathContract` | listing a file raises `NotADirectoryError`; reading a directory raises | none (a class over HTTP sets it: one URL serves an index page or a file) |
| `supports_pickle` | `ReadPathContract` | `pickle.loads(pickle.dumps(path))` is equal to the path and hashes alike | none |
| `supports_rename` | `PathContract` | `rename()` | `MemPath` (`move()` copies instead) |
| `supports_append` | `PathContract` | `open("a")` | `ZipPath` |
| `supports_exclusive_create` | `PathContract` | `open("x")` | none |
| `enforces_directory_hierarchy` | `PathContract` | `mkdir()`/writes below a missing parent raise `FileNotFoundError`; writing a file over a directory raises; `rename()` onto a non-empty directory raises | none (a class over an object store sets it: directories are key prefixes) |
| `supports_mkdir` | `PathContract` | `mkdir()`, and every test that creates a directory | none (a transport with no directories, such as TFTP, sets it) |
| `supports_delete` | `PathContract` | `unlink()`, `rmdir()`, `rm()`, and replacing a file in `copy(overwrite=True)` | none (TFTP sets it) |
| `supports_move` | `PathContract` | `move()`, by `rename()` or by copying and deleting | none (TFTP sets it) |
| `supports_unusual_names` | `PathContract` | names holding a space, `#`, `%`, `+`, `&`, `=` and mixed case are stored and listed as written | none |
| `supports_utime` | `PathContract` | `_utime()` sets an existing file's modification time and `touch()` of it moves the time | `ZipPath` (no settable time) |
| `supports_question_mark_names` | `PathContract` | the same for names holding `?`. **Defaults to `False`**: a Windows file system cannot store such a name | `True` for `MemPath` and `ZipPath`, and where the machine can store one for `LocalPath` and `FTPPath` |

### Which contract runs against the built-in classes?

- **Full `PathContract`**: `LocalPath` and `MemPath` (`tests/test_contract.py`),
  `FTPPath` (in-process pyftpdlib, `tests/test_ftp_path.py`) and `ZipPath` over a
  local archive (`tests/test_archive_path.py`).
- **`ReadPathContract`**: `TarPath`, `ArchivePath`, and `ZipPath` and `TarPath`
  over an archive in memory (`tests/test_archive_path.py`).

See those files for how each class's `root` fixture is wired.
