# Extending

Two equally first-class ways to add a new path-addressable resource. In both,
you implement a small method surface; everything else
(`open`/`read_text`/`write_text`/`glob`/`walk`/`touch`/`rm`/`copy`/`move`/
`exists`/`is_dir`/`is_file`/...) is *derived* from the protocols in
`pathlib_next.protocols`.

- **Track A -- subclass `Path` directly**: for a resource that is not
  naturally a URI (a database-backed virtual filesystem, a key-value store,
  ...). `MemPath` is the reference implementation.
- **Track B -- subclass `UriPath`**: for a new URI scheme. The class registers
  itself and gets URI parsing, joining, query and fragment handling from
  `Uri`.

Whichever track you pick, run the shared contract suite against your
implementation -- see [Testing your implementation](#testing-your-implementation)
below.

## Track A: subclass `Path`

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
| `_symlink_to(target, target_is_directory=False)`, `readlink()` | `symlink_to(..., force=)` is derived; `target` is already a path object. |
| `_chown(uid, gid, *, follow_symlinks=True)` | receives a canonical pair (`None` = unchanged); `chown()` is derived. |
| `_utime(*, atime=None, mtime=None)` | set an existing entry's times, in seconds since the epoch (`None` = leave that one alone); whole seconds, or no access time, is fine. Raise `NotImplementedError` (the default) when the backend cannot set a time: `touch()` of an existing file then leaves it as it is, and `PathSyncer(preserve_mtime=True)` leaves the copy's own time. |

`rm()` is derived from `stat()`, `_scandir()`, `unlink()` and `rmdir()`. An
implementation that overrides it to remove a tree natively still accepts
`follow_symlinks=` and `follow_binds=` (the shared contract calls both), checks
them with `pathlib_next.providers.check_follow(name, policy)` before it removes
anything, and forwards them to `super().rm()` wherever it falls back to the
generic walk. An override of `copy()` that copies a tree natively refuses a
target inside the source with `pathlib_next.providers.contains(source, target)`.

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
`st_ino`. `MemPath` compares its `MemPathBackend` by identity. A `UriPath`
needs nothing: two URIs are told apart only when both were given a backend
(`backend=`, `with_backend()`) and those differ; a backend the path built for
itself through `_initbackend()` adds nothing to what the URI already says --
the path records that it built the backend, whatever the backend's class is.

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
`UriPath` already does: its `rename()` raises `NotImplementedError` for a target it
cannot reach, and `move()` then copies).

`MemPath` implements this surface over nested dicts (`MemPathBackend`: a
`dict` value is a directory, a `bytearray` a file); see
[Memory Path API](../api/mempath.md).

## Track B: subclass `UriPath`

The pure-path side (parsing, join, query/fragment, `with_*`) comes from `Uri`.
Register the scheme and implement the I/O surface:

```python
import errno
import os

from pathlib_next.uri import UriPath
from pathlib_next import FileStat


class MyPath(UriPath):
    __SCHEMES = ("myscheme",)  # name-mangled: declare it in every class

    def _initbackend(self):
        # Connection or session state, created on first use and shared by
        # every path derived from this one on the same endpoint.
        return None

    def _scandir(self):
        # Or implement `_listdir()` to yield names only.
        yield from ()

    def stat(self, *, follow_symlinks=True):
        hint = self._pop_stat_hint()  # metadata from the parent's listing
        if hint is not None:
            return hint
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(self))

    def _open(self, mode="r", buffering=-1):
        raise NotImplementedError(f"open(mode={mode!r})")

    def rename(self, target):
        target = self._rename_target(target)  # sibling str, same endpoint only
        ...  # rename self.path to target.path on the server
        return self.with_path(target.path)


print(type(UriPath("myscheme://host/data/file.txt")).__name__)
```

Defining (importing) the subclass is enough: `UriPath("myscheme://...")`
dispatches to it, also when the class is defined after the first dispatch.
The class name must not start with an underscore, or the name-mangled
`__SCHEMES` lookup misses it, and a subclass that lists no `__SCHEMES` of its
own registers nothing, whatever it inherits.

A distribution that ships schemes declares each one as an entry point, so
that installing it is enough and nothing has to import its module first:

```toml
[project.entry-points."pathlib_next.schemes"]
myscheme = "mypackage.paths:MyPath"
```

The entry point only loads the module; defining the class is what registers
the names it lists, so there is no separate registration call.

### Names are held by one class

A scheme name belongs to one class. Defining a second class that lists a name
another class holds raises `ValueError` at the `class` statement, naming the
holder, and the class registers none of its names. The built-in names (`file`,
`http`, `sftp`, ...) are held by the built-in classes even before their
modules are imported, and a distribution's entry point never takes one: the
built-in module is consulted before the entry points. To replace a class on
purpose, say so in the class statement:

```python
from pathlib_next.uri import UriPath


class MyHttp(UriPath, replace=True):
    __SCHEMES = ("http", "https")


assert UriPath.scheme_class("https") is MyHttp
assert type(UriPath("http://example.com/x")) is MyHttp
```

A built-in class defined after a replacement leaves it in place. Paths built
before the replacement keep their class. Running the same class statement
again (same module and qualified name: `importlib.reload()`, a function called
twice, a notebook cell) is not a second claim: the newer class takes the name
without `replace=True`. A class of another module, or of another qualified
name, is still refused.

`UriPath.schemes()` lists the registered names, sorted, including built-in
and entry-point schemes whose module is not imported yet and schemes whose
client library is not installed (those fail where they are used, naming the
extra). `UriPath.scheme_class(name)` returns the class registered for a
name, importing or loading it first, or raises `ValueError` naming the
registered schemes:

```python
from pathlib_next.uri import UriPath

print(UriPath.scheme_class("data").__name__)  # DataUri
print("sftp" in UriPath.schemes())  # True, though its module is not imported
```

Conventions for a scheme implementation:

- Use `self.path` (percent-decoded) on the wire and `self.source`
  (`scheme`, `userinfo`, `host`, `port`, `parsed_userinfo()`) for the
  connection. `str(self)` is already redacted for error messages.
- `iterdir()` is derived from `_scandir()`, which defaults to `_listdir()`
  plus one `stat()` per child. Override `_scandir()` when one listing call
  returns type, size and mtime (an HTML index, `PROPFIND`, `listdir_attr`,
  `MLSD`, an S3 list page): each child's first `stat()` then returns the
  listing's metadata through `_pop_stat_hint()`, and later calls fetch
  again, so a remote walk costs one request per directory.
- `rename()` and `symlink_to()` receive a `str` as a decoded path, not URI
  syntax; `_rename_target()` resolves a relative one against the parent and
  raises `NotImplementedError` for another endpoint, which makes `move()`
  copy instead. Override `_same_location()` when your namespace is narrower
  than the authority (an archive, a container).
- A derived path (`/`, `parent`, `with_name()`, `relative_to()`, ...) never
  calls `_initbackend()`: it takes the backend its source already holds, and
  only for the same scheme, userinfo, host and port. Paths derived from one
  another share one slot for the backend they derive for themselves, which
  the first of them to need one fills with `_initbackend()` (under a lock);
  a path constructed on its own builds its own on first use.
- Set `_host_filesystem_path = True` only when `self.path` is a real
  filesystem path on the remote host (as for `sftp:`); `os.fspath()` and
  `host_fspath()` then return it.
- Build a child by name with `_make_child_relpath(name)` and a path from
  decoded components with `_from_parsed_parts(source, path, query, fragment)`;
  neither parses its argument. A constructor-level override goes in
  `_init(source, path, query, fragment, **kwargs)`, which every construction
  ends in, and calls `super()._init()`. Two paths are on one endpoint when
  `pathlib_next.uri.same_authority(a.source, b.source)`.
- A backend that cannot be written piecewise (an FTP `STOR`, an archive
  rewrite) is returned from `_open()` as
  `pathlib_next.providers.CommitOnClose(commit)`: `commit(buffer)` runs once,
  when the stream is closed.

The shipped API header (`AGENTS.md` inside the package) lists every one of
these hooks and helpers with its signature, under "For path implementers" and
"For scheme implementers"; the built-in schemes use no other underscore name
of the core.

The built-in schemes are worked examples of increasing size:
[`FileUri`](../api/schemes/local.md) delegates to `LocalPath`,
[`S3Path`](../api/schemes/objstore.md) emulates directories over key
prefixes, [`HttpPath`](../api/schemes/http.md) scrapes HTML listings behind a
replaceable transport, and [`SftpPath`](../api/schemes/sftp.md) caches
connections behind two interchangeable backends.

## Testing your implementation

To ensure custom implementations comply with `pathlib_next`'s expected behaviors, the library offers three contract levels in `pathlib_next.testing` which can be mixed into your `pytest` suite:

1. **`PurePathContract`**: Covers logical pure-path operations (joining, parents, stems, suffix checks, and glob matching) that do not require any physical I/O.
2. **`ReadPathContract`**: Extends `PurePathContract` to verify read-only I/O: `exists()`/`is_file()`/`is_dir()`, `read_text()`/`read_bytes()`, `open()` read modes, `iterdir()`, `stat()`, `glob()`/`rglob()` and `walk()`, plus the error paths with pathlib's exception types (a missing path raises `FileNotFoundError`, listing a file `NotADirectoryError`, reading a directory `IsADirectoryError` or, as pathlib does on Windows, `PermissionError`).
3. **`PathContract`**: Extends `ReadPathContract` to verify writes: `mkdir()`, `write_text()`/`write_bytes()`, `open()` write/append/exclusive modes, `unlink()`, `rmdir()`, `rm()` (also called with `follow_symlinks=` and `follow_binds=`), `copy()` (including `recursive=True`), `move()`, `rename()` and `touch()`, again with their error paths (a missing parent raises `FileNotFoundError`, `rmdir()` of a file `NotADirectoryError`, of a non-empty directory `OSError(ENOTEMPTY)`).

Both I/O levels need a `root` fixture pointing at a **fresh, function-scoped** directory holding the standard tree (`a.txt`, `b.py`, `.hidden.txt`, `sub/c.py`, `sub/nested/d.py`, `empty_dir/`). `populate_fixture_tree(root)` builds it through your path's own `mkdir()`/`write_text()`. The write tests create fixed names under `root` without cleaning up, so two contract classes must never share one.

### Example: Running the full contract

<!-- example: skip: a pytest module, run by tests/test_contract_helpers.py -->
```python
import pytest

from pathlib_next import LocalPath as MyPath  # your Path subclass here
from pathlib_next.testing import PathContract, populate_fixture_tree


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

| Attribute | Contract | Covers | Built-in schemes that set it `False` |
| --- | --- | --- | --- |
| `supports_listing` | `ReadPathContract` | `iterdir()`, `glob()`, `walk()` | `DataUri` (a single resource) |
| `supports_empty_directories` | `ReadPathContract` | `empty_dir/` lists as empty | `GitHubPath`, `GitLabPath` (git trees cannot hold an empty directory) |
| `distinguishes_file_types` | `ReadPathContract` | listing a file raises `NotADirectoryError`; reading a directory raises | `HttpPath` (one URL serves an index page or a file) |
| `supports_pickle` | `ReadPathContract` | `pickle.loads(pickle.dumps(path))` is equal to the path and hashes alike | none |
| `supports_rename` | `PathContract` | `rename()` | `MemPath` (`move()` copies instead) |
| `supports_append` | `PathContract` | `open("a")` | `ZipUri`, `DavPath`, `S3Path`, `GsPath`, `AzPath` |
| `supports_exclusive_create` | `PathContract` | `open("x")` | none |
| `enforces_directory_hierarchy` | `PathContract` | `mkdir()`/writes below a missing parent raise `FileNotFoundError`; writing a file over a directory raises; `rename()` onto a non-empty directory raises | `S3Path`, `GsPath`, `AzPath` (directories are key prefixes) |
| `supports_mkdir` | `PathContract` | `mkdir()`, and every test that creates a directory | none (a transport with no directories, such as TFTP, sets it) |
| `supports_delete` | `PathContract` | `unlink()`, `rmdir()`, `rm()`, and replacing a file in `copy(overwrite=True)` | none (TFTP sets it) |
| `supports_move` | `PathContract` | `move()`, by `rename()` or by copying and deleting | none (TFTP sets it) |
| `supports_unusual_names` | `PathContract` | names holding a space, `#`, `%`, `+`, `&`, `=` and mixed case are stored and listed as written | none |
| `supports_utime` | `PathContract` | `_utime()` sets an existing file's modification time and `touch()` of it moves the time | `ZipUri`, `archive:`/`archive+zip:`, `DavPath`, `S3Path`, `GsPath`, `AzPath` (no settable time) |
| `supports_question_mark_names` | `PathContract` | the same for names holding `?`. **Defaults to `False`**: a Windows file system cannot store such a name | `True` for `MemPath`, `ZipUri`, `S3Path`, `GsPath`, `AzPath`, and where the machine can store one for `LocalPath`, `FileUri`, `FtpPath`, `DavPath`, `SftpPath` |

### Which contract runs against the built-in schemes?

`tests/test_contract.py` runs:

- **Full `PathContract`**: `LocalPath`, `MemPath`, `FileUri`, `ZipUri` and `archive:`/`archive+zip:` (local outer archive), `FtpPath` (in-process pyftpdlib), `DavPath` (in-process WsgiDAV), `S3Path` (moto), `SftpPath` (in-process asyncssh server, with both the paramiko and asyncssh client backends), and `GsPath`/`AzPath` (in-process fake REST servers; they skip when the SDK is not installed).
- **`ReadPathContract`**: `HttpPath` (local HTTP server), `TarUri` and `archive:`/`archive+tar:`, `DataUri` (listing skipped), and `GitHubPath`/`GitLabPath` and their `git+github:`/`git+gitlab:` spellings (in-process fake APIs).

See `tests/test_contract.py` for how each scheme's `root` fixture is wired.
