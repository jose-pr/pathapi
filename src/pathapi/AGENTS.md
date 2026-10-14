# `pathapi` — public API header

Header-file-style reference for the `pathapi` package: every public export
with its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is self-contained.
Development documentation lives with the source at
<https://github.com/jose-pr/pathapi>; the rendered documentation is at
<https://jose-pr.github.io/pathapi/> and the project overview is the
`README.md` shipped next to this file (`pathapi/README.md`). Every
deliberate behavioral difference from `pathlib.Path` is listed, with its
rationale, at <https://jose-pr.github.io/pathapi/divergences/>; this file
states the resulting contract.

Names are imported from the module named in the heading of the section that
documents them; the root `pathapi` re-exports the core ones ("Package
root"). A module or name that starts with an underscore is private. The file
documents the core alone: path classes for URIs, and for SFTP, object stores
and repository hosts, are built on it by other distributions that ship their
own header.

`pip install pathapi` (published as `pathlib-next`, import name `pathlib_next`,
up to 0.10.0). No dependencies: everything in this file runs on
the standard library, and `pytest` is needed only to import
`pathapi.testing` and `pathapi.http.testing`.

`pathapi.__version__` is the installed distribution's version
(`importlib.metadata`), `"0+unknown"` for a source tree that is not installed.

## Package root (`pathapi`)

`import pathapi` exposes `Path`, `Pathname`, `LocalPath`,
`PosixPathname`, `WindowsPathname`, `MemPath`, `FileStat`, the protocols `Stat`,
`Chmod`, `BinaryOpen`, `FsPathLike`, the aliases `PathLike`/`PurePathLike`, the
exceptions `PathError` and `NonRelativePatternError`, and the modules `glob`
(`pathapi.glob`) and `sync` (`pathapi.sync`). `FTPPath`, `ZipPath`,
`TarPath` and `ArchivePath` are exported too, imported on first use so that
`import pathapi` loads neither `ftplib` nor `ssl` nor any archive code.
`pathapi.http` (HTTP requests) is not exported there: import it when
it is needed, since it loads `http.client` and `ssl`.
`MemPath` is defined in `pathapi.mempath`, which also holds
`MemPathBackend` and `MemFile`; `pathapi.testing` is never imported
implicitly.

Every public module declares `__all__` with exactly the names this file
documents for it: the root, `path`, `fspath`, `mempath`, `protocols` (and its
`fs`, `io`, `checksum`), `testing`, `sync`, `glob`, `checksum`, `archive`,
`ftp`, `http`, `providers` and `exceptions`. So `from pathapi import *` publishes
the root's names and nothing else (`P` and `PN` are not among them; they stay
importable from `pathapi.path`). Every other module of the core starts
with an underscore and is private.

The package ships `py.typed`: `Path(...)`, `LocalPath(...)`, `MemPath(...)` and
`FileStat(...)` pass mypy and pyright at their defaults. A protocol stub (`stat`,
`chmod`, `_chown`, `_open`, `checksum`) is an ordinary method that
raises `NotImplementedError`, and a type checker reads `Pathname`'s abstract
members as ordinary methods too, because the bare `Path(...)` builds a
`LocalPath`: a subclass that leaves one out is refused when it is instantiated,
not by a checker.

### `FileStat` (`pathapi`)

```python
class FileStat:
    st_mode: int
    st_nlink: int
    st_uid: int
    st_gid: int
    st_size: int
    st_atime: float
    st_mtime: float
    st_ctime: float
    mode_known
    size_known
    def __init__(
        self, st_mode=None, st_size=0, st_mtime=0, is_dir=False, size_known=True,
    ): ...
    @classmethod
    def from_stat(cls, stat) -> FileStat: ...
    @classmethod
    def from_path(cls, path, *, follow_symlink=True): ...
    def settime(self, value): ...
    def setmode(self, value, isdir=None): ...
    def items(self): ...
```

Slotted stat for non-`os` backends, with the fields of `os.stat_result`
(`st_mode`, `st_nlink`, `st_uid`, `st_gid`, `st_size`, `st_atime`, `st_mtime`,
`st_ctime`), `mode_known` and `size_known`. Without `st_mode`, a placeholder
(`S_IFREG|0o444` / `S_IFDIR|0o555`) with `mode_known=False`. A bare
permission `st_mode` (`0o644`) gets the type bits of `is_dir` ORed in and
counts as reported; one that carries a type keeps it. `size_known=False`
(default `True`) marks a listing's **type-only** stat: the entry is known to
be a file (or a directory) and `st_size` and `st_mtime` are placeholders (0).
It answers the type (`is_dir()`, `is_file()`, `exists()`, `walk()`, `glob()`,
`rm(recursive=True)`) and nothing else: a path class hands such a stat on
once, for a type question, and `stat()` asks the server; `PathSyncer`, `copy()`'s progress total and
`quick_check` ask the path for a size or a time instead of reading the
placeholder (`from_stat()` copies the flag from a foreign stat that has one).
`from_stat(stat)` (a `FileStat` passes through; `None` fields become 0),
`from_path(path, *, follow_symlink=True) -> FileStat | None` (`None` only
on `FileNotFoundError`), `settime(value)`, `setmode(value, isdir=None)`,
`items()`. `is_dir()`/`is_file()`/... are **methods**.

## Pure paths and I/O paths (`pathapi.path`)

### `Pathname`

```python
class Pathname:
    # abstract
    segments: Sequence[str]
    parts
    parent: Self
    def with_segments(self, *segments) -> Self: ...
    def as_uri(self) -> str: ...
    def relative_to(self, other) -> Self: ...
    # derived
    name: str
    suffix: str
    suffixes
    stem
    def with_name(self, name) -> Self: ...
    def with_stem(self, stem) -> Self: ...
    def with_suffix(self, suffix) -> Self: ...
    parents: Sequence[Self]
    def is_relative_to(self, other): ...
    def joinpath(self, *args) -> Self: ...
    root: str
    drive: str
    anchor: str
    def match(self, path_pattern, *, case_sensitive=None): ...
    def full_match(self, pattern, *, case_sensitive=None) -> bool: ...
    def as_posix(self) -> str: ...
    def has_glob_pattern(self): ...
    def is_absolute(self) -> bool: ...
```

ABC for a pure (no I/O) path. Abstract: `segments`,
`parts`, `parent`, `with_segments()`, `as_uri()`,
`relative_to()`. Derived: `name`, `suffix`, `suffixes`, `stem`
(suffix rules of the running interpreter), `with_name`/`with_stem`/
`with_suffix` (`with_name()` and `with_stem()` raise `ValueError` for `""`,
`.` or a separator; the edge cases of `with_stem("")` and `with_suffix(".")`
are those of the running `pathlib`: from 3.13 `with_stem("")` raises when the
name has a suffix), `parents`,
`is_relative_to()`, `joinpath()`, `/` and `"prefix" / path`,
`root`/`drive`/`anchor` (`root` is `"/"` when the first segment is empty;
`drive` is `""`), `match()` (pathlib's
right-anchored per-segment match; empty pattern → `ValueError`),
`full_match()` (3.13 semantics: the root is
a component of its own, so a rooted path needs a rooted pattern or a leading
`**` that reaches past it, and a lone `*` never matches it; `.` and empty
components of the pattern are ignored; a bracket expression never matches a
separator), `as_posix()`, `has_glob_pattern()`, `is_absolute()` (the path
has a root; a class whose rooted paths are not all absolute overrides it).

- `__eq__`/`__hash__` default to `(type(self), tuple(self.segments))`: exact
  type, so a subclass never equals its base. `LocalPath`/`PosixPathname`/
  `WindowsPathname` keep `pathlib.PurePath` equality; a class that compares
  something else (a URI's text) overrides both together.
- `<`, `<=`, `>`, `>=` compare `_order_key()` (default: the segments)
  between two paths of the same exact type, so `sorted()` works over
  `MemPath`; any other operand gives `NotImplemented`
  (`TypeError`), as `pathlib` does across flavours. A type with its own
  `__eq__` overrides `_order_key()` with the value that `__eq__` compares.
  `LocalPath`/`PosixPathname`/`WindowsPathname` keep `pathlib`'s ordering.
### `Path`

```python
class Path(
    Pathname, Move, Copy, Identity, Create, Symlink, Remove, Listing, Resolve,
    Readlink, Hardlink, Owned, Stat, Chmod, Chown, Utime, BinaryOpen,
):
    info
    def is_hidden(self): ...
    def is_junction(self) -> bool: ...
    def is_mount(self) -> bool: ...
    def is_dir_binding(self) -> bool: ...
    def samefile(self, other_path): ...
    def iterdir(self) -> Iterator[Self]: ...
    def glob(
        self, pattern, *, case_sensitive=None, include_hidden=True,
        recursive=None, dironly=None, recurse_symlinks=False, native=True,
        on_error=None, bound_loops=False,
    ): ...
    def rglob(
        self, pattern, *, case_sensitive=None, include_hidden=True,
        recursive=True, dironly=None, recurse_symlinks=False, native=True,
        on_error=None, bound_loops=False,
    ): ...
    def walk(self, top_down=True, on_error=None, follow_symlinks=False): ...
    def touch(self, mode=None, exist_ok=True): ...
    def mkdir(self, mode=0o777, parents=False, exist_ok=False): ...
    def unlink(self, missing_ok=False): ...
    def rmdir(self): ...
    def rm(
        self, recursive=False, missing_ok=False, ignore_error=False, *,
        follow_symlinks=False, follow_binds=False,
    ): ...
    def rename(self, target): ...
    def symlink_to(
        self, target, target_is_directory=False, *, force=False,
    ) -> None: ...
    def readlink(self) -> Self: ...
    def hardlink_to(self, target) -> None: ...
    def absolute(self) -> Self: ...
    def resolve(self, strict=False) -> Self: ...
    def owner(self, *, follow_symlinks=True) -> str: ...
    def group(self, *, follow_symlinks=True) -> str: ...
    def copy(
        self, target, *, overwrite=False, follow_symlinks=True,
        preserve_metadata=True, recursive=False, ignore_error=None,
        progress=None, on_error=None,
    ): ...
    def copy_into(
        self, target_dir, *, overwrite=False, follow_symlinks=True,
        preserve_metadata=True, recursive=False, ignore_error=None,
        progress=None, on_error=None,
    ) -> Path: ...
    def move_into(self, target_dir, *, overwrite=False) -> Path: ...
    def replace(self, target) -> Path: ...
    def move(self, target, *, overwrite=False): ...
    # override hooks
    def _same_filesystem(self, other) -> bool: ...
    def _node_key(self) -> tuple[object, tuple[str, ...]] | None: ...
    def _scandir(self) -> Iterator[Tuple[str, Optional[FileStat]]]: ...
    def _mkdir(self, mode): ...
    def _symlink_to(self, target, target_is_directory=False) -> None: ...
    def _coerce_target(self, target) -> Path: ...
    def _rename_compatible(self, target) -> bool: ...
    def _symlink_target(self, target) -> Self: ...
```

Base class for I/O paths.
`Path(*args)` on the bare class constructs a `LocalPath`.

- Operation precedence: `Path.__init_subclass__` re-asserts pathapi's
  `copy`, `move`, `copy_into`, `move_into`, `exists`, `rglob`, `read_text`,
  `write_text` and `symlink_to` on any subclass that would otherwise inherit stdlib
  `pathlib`'s. A class mixing a concrete stdlib path with `Path` but not
  `LocalPath` is a local class too: it gets every function `LocalPath`
  defines (`stat`, `chmod`, `is_junction`, `is_mount`, `glob`, `walk`,
  `_scandir`, `_symlink_to`, `_chown`, `is_dir`/`is_file` before 3.13, ...)
  wherever stdlib or a generic default would answer, so `rm(recursive=True)`
  stays out of a junction there as on `LocalPath`. A method defined in the
  subclass itself, or in a mixin of your own, always wins.
- `is_hidden()` — name starts with `"."`. `__iter__()` is `iterdir()`.
- Every operation above is defined by a protocol (see "Protocols"): `Path` adds
  only `is_hidden()`, `__new__` and `__init_subclass__`. `readlink()`,
  `hardlink_to()`, `absolute()`, `resolve()`, `owner()` and `group()` are
  defined on every `Path`, and raise `NotImplementedError` until a class supplies
  `readlink()`, `_hardlink_to()`, `_cwd()`, `_user_name()` and `_group_name()`;
  `supports(path, Readlink)` and the like say which it did. `LocalPath` keeps
  pathlib's own.
- `samefile()` — compares `(st_dev, st_ino)`; `NotImplementedError`
  when `stat()` lacks them (`LocalPath` uses pathlib's).
- `_same_filesystem()` — override hook: whether `other`'s
  segments are resolved in the same namespace (host, store, tree) as this
  path's. `==` ignores that, so `copy()`, `move()` and `PathSyncer` ask it
  before treating two paths as the same file, nested, or overlapping.
  Called only with `type(other) is type(self)`; must be symmetric and do no
  I/O. Default: both paths share one `_backend` object, or neither has
  one, so equal paths of a type that says nothing ARE the same file. **A
  type whose instances can front different hosts or stores must override
  it**, or a transfer between two of them spelled alike is refused
  (`OSError(EINVAL)` "same file" / `ValueError` "overlap"). `MemPath`:
  same `MemPathBackend` instance.
- `_node_key()` — override hook for a type
  whose segments spell one node several ways (`MemPath`: `a.txt`,
  `/a.txt`, `/d/../a.txt`; an archive member: the spellings of its
  archive). `namespace` is an object two paths share exactly
  when they resolve names in one tree (compared with `is`); `names` is the
  node's normalized position in it (`()` for the root). `copy()` and
  `move()` compare two paths that both answer it, whatever their classes.
  Default `None`: two paths of one type that `_same_filesystem()` places
  together are compared with `==` and `is_relative_to()`. No I/O.
- `iterdir()` — **stub** (`NotImplementedError`); a
  listable `Path` must implement it (or `_scandir()`).
- `_scandir()` — non-following stat
  per entry; default: `iterdir()` + one `stat(follow_symlinks=False)` per
  child. Consumed by `walk()`, `glob()`, `rm(recursive=True)` and
  `PathSyncer`; override it when the listing call already returns metadata.
  `None` means "unknown", never "missing". Never yield a name that is not
  one path component (`providers.is_safe_child_name()`: `""`, `.`, `..`, or one
  containing `/` or NUL): every listing of the package skips such a name, and
  an override must too.
- `glob()` —
  pathlib semantics: hidden entries included, `**` does not descend into
  directory symlinks unless `recurse_symlinks=True`, a
  trailing `**` selects files too on 3.13+, a missing or non-directory base
  yields nothing, `""` → `ValueError`, an absolute pattern →
  `NonRelativePatternError`. `recursive=None` enables recursion when a
  component is `**`; an explicit value wins. Validates eagerly, selects
  lazily. On a remote scheme a recursive glob lists every directory of the
  subtree, once. A path two `**` reach along several splits is yielded once
  (3.13+ `pathlib` yields it once per split). A literal component is checked
  as the running `pathlib` checks it: before 3.13 one that is not the last
  must be a directory (`a.txt/..` selects nothing), and from 3.12 the last
  is tested without following a link, so a dangling symlink is selected.
  An explicit `case_sensitive` compares literals against the listing too
  and yields the name as stored, and so does 3.12 without it (3.12 lists
  every literal; later versions join it as typed); `.` and `..` stay
  literal. From 3.13 a final literal with a trailing separator is
  tested as `os.path.lexists(name + os.sep)`, so the platform decides: Windows
  selects a dangling directory link, macOS a link to a file (`LocalPath` and
  stdlib paths; `native=False` tests it by following the link). A pattern with
  no component (`.`) is a `ValueError`; `rglob(".")` selects every entry
  below.
  - **`pattern=None`** expands the pattern THIS PATH CARRIES
    (`LocalPath("/etc/*.conf").glob(None)`), splitting at the first
    wildcard — the supported form for a path that is itself a pattern.
    `""` still raises.
  - **`on_error(error)`** is called when a directory cannot be listed,
    the same contract as `walk()`/`os.walk`: raising from it propagates,
    returning treats that directory as empty. Without it the listing is
    skipped in silence (pathlib's behaviour), so a caller could not tell
    an unreadable directory from an absent one. A base that is missing or
    not a directory is reported (once) whatever the first component is.
    `error.filename` names the directory even when the backend left it
    unset.
  - **`bound_loops=True`** skips a directory whose `(st_dev, st_ino)` is
    already on the CURRENT DESCENT PATH — a directory reachable below
    itself, which is what a loop is. It bounds a Windows junction loop,
    which `recurse_symlinks=False` cannot (a junction reports
    `is_symlink() == False`) and which `pathlib` itself walks until the
    recursion limit. One directory deliberately reachable under two
    SIBLING names (a shared layer junctioned in twice) is not a loop and
    both names expand — the rule is the ancestor chain, not everything
    seen, the same line `find -L` draws. A backend whose stat carries no
    identity (`MemPath`, most remote backends, a `st_ino` of 0 or `None`) is
    walked unbounded.
  - **`recurse_symlinks=True`** lets `**` descend through symlinks to
    directories, as pathlib 3.13 does (the keyword is accepted on every
    version). A link to a directory the descent is already inside
    (`(st_dev, st_ino)` equal) is an entry and is not entered, and where the
    stat carries no identity no more than 40 links are followed in one
    descent, so a loop ends where pathlib follows it until the system refuses
    it; those repeated entries are the only difference. A backend without
    symlinks answers as before.
  - **`native=True`** (default) follows the running interpreter on the three
    rules pathlib changed mid-series: a trailing `/` is ignored before 3.11
    and selects directories only from 3.11; `a**` raises `ValueError`
    before 3.13 and is a plain wildcard from 3.13; a trailing `**` selects
    directories only before 3.13 and files too from 3.13. `native=False`
    applies one rule on every version (trailing `/` → directories only,
    `a**` → a plain wildcard, trailing `**` → files too), so a pattern
    answers the same on every interpreter and backend;
    `pathapi.testing`'s contract suite uses it.
- `rglob()` — `glob(f"**/{pattern}", recursive=True)`.
  `pattern=None` is `glob(None)`. `LocalPath` raises the audit events
  `pathlib.Path.glob` and `pathlib.Path.rglob` with the arguments `pathlib`
  passes on the running version (3.13+ also raises `glob` for the call
  `rglob` stands for); `pattern=None` raises none.
- `walk()` — drives
  `_scandir()`; its stats are trusted only with `follow_symlinks=False`.
  Without `follow_symlinks` a symlink to a directory and a Windows junction
  (`is_junction()`) are listed in `filenames` and not entered, as
  `pathlib.Path.walk()` does; with it they are entered, and nothing protects
  the walk from a loop (`LocalPath` included: its walk is this one).
  A listed name that is not one path component (`providers.is_safe_child_name()`)
  is left out of `dirnames`/`filenames`; `on_error`, when given, is called
  with a `ValueError` for it (`error.filename` is the directory).
- `touch()` — `FileExistsError` when `exist_ok=False`
  and the path exists; never truncates; creates with `open("x")` (falls
  back to `"w"` when `x` is unsupported); `chmod(mode)` only when `mode` is
  passed (unmasked), and a class that cannot chmod (`NotImplementedError`)
  raises it and removes the file it created, so a mode asked for is never
  dropped; an existing file is left as it is, mode included, as `pathlib`
  does; sets an existing file's access and modification times
  to now through `_utime()`, and leaves them as they are where the class
  cannot set one (`NotImplementedError`). `LocalPath` uses pathlib's
  `touch()`.
- `_mkdir()` (stub) / `mkdir()`.
- `unlink()`, `rmdir()` — stubs.
- `rm()` — extension.
  `ignore_error` is a bool or `callable(error, path) -> bool` (True
  swallows); each error is offered once, and a predicate that raises ends
  the call with its own exception, which is not offered to it again. Recursive removal is bottom-up and
  never descends through a directory symlink or a binding (a Windows
  junction, a mount point — `is_dir_binding()`): the entry itself is
  removed, never what is behind it, which is what `rm -r` does.
  `follow_symlinks=` (symlinks) and `follow_binds=` (bindings) choose per
  call: `False` (default, remove the entry), `True` (remove the contents
  behind it too, then the entry: a followed symlink is unlinked), `None`
  (leave it in place — the enclosing directory is then not empty and says
  so), or a callable `policy(path) -> bool | None` asked per entry, so one
  tree can keep one mount and follow another. Path components before the
  final one are followed as usual; the path `rm()` is called on is decided by the same
  policy as one met on the way down (without `recursive` a symlink is only
  unlinked). A policy that is not `True`/`False`/`None`/a callable, or a
  callable's answer that is not one of the three, raises `ValueError`
  before anything is removed and is never offered to `ignore_error`. A class
  that overrides `rm()` takes and checks both keywords; one whose backend holds
  no symlinks or bindings decides nothing with them, and one with a native
  recursive removal uses it only for the default `follow_symlinks=False`
  and runs this walk otherwise. A listed name that is not one path component
  (`providers.is_safe_child_name()` with `windows=False`: the names come from
  the directory's own listing) is never joined onto it: it raises
  `ValueError`, offered to `ignore_error` as `(error, directory)` once per
  entry, and nothing is removed for it.
- `rename()` — stub. Implementations return the new path.
- `_symlink_to()` (stub; receives a path
  object) / `symlink_to()`
  — `force=True` unlinks an existing non-directory entry first (not atomic;
  never removes a directory). A `str` target goes through the overridable
  `_symlink_target()` and is stored verbatim; relative stays relative:
  `LocalPath` hands the text on untouched (`./t/` stays `./t/`, as
  `pathlib` stores it) except on Windows, which cannot resolve a relative
  target spelled with `/`, so the path class rewrites it there; a class for
  URI references must not parse it as one, and any other class normalizes it
  with `with_segments()`, which keeps per-instance state. Implemented by
  `LocalPath`, and by classes of other distributions whose backend has links.
- `copy()`
  - A file copy calls `stat()` once on the source (type, mode, size and
    identity) and once on the target.
  - Existing target: `FileExistsError` unless `overwrite=True`; a directory
    target of a file copy → `IsADirectoryError`; a directory source needs
    `recursive=True`; copying into its own subtree → `OSError(EINVAL)`; onto
    the same file (or a case-insensitive alias) → `OSError(EINVAL)`. Two
    files of this machine (`LocalPath`, a subclass, `pathlib.Path`, a
    `file:` URI) are compared by identity whatever their classes, a
    directory reached through a symlink included. A type that answers
    `_node_key()` is compared by that, across classes. Otherwise "same" is
    `samefile()` where `stat()` carries `st_dev`/`st_ino`, else equal paths
    of one type that `_same_filesystem()` places together.
  - The source is opened before the target is touched; a failed stream
    removes the partial target. `overwrite=True` removes an existing file
    target with `unlink(missing_ok=True)` just before writing: a backend
    whose write replaces the file and which cannot delete implements
    `unlink()` so that `missing_ok=True` returns without removing.
  - A recursive copy runs inside `target._write_batch()`: onto a zip
    archive it is one rewrite of the file, and a copy that raises leaves
    the archive as it was.
  - A recursive copy refuses any child name that would not stay inside
    `target` — `..`, and when the target reads names with Windows rules
    (`providers.is_windows_flavoured()`) `\`, `:`, a trailing dot or space, and
    the reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`,
    `LPT1`-`LPT9`, with or without an extension). The names come from a
    listing the destination does not control (an archive, a remote index,
    an object-store key); on a Windows target `"C:x"` joins to a
    drive-relative path outside it and `"report."` is stored as `"report"`.
    Raised as `ValueError` through `ignore_error`, per child, like any other
    child failure.
  - `follow_symlinks=False` on a symlink recreates the link
    (`NotImplementedError` if either side cannot); only the target text is
    copied, not the link's own mode, times or owner. A dangling symlink at
    the target is written through, as `shutil.copyfile` does.
  - `preserve_metadata=True` copies permission bits only, and only a mode the
    source backend really reported (`FileStat.mode_known`): a file's, and a
    directory's after its children are copied, so a read-only directory
    still receives them. Between two different classes the setuid, setgid
    and sticky bits are dropped, so a mode read from a tar member or a
    remote server never makes a privileged file here.
  - `ignore_error`: `True` suppresses child errors of a recursive copy,
    `False`/`None` raise; a callable is called as `ignore_error(error)` and
    the error is **always** suppressed (its return value is ignored — not
    `rm()`'s `(error, path)` predicate).
  - `on_error`: `rm()`'s predicate for a recursive copy, `(error, path) ->
    bool` (or a bool), asked once per child error with `path` the source
    entry that failed. `True` skips that entry and the copy goes on, `False`
    raises the error, and a predicate that raises ends the copy with its own
    exception. An error it declined, or raised, is not offered again by the
    directories above (not even for a nested `copy()`). Errors outside the
    children — the root, the target directory, a refused prefix of an object
    store — are raised and never offered. Passing `ignore_error` and
    `on_error` together raises `TypeError` before anything is copied.
    Every `copy()` override takes it and passes it on only when it was given;
    a native walk may call it from a worker thread, and children already
    running when one is declined are each offered their own error before the
    copy stops.
  - `progress(path, bytes_copied, total_size | None)` per chunk of each file
    streamed; not called by a native transfer that bypasses the streaming copy.
  - A `str` target goes through `_coerce_target()`: `with_segments()` by
    default, a URI parse on a class for URI references (so a `str` can name
    another scheme).
- `copy_into()` — `copy(target_dir / self.name, ...)`: the same
  keywords and defaults as `copy()`, and the new path is returned (pathlib
  3.14 returns it too). A `str` directory is a path on this backend, as for
  `copy()`. `ValueError` for a path with no name. Present on every backend
  and version; on 3.14 it replaces stdlib's, which overwrote and copied
  trees by default.
- `move_into()` — `move(target_dir /
  self.name, overwrite=overwrite)`, returning the new path.
- `replace()` — `move(target, overwrite=True)`, returning
  `target` as a path. A file replaces a file and a directory an empty
  directory; a directory that holds anything raises `OSError(ENOTEMPTY)`
  and a file onto a directory `IsADirectoryError`, both before anything is
  removed. `LocalPath` keeps stdlib's `os.replace()`.
- `move()` — validates first (missing source →
  `FileNotFoundError`, file onto directory → `IsADirectoryError`, existing
  target without `overwrite` → `FileExistsError`; a same-file spelling is
  renamed in place), and refuses before it removes anything a target that
  holds the source (`OSError(ENOTEMPTY)`), lies inside it
  (`OSError(EINVAL)`) or is a link to the same file (`OSError(EINVAL)`).
  Sameness is decided as for `copy()`. Two hard links of one file are two
  names: an existing target needs `overwrite=True`, and the source name is
  then removed, so the target name keeps the content. Tries `rename()`
  when `_rename_compatible(target)` (default: only onto a path of the very
  same class; a class for URI references leaves it to `rename()`, which raises
  `NotImplementedError` for a target it cannot reach; `LocalPath` requires a
  local target), falling back to `copy(recursive=True)` + `rm`/`unlink` on
  `NotImplementedError` or `OSError(EXDEV)`. The fallback recreates a
  symlink, and the links inside a moved tree, at a target whose class
  implements `_symlink_to()`, so the result does not depend on whether
  `rename()` was available; any other target receives what the links point
  at. `overwrite=True` replaces a
  local file atomically (`replace()`); elsewhere the target is unlinked just
  before the rename. Returns `rename()`'s result (`None` on the fallback).

### `FsPathLike`, `PathLike`, `PurePathLike`

```python
class FsPathLike(Protocol):
    def __fspath__(self) -> str: ...
```

`Protocol` with `__fspath__()`, for annotations. A class qualifies by defining
`__fspath__` itself, which is also how `os.PathLike` recognises it:
`LocalPath`, `PosixPathname` and `WindowsPathname` do (from `pathlib.PurePath`),
and `Pathname`, `MemPath`, `FTPPath` and the archive paths do not, so
`isinstance(path, os.PathLike)` is False for them and `os.fspath(path)` is a
`TypeError`. A class of your own that has a local name defines `__fspath__`
and is then an `os.PathLike`.
**`PathLike`** = `str | Path`; **`PurePathLike`** = `str | Pathname`.

### For path implementers (`pathapi.path`, `pathapi.providers`, `pathapi.archive`)

The names a class built on `Path` overrides or calls beyond the stubs above,
in one place: the classes of the package use all of them, so they are contract. The
helpers are imported from `pathapi.providers`, except `detect_format`,
which is in `pathapi.archive`.

```python
class Pathname:
    def _anchor_segments(self) -> tuple[str, ...]: ...

class Path(Pathname, Move, Copy, Identity, Create, Symlink, Remove, Listing,
           Resolve, Readlink, Hardlink, Owned, Stat, Chmod, Chown, Utime, BinaryOpen):
    def _scandir(self) -> Iterator[Tuple[str, Optional[FileStat]]]: ...
    def _same_filesystem(self, other) -> bool: ...
    def _coerce_target(self, target) -> Path: ...
    def _open(self, mode="r", buffering=-1) -> IOBase: ...
    def _chown(self, uid, gid, *, follow_symlinks=True) -> None: ...
    def _utime(self, *, atime=None, mtime=None) -> None: ...
    def _copy_from(
        self, source, follow_symlinks=True, preserve_metadata=False,
    ) -> None: ...
    def _write_batch(self) -> ContextManager[object]: ...

def check_follow(name, policy) -> None: ...
def contains(src, target) -> bool: ...
def copy_error_predicate(ignore_error, on_error) -> OfferedOnce | None: ...
def detect_format(name, peek=None) -> str: ...

class DiscardOnError:
    def discard(self) -> None: ...
    def _holds_uncommitted(self) -> bool: ...

class CommitOnClose(DiscardOnError, BytesIO):
    def __init__(self, commit, initial=None): ...
    def discard(self) -> None: ...
```

- Primitives, by protocol ("Protocols" has the table): `stat`; `chmod`;
  `_chown`; `_user_name`, `_group_name` (for `owner()`/`group()`); `_utime`;
  `_mkdir`; `_open`; `iterdir`; `unlink`, `rmdir`; `readlink`; `_symlink_to`;
  `_hardlink_to`; `_cwd`; `rename`; `checksum`. Implement what the backend can
  and leave the rest: `pathapi.protocols.supports(path, Protocol)` then
  tells a caller, without a call that raises, which protocols the class has.
- `_supports(protocol)` — optional: return `True` or `False` for a protocol
  whose answer depends on the connection (a server that lacks an extension),
  `None` to leave it to the class. `supports()` asks it first for an instance.
  It may make at most one request per backend, cached for the backend's
  lifetime, and it never raises: when the request fails it returns `None`.
- `_anchor_segments()` — the leading `segments` that anchor the path instead
  of naming a directory (`("",)` for a rooted path, else `()`); `parents`
  stops there. Override it when the root is spelled differently (a class for
  `file:` URIs anchors at a Windows drive or share).
- `info` — an object with the methods of `pathlib.types.PathInfo`
  (Python 3.14): `exists()`, `is_dir()`, `is_file()` (each with
  `follow_symlinks=True`) and `is_symlink()`. A stat that fails with
  `OSError`/`ValueError` reads as "no such entry". It calls `stat()` at most
  once following links and once not (an entry that is missing or not a link
  shares its non-following stat), and remembers the answers on the info object
  alone: every `path.info` is a fresh question, since a path holds no state.
  `LocalPath` has pathlib's own on 3.14 and this one before.
- `_copy_from(source, follow_symlinks=True, preserve_metadata=False)` — the
  hook Python 3.14's `pathlib.Path.copy(target)` calls on a target that is
  not a stdlib path (after `target.with_segments`, `target.parents` and `==`
  have said it is not the source): `pathlib.Path(f).copy(MemPath("/x"))`
  works on 3.14, and returns `target.joinpath()`. It copies `source` (anything
  with `info`, `iterdir()`, `readlink()`, `open()` and `stat()`) as 3.14 does:
  an existing file is overwritten, an existing directory raises, children are
  copied one by one, `follow_symlinks=False` makes a link through
  `symlink_to()`, and `preserve_metadata` copies the permission bits and the
  modification time where the class can set them (`chmod()`, `_utime()`).
  `Path.copy()` of this library does not call it. A class that mixes a
  stdlib path keeps stdlib's.
- `_write_batch()` — a context manager that `copy(recursive=True)` enters
  around the writes it makes below its target and `PathSyncer.sync()`
  around a whole sync of its root target (never for a dry run). Override it
  in a class whose every write is expensive, to hold them and commit them
  together when the block ends (`ArchiveUri` returns its `batch()`): entries
  nest and must join, and an exception must undo what was held. It must
  read nothing and raise nothing; the default does nothing.
- `_scandir()`, `_same_filesystem()`, `_coerce_target()` — see `Path`.
  `_open()`, `_chown()` and `_utime()` — see `BinaryOpen` and `Chmod`.
- `check_follow(name, policy)` — `ValueError` naming `name` unless `policy` is
  `True`, `False`, `None` or a callable. An `rm()` override calls it for
  `follow_symlinks` and `follow_binds` before it removes anything.
- `contains(src, target)` — whether `target` is `src` or lies inside it,
  looking through symlinks and deciding as `copy()` does (`_node_key()`,
  else `_same_filesystem()` and `==`). A `copy()` override that copies a
  tree natively refuses a target inside the source with it.
- `copy_error_predicate(ignore_error, on_error)` — `None` when `on_error` was
  not given, else the `OfferedOnce` predicate to ask about each failing child;
  `TypeError` when `ignore_error` was given too. A `copy()` override calls it
  first, passes `on_error=` on to `super().copy()` and to its children only
  when it is not `None`, and offers the source child that failed.
- `detect_format(name, peek=None)` — `"zip"` or `"tar"` from the extension of
  `name`, else from the first bytes `peek()` returns (called only then).
- `CommitOnClose(commit, initial=None)` — an in-memory `BytesIO` that calls
  `commit(buffer)` once at `close()`, before the buffer is closed: always for
  a new file, only after a write for one opened with `initial` content. Return
  it from `_open()` for a backend that cannot write a file piecewise. It
  inherits `DiscardOnError`; its `discard()` drops the buffer so that a later
  `close()` commits nothing. A buffer garbage collected unclosed while it
  holds something to commit is discarded and emits a `ResourceWarning`: close
  the file to commit it.
- `DiscardOnError` — mixin for a write stream that commits when it is closed
  (an upload, a rewrite of an archive), so that a write which failed commits
  nothing. Its `__exit__` calls `discard()` when the `with` block over the
  stream ended in an exception (any `BaseException`) and then closes; a block
  that ended normally just closes, and `close()` outside a `with` block
  commits, as before. The subclass defines `discard()` (the mixin's raises
  `NotImplementedError`): drop what was written so that `close()` commits
  nothing, and do nothing more when called twice or after `close()`. Put the
  mixin first in the bases (`class Upload(DiscardOnError, io.BytesIO)`), so
  that its `__exit__` wins over `io.IOBase`'s. The target is then left as it
  was, and is not created if it was absent. A stream garbage collected
  unclosed is discarded the same way, with a `ResourceWarning`, when
  `_holds_uncommitted()` (true by default) says closing would commit something;
  override it to return false for a stream that was never modified.
- A stream returned by `_open()` — in binary mode `open()` returns it
  untouched, so its own `__exit__` is the mechanism: a stream that defines
  `discard()` without inheriting `DiscardOnError` (or doing the same in its own
  `__exit__`) is not discarded there. In text mode the wrapper `open()` builds
  calls the stream's `discard()` whenever it has a callable one, mixin or not,
  and drops the text it still holds instead of writing it to the stream; it
  also discards when its final flush fails or when it cannot be built (an
  unknown `encoding=`). A stream without `discard()` is used as it is, which is
  right for one that writes through as it goes (`LocalPath`, `MemPath`, an SFTP
  path): a failed block leaves what it had written.

## Local filesystem (`pathapi.fspath`)

### `LocalPath`

```python
class LocalPath(pathlib.Path, Path):
    def stat(self, *, follow_symlinks=True): ...
    def chmod(self, mode, *, follow_symlinks=True): ...
    def is_dir(self, *, follow_symlinks=True): ...
    def is_file(self, *, follow_symlinks=True): ...
    def exists(self, *, follow_symlinks=True): ...
    def read_text(self, encoding=None, errors=None, newline=None) -> str: ...
    def write_text(self, data, encoding=None, errors=None, newline=None): ...
    def _scandir(self): ...
    def _chown(self, uid, gid, *, follow_symlinks=True) -> None: ...
    def _utime(self, *, atime=None, mtime=None) -> None: ...
    #  Path's signatures: glob, rglob, walk, symlink_to, _symlink_to, copy,
    #  copy_into, move, move_into, rm
```

`pathlib.WindowsPath`/`PosixPath` (by `os.name`) with
`Path` mixed in; stdlib behavior except where overridden: `_scandir()`
(tuples from `os.scandir` lstat), `walk()`, `glob()`,
`stat()`/`chmod()` (`follow_symlinks=` on 3.9; `chmod` accepts octal
strings), `is_dir()`/`is_file()` (`follow_symlinks=` before 3.13),
`_symlink_to()`, `_chown()` (`shutil.chown`; `NotImplementedError` where
`os.chown` is missing, i.e. Windows), `_utime()` (`os.utime`), plus pathapi's `copy`, `move`,
`copy_into`, `move_into`, `exists`, `rglob`, `read_text`, `write_text` and
`symlink_to`.
`exists()` returns `False` for any `OSError`/`ValueError` on every Python
version; `is_dir()`, `is_file()`, `is_fifo()`, `is_socket()`,
`is_block_device()` and `is_char_device()` are stdlib's: before 3.13 they
raise for an error other than ENOENT, ENOTDIR, EBADF or ELOOP (a
`PermissionError` from `stat()`, say), from 3.13 they return `False`. A
stdlib `pathlib.Path` is not a `pathapi.Path`, and `MemPath` is not a
stdlib path.

### `PosixPathname`, `WindowsPathname`

```python
class PosixPathname(PurePosixPath, Pathname): ...
class WindowsPathname(PureWindowsPath, Pathname): ...
```

pure classes over
`PurePosixPath`/`PureWindowsPath` implementing `Pathname`.

## In-memory filesystem (`pathapi.mempath`)

### `MemPath`

```python
class MemPath(Path):
    def __init__(self, *segments, backend=None): ...
    def relative_to(self, other, *, walk_up=False) -> Self: ...
    backend
    normalized

class MemBytesIO(BytesIO):
    def __init__(self, dest, *, append=False): ...
```

`Path` over nested dicts; the
reference `Path` subclass. Segments may be `str`, `Pathname` or `MemPath`
(the first `MemPath` argument names the backend, as the left operand does
for `/` and `joinpath()`, and an explicit `backend=` wins over it; another
`Path` → `NotImplementedError`). Joined and normalized like `PurePosixPath`.
An unknown keyword → `TypeError`.

- `backend` (a `MemPathBackend`); `normalized` is the list of names with `..`
  applied to the text and clamped at the root (`[""]` for the root);
  `segments` is a tuple (`("", "a")` for `/a`); `parts` is `(segments, backend)`; `as_uri()` →
  `mempath:<quoted posix path>` (no `mempath:` scheme is registered; build
  `MemPath` directly).
- `..` is applied to the tree, as on a POSIX filesystem: the directory it
  leaves must exist (`/missing/../a` → `FileNotFoundError`, `/f/../a` with
  `f` a file → `NotADirectoryError`), and above the root it stays at the
  root.
- `stat()` → `FileStat` with `st_size` and `st_mtime` (time of the last
  write); the mode is a placeholder (`mode_known=False`).
- `open()` supports `r`, `w`, `x`, `a` (binary or text); `+` modes →
  `NotImplementedError`. A binary handle is a `MemBytesIO`, a `BytesIO` that
  writes its buffer back into the file's `bytearray` (`dest`) on `flush()` and
  close; with `append=True` every write lands at the end. Writes are visible after `flush()`/`close()`; two
  `a` handles on one file both land (each adds only what it wrote). A `with`
  block that raised leaves what it had written, as a local file does.
- `rmdir()` of the root → `OSError(EBUSY)`, so `root.rm(recursive=True)`
  empties the tree and then raises that.
- `_utime()` sets a file's modification time (the access time is not
  kept); a directory has none (`NotImplementedError`), so `touch()` leaves
  one alone.
- `relative_to(other, *, walk_up=False)` takes a `MemPath` of the same
  backend or a `str` and returns the remainder as a relative `MemPath` on that
  backend, comparing the names as written (no `..` applied), as `pathlib` does.
  `ValueError` when `self` is not below `other`, when only one of them is
  rooted, or when the backends differ. With `walk_up=True` (on every
  interpreter) a path beside `other` gets a `..` per level climbed; a `..` in
  `other` cannot be walked (`ValueError`).
- Not implemented (`NotImplementedError`): `rename()` (`move()` copies),
  `chmod()`, `symlink_to()`.
- A `str` destination to `copy()`/`move()` stays on the same backend.

### `MemPathBackend`

```python
class MemPathBackend(dict): ...
```

Storage: `dict` value = directory,
`bytearray` (`MemFile`, carrying `mtime`, which `copy`, `deepcopy` and
`pickle` keep on every version) = file. Pass one instance as `backend=` to
share a tree; each root `MemPath()` otherwise gets its own.

## FTP (`pathapi.ftp`)

Standard library only (`ftplib`, `ssl`); needs no extra and no URI.
`pathapi.FTPPath` is the same class, imported on first use.

### `FTPPath`

```python
class FTPPath(Path):
    backend
    def __init__(
        self, *segments, backend=None, host=None, port=21, user=None,
        password=None, tls=False, timeout=30.0, ssl_context=None, verify=True,
    ) -> None: ...
```

A `Path` for a directory tree on an FTP server. Nothing connects when a path
is built.

- **Construction.** `FTPPath("/pub/x", host="h", user="u", tls=True)` builds
  the path's own `FTPBackend` from the endpoint keywords (the same as
  `FTPBackend`'s). `backend=` instead shares one connection cache between
  paths built separately, or plugs in a `BaseFTPBackend` of your own. Giving
  `backend=` together with any endpoint keyword is a `TypeError`, and so is
  giving neither (unless a segment is an `FTPPath`, whose backend is kept).
  Segments are `str`, `FTPPath` or a pure path; an absolute segment restarts
  the join, empty and `.` segments collapse. Every path derived from one
  (`/`, `joinpath`, `parent`, `with_name`, `iterdir`, `relative_to`) keeps its
  backend.
- **Names.** The tree has no working directory: `pub/x` and `/pub/x` name one
  file, read from the server root, and `/` is sent for the root (never an
  empty argument, which a server reads as its working directory). `..` is
  sent as written.
- **Text forms.** `str()` is the path alone. `repr()` names the endpoint
  (`FTPPath('/pub/x', host='h', port=21)`, plus `user=` and `tls=True` when
  set) and never a password. `as_uri()` is `ftp://[user@]host[:port]/path`
  (`ftps:` with `tls`), without a password; `NotImplementedError` on a
  backend that is not an `FTPBackend`.
- **Identity.** `==` compares the names only (as `MemPath` does); two paths are
  one tree (`copy()`, `move()` and `PathSyncer` ask) when they share a backend
  object or their backends have equal `endpoint`s. `_node_key()` follows the
  same rule and applies `..` to the text. A pickle holds the text and the
  backend, which carries no connection and a password only if it was given
  one; `copy.copy` and `copy.deepcopy` share the backend.
- **Behavior.** Everything under "What a path does" below.

### `FTPBackend`, `BaseFTPBackend`

```python
DEFAULT_PORT = 21
DEFAULT_TIMEOUT = 30.0
IDLE_PROBE_SECONDS = 1.0

class FTPEndpoint(NamedTuple):
    host: str
    port: int
    user: str | None
    tls: bool

class BaseFTPBackend:
    endpoint
    def client(self) -> ftplib.FTP: ...
    def close(self) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *exc_info) -> None: ...

class FTPBackend(BaseFTPBackend):
    host
    port
    user
    password
    tls
    timeout
    ssl_context
    verify
    def __init__(
        self, host, port=21, *, user=None, password=None, tls=False,
        timeout=30.0, ssl_context=None, verify=True,
    ) -> None: ...
```

- `FTPBackend` is the connections to one server and account. A thread opens
  its control connection on first use and keeps it, so paths that share a
  backend share a connection per thread; connections are cached per (backend,
  endpoint, thread) in an LRU of 128, closed on eviction and when their thread
  ends. A connection that carried a command within the last
  `IDLE_PROBE_SECONDS` is used as it is; one idle for longer, or not yet used,
  is probed with `NOOP` and replaced when dead.
- `port` defaults to 21; `tls=True` is explicit FTPS on that same port (port
  990 is never assumed) with `PROT P`. The certificate and host name are
  verified with `ssl.create_default_context()` before `USER`/`PASS` are sent;
  `ssl_context` (e.g. `ssl.create_default_context(cafile=...)`) wins over
  `verify`; `verify=False` accepts any certificate. Data connections reuse the
  TLS session. No `user` logs in as `anonymous`. `timeout` bounds connect,
  replies and transfers (`None` = forever).
- `close()` closes every control connection the backend holds, whichever
  thread opened it; one a command is running on closes when that command
  ends. It is harmless twice and before any use, and a later use connects
  afresh. Leaving a `with` block calls it. Both belong to `BaseFTPBackend`,
  so a backend of your own is closed the same way. A `ValueError` for an
  empty `host`.
- `BaseFTPBackend.client()` is what to override to supply connections: a new
  connected, logged-in `ftplib.FTP`. `endpoint`, when set, is a hashable value
  naming the server and account (`FTPBackend`'s is an `FTPEndpoint`, which holds
  no password); without one only the same backend object is the same tree.

### What a path does

- A listing uses `MLSD` (UTC `modify`; mode from `unix.mode` or the `perm`
  fact); `stat()` uses `MLST` of the entry itself where the server's `FEAT`
  lists it (one reply on the control connection, `FEAT` asked once per
  connection), else the parent's `MLSD`; a server without `MLSD` falls back
  to `NLST` for a listing and `SIZE`/`CWD` for a stat. A `size` fact that is
  not a plain non-negative number is unknown (`st_size` 0). A listing is
  read whole inside the guarded call: a transient `4xx` reply is
  `OSError(EAGAIN)`, a name the client cannot decode is `OSError(EILSEQ)`
  for that directory only, and a listing that dies mid-transfer drops the
  connection, so the next request reconnects. `stat()` of another entry in
  a directory that holds an undecodable name is answered by `MLST`, or on a
  server with `MLSD` and no `MLST` by `SIZE`/`CWD` (after a reconnect).
- Reads download the whole file into memory; `"w"`/`"x"`/`"a"` (`APPE`)/
  `"r+"` buffer in memory and upload on close (`"x"` checks then writes). A
  `with` block that raised uploads nothing: the file is left as it was, or not
  created.
- `_utime()` sends `MFMT` (the modification time, UTC, whole seconds; the
  access time cannot be set) when the server's `FEAT` lists it (asked once
  per connection), else raises `NotImplementedError` without sending it.
- `rename()` on the same server (a `str` target is a sibling name; a path of
  another server is `NotImplementedError`, so `move()` copies). Whether an
  existing target is replaced is the server's: a POSIX one replaces it, a
  Windows one answers `550 File exists`, which is `FileExistsError` when the
  target is there (a reply that names a permission problem stays
  `PermissionError`). `chmod()` via `SITE CHMOD` (`NotImplementedError`,
  chained to the server's reply, when the server lacks it or refuses it, or
  `follow_symlinks=False`).
- Errors are the `OSError` family: a refused login is `PermissionError`, a
  connection that cannot be made or drops is `ConnectionError`.

## Archives (`pathapi.archive`)

Standard library only (`zipfile`, `tarfile`); needs no extra and no URI.
`pathapi.ArchivePath`, `ZipPath` and `TarPath` are the same classes,
imported on first use.

### `ArchivePath`, `ZipPath`, `TarPath`

```python
MEMBER_SPOOL_BYTES = 16777216
MAX_LINKS = 40

class ArchivePath(Path):
    archive
    backend
    def __init__(self, *segments, archive=None) -> None: ...
    @classmethod
    def backend_for(cls, archive, *, shared=True, identity=None): ...
    def refresh(self) -> None: ...
    def batch(self) -> ContextManager[None]: ...
    def readlink(self) -> LinkTarget: ...
    def _rm_tree(self, on_error, follows=False) -> bool: ...
class ZipPath(ArchivePath): ...
class TarPath(ArchivePath): ...
class LinkTarget(PurePosixPath):
    path: str
```

The members of an archive file. The segments are the member path; `archive=`
is the archive file as a `Path`, or a `str` or other `os.PathLike` (a
`LocalPath`). `ZipPath` and `TarPath` fix the format, `ArchivePath` reads it
from the extension (`.zip`/`.jar` vs `.tar`/`.tgz`/`.tar.*`), else from the
first bytes (a `PK` magic sniff) on first use.

- **Construction.** `ZipPath("d/x", archive="a.zip")`. A path with no
  `archive=` takes the one of a segment that is a path of the same family
  (`ZipPath(root, "x")`, `/`, `joinpath`, `parent`, `with_name`, `iterdir`,
  `relative_to` all keep it); with neither it is a `TypeError`, as it is for
  an `archive=` that is not a path, a `str` or an `os.PathLike`. `archive=`
  may also be the backend of another path of the archive: `backend_for(
  archive, *, shared=True, identity=None)` returns the backend to pass, the
  one shared by every path of that file and format, or with `shared=False`
  one of its own; `identity` names a non-local archive file in the registry
  (else its `_node_key()`). It reads nothing.
- **Names.** Member names are read from the archive root: `d/x` and `/d/x`
  are one member, and the empty path or `/` is the archive itself.
- **Text forms.** `str()` is the member path alone. `repr()` names the
  archive (`ZipPath('a/b', archive=LocalPath('x.zip'))`) without reading it.
  `as_uri()` is `zip:<archive uri>!/<member>` (`tar:`, `archive:`), with a
  literal `!/` in a member name written `%21/`.
- **Identity.** `==` compares the names only; two paths are one tree
  (`copy()`, `move()` and `PathSyncer` ask) when they share a handle, and
  `_node_key()` is that handle and the normalized member. A pickle holds the
  text and the archive file; `copy.copy` and `copy.deepcopy` share the handle.
- `archive` is the archive file, `backend` the shared handle (it settles the
  format of an `ArchivePath` on the first call), `refresh()` is described
  below, `batch()` and `readlink()` under Writes and Links. `_rm_tree(
  on_error, follows=False)` is the one-rewrite removal `rm(recursive=True)`
  uses, for a wrapper that keeps the generic walk for the rest: it returns
  False when the case is not that one, and asks `on_error(error, self)` about
  a failure.
- `ArchivePath` settles the format on first use, not when the path is built:
  constructing, printing, joining, copying and pickling a path read nothing,
  a non-local archive file that had to be read to decide is not fetched a second
  time, and a failure to read the outer propagates and decides nothing, so
  the next call tries again (an `ArchivePath` to an archive file that is missing
  and has no extension raises `FileNotFoundError` on a write, as it does on
  a read).
- One shared handle per archive file and format (keyed by the real local
  path, or by the node -- else the URI -- of a non-local archive file),
  released when no path references it, so paths built separately for one
  file see each other's writes. A local zip or tar is read from its file as
  needed (a compressed tar is decompressed from its start for each member it
  reaches) and no OS handle is held between calls; a non-local archive file is
  read whole into memory, once, and kept while any path to it lives.
  `refresh()` forgets what is held of it, so the next use reads it again
  (every path to the same archive shares it); it is how a change at a remote
  source is seen, and a local archive needs none (it is checked against the
  file on every use).
- Cost. The members, the directories and each entry's stat data are derived
  once per open handle, so a listing, a `walk()` or a `stat()` costs one
  validation of the file and work in the size of the directory, not of the
  archive. A member read is copied out of the shared handle: in memory up to
  `MEMBER_SPOOL_BYTES` (16 MiB) and past that into an
  unnamed temporary file, so memory stays small however large the member is,
  and the disk used is the member's size as the archive declares it (an
  archive can declare more than it holds). `open("r+")` and a write buffer
  hold the whole member in memory. A zip mutation copies the entries it does
  not change as they lie in the archive, without decompressing them, so it
  costs the archive's stored size; an encrypted member, or one stored with a
  compression method `zipfile` lacks, does not stop a change to another
  member.
- **Member names are normalized POSIX relative paths**, whatever the
  writer emitted and whichever format: a leading `./` (`tar -C dir .`,
  `shutil.make_archive`), empty segments (`a//b`) and interior `.`/`..`
  (`a/./b`, `a/b/../c`) resolve, so one member has one name and a listing
  and a lookup always agree. The spelling as written still addresses the
  member. A name that would leave the root -- `../x`, `/abs`, or a `..`
  with nothing to spend it on -- has no name inside the archive: it is
  never listed and never readable. A name only a *Windows destination* would
  misread (`C:drive.txt`, `a\b`) IS a member, because it is an ordinary POSIX
  filename; refusing to join it is the destination's rule, applied by
  whatever writes there (see `copy()` below, `PathSyncer`,
  `unpack_archive()`). On Windows `zipfile` rewrites `\` to `/` in a zip
  member name; elsewhere (and in a tar) a backslash name is a member like any
  other. When two spellings normalize to one name the
  later member wins, as in `zipfile`/`tarfile`. Exception types are POSIX on every
  platform.
- The archive root is the archive: `exists()`/`is_dir()`/`stat()` of it
  open the archive, so a missing one does not exist (`FileNotFoundError`
  from `stat()`) and a file that is not an archive raises
  `zipfile.BadZipFile` / `tarfile.ReadError`, as a listing does. So does any
  other damage that stops the archive from being read -- a bad deflate
  stream, a short or inconsistent header, a zip version `zipfile` does not
  know, a truncated or corrupt gzip/bzip2/xz stream -- whichever decoder
  noticed it, and never as `zlib.error`, `struct.error`, `EOFError` or an
  `OSError` without an errno. A member that is encrypted raises
  `RuntimeError`, and one stored with a compression method `zipfile` lacks
  `NotImplementedError`, as `zipfile` raises them. A member another thread's
  rewrite removed between the lookup and its stat is `FileNotFoundError`.
- **Links.** A tar `SYMTYPE` member and a zip entry made on Unix whose mode
  says link (`S_IFLNK`; its data is the text) are symbolic links.
  `is_symlink()` is True and `stat(follow_symlinks=False)` / `lstat()`, in a
  listing too, is a link (`S_IFLNK`, `st_size` the length of the text).
  `readlink()` is the text as a `LinkTarget`: a `PurePosixPath`, so spelled
  normalized, whose `path` is the text exactly as stored (what `copy()` and
  `PathSyncer` recreate a link from); `OSError(EINVAL)` for a member that is
  not a link, `OSError(EIO)` when the text cannot be read (a zip link over
  4096 bytes, or damaged data). `stat()`, `exists()`, `is_dir()`, `is_file()`,
  `open()` and `iterdir()` follow links inside the archive, on the way
  (`ln/x` is the member `x` of what `ln` points at) and at the end; a relative
  text is read from the directory that holds the link. A link that is
  absolute, goes above the root, has no readable text or points at nothing
  resolves to nothing: `FileNotFoundError` whose `filename` names the link,
  never a path outside the archive. A loop, or more than `MAX_LINKS` (40)
  links followed by one lookup, is `OSError(ELOOP)`. `walk()` and `glob()`
  list a link once and by default do not enter it; `rm(recursive=True)`
  removes the entry, never what it points at; `copy(follow_symlinks=False)`
  and a `PathSyncer(follow_symlinks=False)` recreate it with `symlink_to()`
  where the target can hold one. With `follow_symlinks=True` (`copy()`'s
  default) a listing follows one directory link per path and never a link to
  the directory that holds it or to one above it: such a link, and one met
  below another link, answers `OSError(ELOOP)` to a following `stat()`,
  `is_dir()` or read, so `walk()` and `glob()` list it as an entry and
  `copy()`, `PathSyncer` and `rm()` meet it as one that cannot be entered
  (an error for that entry offered to `ignore_error`, for `rm()` a link
  removed as a link). The entries a recursive operation visits are at most
  `(members + links) * (directory links + 1)` however the links point; spell
  the path (`root / "l1" / "l2" / "f"`, or start the walk there) to go
  further. Nothing is written
  through a link (`NotImplementedError`: a write, `mkdir()`, `unlink()` or
  `rename()` whose path passes through one, or a write that names one);
  `unlink()`, `rename()` and `rm()` of the link itself act on its entry, and
  no link can be created in an archive. A tar hard link is a second name for
  a member, not a symlink: it reads and stats as that member and
  `is_symlink()` is False.
- A file has no children: with a file `y` and a member `y/z`, `y/z` does
  not exist for `exists()`, `stat()`, `open()`, `unlink()` and `rename()`
  (`FileNotFoundError`; writing or `mkdir()` below `y` is
  `NotADirectoryError`), and no listing shows it.
- Writes: zip only, and only with a local archive file (else
  `NotImplementedError`). `"w"`/`"x"`/`"r+"`, `mkdir()`, `unlink()`,
  `rmdir()`, `rename()` (same archive; replaces like POSIX `rename`;
  a destination below a file or a missing directory is
  `NotADirectoryError` / `FileNotFoundError`, a directory into itself
  `OSError(EINVAL)`); parents must exist; `"a"` unsupported. A `with` block
  over a written member that raised changes nothing (inside `batch()` the
  member is not staged and the block's other changes still commit). Every mutation
  replaces the archive atomically (temp file + `os.replace`) and keeps
  other members' metadata, the comment and any prefix bytes. A write uses
  the normalized name. A new member is stored deflated; an existing member that is
  overwritten keeps its compression method. The archive is replaced by a
  new file, so another hard link to it keeps the old content and the
  writing user owns the result; an archive the caller may not write
  (`os.access(..., W_OK)`) is refused with `PermissionError`, on every
  platform, before anything is written.
- `batch()` — for a writable archive (`NotImplementedError` otherwise, as
  for any write): inside the block every change (`"w"`/`"x"`/`"r+"`,
  `mkdir()`, `unlink()`, `rmdir()`, `rename()`, `rm()`) goes to a member
  list held in memory (written data up to `MEMBER_SPOOL_BYTES`, then an
  unnamed temporary file), every path of the archive reads it, and the
  file is not touched. When the outermost block ends without an exception
  the archive is replaced ONCE, with the members, order and metadata that
  the same changes made one by one would leave; an archive that does not
  exist yet is created then. An exception leaves the file byte for byte as
  it was and drops the changes, and so does an archive another process
  replaced meanwhile (`OSError(EBUSY)`; their file stays). A block inside
  another joins it and commits or drops nothing itself. The block holds the
  archive for its thread: another thread's use of the archive waits for it.
  A change patches the pending member list, so the list is derived twice
  however many changes the block makes (once for the archive as the block
  finds it, once when it first holds a change); only a change to a name
  the archive spells two ways derives it again.
  `copy(recursive=True)` into a zip and `PathSyncer.sync()` onto one enter
  it themselves, so each is one replacement; a copy or sync that raises
  leaves the archive as it was, where the members written before the error
  used to stay.
- Removing a file never removes its directory: when `unlink()`, `rename()`
  or `rm()` takes the last member out of a directory that exists only
  through its members, the directory's own entry (`name/`) is written in
  the same rewrite. `rmdir()` of the root succeeds on an empty archive
  (`OSError(ENOTEMPTY)` otherwise) and removes nothing.
  `rm(recursive=True)` of a directory is ONE rewrite (unless a policy other
  than `follow_symlinks=False` has to meet each link under it), and raises
  `OSError(ENOTEMPTY)` removing nothing when the tree holds a member under
  a file.
- A member the archive spells several ways (`norm` and `./norm`) is one
  member: `unlink()`, `rename()`, `rmdir()` and `rm()` act on every entry
  that names it, and renaming over it replaces them all.
- `TarPath` (plain, gz, bz2, xz) is read-only.

### `make_archive`, `unpack_archive` (`pathapi.archive`)

```python
def make_archive(src, format, target) -> None: ...
def unpack_archive(archive, dest) -> None: ...
```

`make_archive()` (`format` `"zip"` or
`"tar"`, else `ValueError`; `src` file or directory of any `Path`; built in
a temporary buffer, written to `target` only when complete; zip64 always;
every directory is a member, empty ones too; a member carries the source's
modification time and, in a tar, its permission bits — a source that
reports no time gets 1980-01-01 (zip) or the epoch (tar), and one that
reports no mode gets 0o644 for a file and 0o755 for a directory; a tar
member is sized from the bytes read, not from `stat()`)
and `unpack_archive()` (format from the name, else magic
bytes; creates `dest`; non-seekable streams are buffered; members that
would leave `dest` are skipped, and for a Windows-flavoured `dest` so are
members with a part Windows would rewrite or send to a device
(`UserWarning`); tar hard links and links to regular files are extracted as
copies, other links — to nothing, to a directory, or in a loop — skipped
with `UserWarning`).

## HTTP requests (`pathapi.http`)

Standard library only (`http.client`, `ssl`); needs no extra and no URI.
`import pathapi.http` loads `http.client` and `ssl`; `import pathapi`
does not. The package sends a request through a **transport**, follows
redirects under one rule for credentials, and maps a failed request to the
`OSError` types `pathlib` raises. Path classes for HTTP servers in other
distributions are built on it.

```python
class HTTPClient:
    headers: dict
    timeout: float | tuple | None
    user_agent: str
    def __init__(
        self, transport=None, *, headers=None, timeout=(10, 60), user_agent=None,
    ): ...
    transport
    def request(
        self, method, url, *, data=None, headers=None, stream=False,
        allow_redirects=None, timeout=..., auth=None,
    ): ...
    def redirect(
        self, method, url, resp, *, headers=None, stream=False, timeout=...,
        auth=None,
    ): ...
    def close(self): ...

DEFAULT_TIMEOUT = (10, 60)
MAX_REPLY_BYTES = 8388608
```

- **`HTTPClient(transport=None, *, headers=None, timeout=(10, 60),
  user_agent=None)`** holds a transport and what every request is sent with.
  `transport` is any object with `send()` (the contract below); without one the
  client builds the transport that `PATHAPI_HTTP_TRANSPORT` names (the
  built-in `HTTPTransport` by default) on first use, and `close()` or leaving
  its `with` block closes a transport it built, never one it was given. A
  transport without a `send()` method is `TypeError`. `headers` are sent with
  every request, the call's headers merged over them by name (case-blind, a
  value of `None` removes the name) over `User-Agent: <user_agent>` (default
  `pathapi/<version>`) and `Accept: */*`. Every request gets
  `timeout=(10, 60)` seconds (`connect`, `read`) unless the client or the call
  gives one; `timeout=None` waits without limit. `repr()` shows no header.
- **`request(method, url, *, data=None, headers=None, stream=False,
  allow_redirects=None, timeout, auth=None)`** returns the final
  `HTTPResponse`. `data` is `bytes`, `str` (UTF-8), a binary file or an
  iterable of `bytes`; one that is not `bytes` cannot be sent twice, so it is
  not sent again after a redirect or a dropped connection. `auth` is a
  `(user, password)` pair sent as `Authorization: Basic`, or a `str` sent as the
  `Authorization` value (`"Bearer <token>"`), to the origin of `url` unless a
  header names an `Authorization`. A `url` that carries credentials,
  a URL or header value with a control character (CR and LF included) and
  another scheme than `http:` or `https:` are `ValueError`, before anything is
  sent. A failure of the connection is `TimeoutError`, `ConnectionError`,
  `ssl.SSLError` or `OSError(EIO)` as the transport raises them. A status of
  400 or more is **not** an error here: it is the reply (`raise_for_status()`).
  With `stream=False` the body has been read when `request()` returns, and one
  that decodes to more than `MAX_REPLY_BYTES` is `OSError(EFBIG)`; with
  `stream=True` the caller reads the body and closes the response.
- **Redirects.** `GET`, `HEAD` and `OPTIONS` follow up to 30 redirects (a `303`
  becomes a `GET`); `allow_redirects=False` returns the first answer. A redirect
  whose `Location` is missing or that cannot be requested (another scheme, a
  port out of range, a host that is not a name) is `OSError(EIO)` naming the
  status, never `ValueError`. A redirect that changes the scheme, host or port
  drops `Authorization` for that hop and every later one (a return to the first
  origin does not bring it back), also from `https:` to `http:`, and nothing
  else: every header other than `Authorization` goes to the new host as well,
  so keep a secret in `Authorization`. Every other method (`PUT`, `POST`,
  `PATCH`, `DELETE`, `PROPFIND`, ...) is sent without following: a 307 or 308
  (for `PROPFIND` also a 301, 302 or 303) to the same scheme, host and port is
  sent once more with the same method, headers and body, and any other 3xx, a
  redirect to another origin or a second redirect is `OSError(EIO)` naming the
  status and the `Location` (userinfo, query and fragment removed). A body
  that is not `bytes` cannot be sent again, so it raises too.
- **`redirect(method, url, resp, *, headers=None, stream=False, timeout,
  auth=None)`** sends `method` to where `resp`, the answer to a request for
  `url` made with `allow_redirects=False`, redirects to, and follows what that
  redirects to in turn. `Authorization` is left off whenever the target is
  another scheme, host or port than `url`'s. No usable `Location` is
  `OSError(EIO)`.

```python
class HTTPTransport:
    def __init__(self, *, ssl_context=None, verify=True, proxies=None): ...
    def send(
        self, method, url, *, headers, body=None, timeout, stream=False,
    ): ...
    def close(self): ...

class HTTPResponse:
    status: int
    reason: str
    headers: Headers
    url: str
    def __init__(
        self, status, reason, headers, url, body=b"", *, decode=True,
    ): ...
    def read(self, size=-1): ...
    def iter_bytes(self, chunk_size=65536): ...
    def buffered(self): ...
    def close(self): ...

class Headers:
    def get_all(self, name): ...
```

- **The transport contract.** Any object with `send(method, url, *, headers,
  body=None, timeout, stream=False) -> HTTPResponse` and `close()` is a
  transport (a context manager is optional). `send()` makes exactly one
  request: it follows no redirect, adds no credentials and retries nothing
  but what is stated below. `body` is `None`, `bytes`, a binary file or an
  iterable of `bytes` (never `str`: `TypeError`). A body is sent under a
  `Content-Length` when its length is known without reading it: `bytes`, a
  list or tuple of `bytes`, a file that is seekable (the bytes from `tell()`
  to its end, the position restored) and a regular file (its size less
  `tell()`). Any other body (an iterator, a generator, a pipe or another
  stream that cannot report its size) is sent `Transfer-Encoding: chunked`. A
  `Content-Length` in `headers` is trusted: that many bytes are sent and
  nothing is chunked, a longer body is cut there, a count that is not a number
  or is larger than a body whose length is known is `ValueError` before
  anything is sent, and a body that runs out first is `OSError(EIO)` with its
  connection closed. No body is sent past its length (a file that grew after it
  was measured is cut there). A file or iterator is never rewound and never
  sent twice. `timeout` is seconds, a `(connect, read)` pair or `None` (no
  limit); the read half bounds each wait on the server, not the whole reply.
  With `stream=False` the body has been read when `send()` returns; with
  `stream=True` it is read from the response, which the caller closes (an
  unread rest of a body closes its connection). A transport raises only
  `TimeoutError`, `ConnectionError`, `ssl.SSLError`, `ValueError` (before
  anything is sent, for a header or URL with a control character, a URL with
  credentials, or another scheme) or else `OSError(EIO)`; no third-party
  exception and no URL or header value is in its text. A reply below 200 is
  never the one `send()` returns: `HTTPTransport` skips interim (1xx) replies,
  at most 8, and past that is `OSError(EIO)` with its connection closed.
- **`HTTPResponse`**: `status`, `reason`, `headers` (read-only, names compare
  without case, a name sent twice reads as `"a, b"` and `headers.get_all()`
  lists them; `repr()` shows names only), `url` (the URL requested, without
  fragment; an empty path is `/`), `read(size=-1)`, `iter_bytes(chunk_size)`,
  `buffered()` (reads the rest of the body into memory, closes the connection
  and returns the response, which stays readable: what `send(stream=False)`
  returns), `close()` and a context manager. A `gzip` or `deflate` body is
  decoded, in steps of at most 256 KiB of output per step, unless the request
  set an `Accept-Encoding` naming a coding of its own (`identity` counts as
  none); `headers` stay as received, so `Content-Length` counts the encoded
  bytes. A body that ends before the length the server stated, a chunked body
  cut short, and a coded body that is not valid are `OSError(EIO)`, never a
  shorter read. A transport builds one from `bytes` or from an object with
  `read(amount)` and `close()`; a fake transport in a test does the same.
- **`HTTPTransport(ssl_context=None, verify=True, proxies=None)`** is standard
  library only. `https:` is verified against `ssl_context`, else against
  `ssl.create_default_context()` (the system trust store, not certifi);
  `verify=False` is the one way to turn verification off (each transport built
  with it logs a warning under the `pathapi.http` logger), and `ssl_context`
  wins over it. Each thread keeps one idle connection per server (at most 8,
  closed when the thread ends or on `close()`), reused for its next request
  unless the server has closed it or sent bytes on it since (checked before
  anything is sent). A request that fails before any byte of a reply on a
  reused connection that the server had closed is sent once more on a new
  connection **only** if it is `GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE` or
  `PROPFIND` and its body is `None` or in memory; any other request uses an
  idle connection only if it carried a reply within the last second. A timeout
  is never retried. `stream=False` holds the whole body in memory; read a body
  that may be large with `stream=True`.
- **Proxies.** `proxies=None` reads the environment (`HTTP_PROXY`,
  `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, through `urllib.request`, which
  matches names and suffixes in `NO_PROXY`; a CIDR block there, such as
  `10.0.0.0/8` or `fd00::/8`, also sends an IP-literal host direct, and a host
  name is never resolved to be matched against one). Otherwise `proxies` is
  indexed with the request URL (no fragment): it returns `None` (direct), one
  proxy, or an iterable of them to try in order when a connection cannot be
  made (`None` in the order is a direct attempt, and an empty iterable is
  direct as well); `KeyError` means direct and the environment is not read;
  `{}` sends everything direct (unlike other clients, which still read the
  environment); an exception of the provider other than `KeyError` reaches the
  caller as it is. A proxy is a `str` URL or any object with a `url`
  attribute, so a proxy resolver from another library works as it is, with none
  imported here:

  ```
  class Resolver:  # tries the first proxy, then connects directly
      def __getitem__(self, url):
          return ["http://user:secret@proxy.example:3128", None]

  transport = HTTPTransport(proxies=Resolver())
  ```

  A plain `dict` may use the keys the `requests` library does (`"http"`,
  `"https"`, `"all"`, `"scheme://host"`). Only `http://` proxies work:
  absolute-form requests for `http:` and `CONNECT` for `https:`, with
  `Proxy-Authorization: Basic` from the proxy URL's userinfo (sent to the proxy
  only); any other scheme (`socks5`, `https`) is `OSError(EPROTONOSUPPORT)`
  naming the scheme and `pathapi-requests`. A proxy that refuses `CONNECT`
  is a `ConnectionError` and the next proxy is not tried. Proxy credentials are
  in no message, `repr()` or traceback.

### Replies and errors (`pathapi.http`)

```python
class HTTPStatusError(Exception):
    response: HTTPResponse
    def __init__(self, response): ...

def raise_for_status(resp): ...
def read_limited(resp, what, limit=None): ...
def json_body(resp, what, limit=None): ...
def drop_body(resp, limit=None): ...
def translate_errors(subject, *, conflict=FileExistsError): ...
def os_error(error_class, subject): ...
def server_size(text): ...
def peer_text(text, limit=120): ...
def split_userinfo(url): ...
def is_redirect(resp): ...
```

- `raise_for_status(resp)` raises `HTTPStatusError` (holding `response`) for a
  status of 400 or more.
- `read_limited(resp, what, limit=None)` is the whole decoded body of `resp`,
  which may be at most `limit` bytes (default `MAX_REPLY_BYTES`, 8 MiB after
  decoding, however small it is on the wire; assign
  `pathapi.http.MAX_REPLY_BYTES` to allow more, it is read each time):
  more is `OSError(EFBIG)` naming `what`, and the connection is closed.
  `json_body()` is that, parsed (`ValueError` when it is not JSON).
- `drop_body(resp, limit=None)` gives up the body of a reply nobody looks at: it
  is read up to `limit` bytes so the connection can serve the next request, and
  `resp` is closed, which closes the connection when the body was longer.
- `translate_errors(subject, *, conflict=FileExistsError)` is a context manager
  that maps what a request raised to what `pathlib` raises for `subject` (a path
  or URL, shown through `str()`): an `HTTPStatusError` of 404 or 410 becomes
  `FileNotFoundError`, 401 or 403 `PermissionError`, 405 or 501 `PermissionError`
  naming the status, 409 `conflict` (a write passes `FileNotFoundError`: RFC 4918
  uses 409 for a missing parent collection), any other status `OSError(EIO)`
  naming it (`HTTP Error 502 Bad Gateway for <subject>`). A `TimeoutError` or
  `ConnectionError` is raised again with a message holding `subject` and the
  exception's type name. The cause is never chained, so a URL, a header or a
  credential that the transport's error quoted is in no message or traceback.
  `os_error(error_class, subject)` builds one such exception as `pathlib` does,
  with `errno` and `filename` set.
- `peer_text(text, limit=120)` is text that came from the other end (a reason
  phrase, a `Location`, a header value) as an error message may show it:
  control, line-separator and bidirectional characters escaped, cut to `limit`.
  `server_size(text)` is a byte count a server stated as an `int`, or `None` when
  it is not a plain non-negative decimal number. `split_userinfo(url)` is
  `(url_without_userinfo, (user, password) | None)`, each part the octets its
  percent-escapes stand for (`str` when ASCII, else `bytes`); `is_redirect(resp)`
  says whether a reply is a 301, 302, 303, 307 or 308 that names a `Location`.

### For transport implementers (`pathapi.http`)

```python
TRANSPORT_GROUP = "pathapi.http_transports"
TRANSPORT_ENV = "PATHAPI_HTTP_TRANSPORT"
def transport_names() -> tuple[str, ...]: ...
def get_transport(name: str) -> type: ...
def default_transport(): ...

class PreparedRequest(NamedTuple):
    method: str
    url: str
    headers: dict[str, str]
    body: Any
    timeout: tuple[float | None, float | None]
    decode: bool

def prepare_request(
    method, url, *, headers, body=None, timeout,
) -> PreparedRequest: ...
```

A distribution provides an HTTP transport by declaring an entry point in the
group `pathapi.http_transports`: the name is the transport's (`requests`)
and the value names a class that is built with no arguments (`pyproject.toml`:
`[project.entry-points."pathapi.http_transports"]`, `requests =
"pathapi_requests:RequestsTransport"`). The class implements "The transport
contract" above: `send()` and `close()`.

- `transport_names()` — the transport names, sorted: `builtin`, every entry of
  the group of an installed distribution, and `requests` whether or not its
  distribution is installed. Nothing is imported to list them.
- `get_transport(name)` — the class called `name`; the name is case-sensitive.
  `builtin` is always `HTTPTransport`: an entry called `builtin` in the group is
  ignored. A transport that another distribution provides and that is not
  installed raises `ImportError` naming it (`pip install pathapi-requests`
  for `requests`); any other name is a `ValueError` listing `transport_names()`;
  an entry that does not load a class is a `TypeError`.
- `default_transport()` — a new transport of the kind
  `PATHAPI_HTTP_TRANSPORT` names, which is what a client given no transport
  builds: `get_transport(name)()`. An empty, differently cased or unknown value
  is a `ValueError` naming the choices.
- `prepare_request(method, url, *, headers, body=None, timeout)` — the arguments
  of `send()` as the contract reads them, so a transport that starts with it
  refuses what the built-in transport refuses and frames a body the same way.
  `method` is upper-cased and the fragment is dropped from `url`. `headers` come
  back as a `dict` of text values with the `Content-Length` of a body whose
  length is known (`0` for a `PATCH`, `POST` or `PUT` without one) and
  `Accept-Encoding: gzip, deflate` unless the caller named an encoding. `body` is
  `None`, `bytes`, an object that yields exactly the `Content-Length` bytes (a
  file or a list of `bytes`, cut at the length it was measured at) or an
  iterable of `bytes` to send chunked. `timeout` is the `(connect, read)` pair.
  `decode` is what to pass as `HTTPResponse(..., decode=decode)`. It raises
  `ValueError`, before anything is sent, for a method or header name that is not
  a token, a header value or URL with a control character, a URL with
  credentials or another scheme, a `Content-Length` that is not a count of bytes
  or exceeds the body, and a timeout that is not a positive number, `None` or a
  pair of them; and `TypeError` for a `str` body. No message quotes the URL or a
  header value.

The suite that holds a transport to these promises ships with the package:
"Testing support" under "Testing helpers".

## Protocols (`pathapi.protocols`)

`Path` is a class statement over these protocols. Each is a `typing.Protocol`
with default bodies, subclassed explicitly as a mixin: a class lists the ones
it implements, supplies the **primitives** (stubs that raise
`NotImplementedError`) and gets the derived operations. A read-only backend is
`class X(Pathname, Listing, Stat, BinaryOpen)` with no `Path`; list a protocol
before the ones it is built on (`Listing` before `Stat`), as Python's MRO
requires. `Path` lists `Copy` before `BinaryOpen`, so `Path.copy` is the path
copy and not the stream copy.

| Protocol | Module | Primitives | Derived | Bases |
| --- | --- | --- | --- | --- |
| `Named` | `pure` | none (structural only) | `name`, `segments`, `parent`, `with_segments()`, `joinpath()`, `/`, `as_posix()` | none |
| `FileStatLike` | `fs` | `st_mode`, `st_size`, `st_mtime` | | none |
| `Stat` | `fs` | `stat()` | `lstat()`, `exists()`, `is_dir()`, `is_file()`, `is_symlink()`, `is_block_device()`, `is_char_device()`, `is_fifo()`, `is_socket()`, `info`, `is_junction()`, `is_mount()`, `is_dir_binding()` | none |
| `Chmod` | `fs` | `chmod()` | `lchmod()` | `Chown`, `Utime` |
| `Owned` | `fs` | `_user_name()`, `_group_name()` | `owner()`, `group()` | `Stat` |
| `Chown` | `fs` | `_chown()` | `chown()` | none |
| `Utime` | `fs` | `_utime()` | | none |
| `Create` | `fs` | `_mkdir()` | `mkdir()`, `touch()` | `Stat`, `Chmod`, `Utime` |
| `BinaryOpen` | `io` | `_open()` | `open()`, `read_bytes()`, `read_text()`, `write_bytes()`, `write_text()`, `copy()` | none |
| `Listing` | `tree` | `iterdir()` | `_scandir()`, `glob()`, `rglob()`, `walk()`, iteration | `Stat` |
| `Remove` | `tree` | `unlink()`, `rmdir()` | `rm()` | `Listing` |
| `Readlink` | `links` | `readlink()` | | `Stat` |
| `Symlink` | `links` | `_symlink_to()` | `symlink_to()`, `_symlink_target()` | `Remove` |
| `Hardlink` | `links` | `_hardlink_to()` | `hardlink_to()` | `Stat` |
| `Resolve` | `resolve` | `_cwd()` | `absolute()`, `resolve()` | `Readlink` |
| `Identity` | `transfer` | none | `samefile()`, `_same_filesystem()`, `_node_key()` | `Stat` |
| `Copy` | `transfer` | none | `copy()`, `copy_into()`, `_copy_from()`, `_coerce_target()`, `_write_batch()` | `Identity`, `Create`, `Symlink`, `Readlink`, `BinaryOpen` |
| `Move` | `transfer` | `rename()` | `move()`, `move_into()`, `replace()` | `Copy`, `Remove` |
| `NativeChecksum` | `checksum` | `checksum()` | `supported_checksums()` | none |

One protocol per primitive that a backend can have without the others: an
archive reads links and makes none (`Readlink` and not `Symlink`), a server may
set times and not modes, and `Owned` reads names where `Chown` writes ids.
`BinaryOpen` is one protocol: a single `_open(mode)` serves reading and writing,
and a mode the backend cannot honour raises `NotImplementedError` per call.
`Create` also calls `open()` and `unlink()` in `touch()`. All names are
imported from `pathapi.protocols`; the module column is where each one
is defined.

### `supports`, `capabilities`

```python
def supports(obj, *protocols) -> bool: ...
def capabilities(obj) -> FrozenSet[type]: ...
```

`supports(obj_or_class, *protocols)` is true when every primitive of every
protocol named is something other than its default stub; `capabilities()`
lists the protocols for which that holds. Ask once instead of calling and
catching `NotImplementedError`. A primitive also counts as implemented when
another library defines the operation it feeds: `pathlib`'s `mkdir` for
`_mkdir`, `open` for `_open`, `absolute` for `_cwd`, `hardlink_to` for
`_hardlink_to`, `chown` for `_chown`, `symlink_to` for `_symlink_to`, `owner`
for `_user_name` and `group` for `_group_name`; so `LocalPath` supports `Owned`
where `pathlib` defines `owner()` and `group()`, which includes Windows, where
they exist and raise `NotImplementedError` when called. A protocol with no primitives
(`Identity`, `Copy`) is supported by a class that has it as a base. A class
whose answer depends on its connection defines `_supports(protocol) -> bool |
None`, asked first for an instance; `None` falls back to the answer from the
class. It may make at most one request per backend, cached for the backend's
lifetime, and never raises: a failed request answers `None`. The answer is
advisory, like `supported_checksums()`: a primitive may still raise
`NotImplementedError` for an argument it cannot handle.
`TypeError` for no protocol and for something that is not a protocol of this
package (`Named`, `FileStatLike` and the like have no primitives to ask).

### `pure.Named`

```python
class Named(Protocol):
    name
    segments
    parent
    def with_segments(self, *segments): ...
    def joinpath(self, *args): ...
    def as_posix(self): ...
```

Structural only: `Pathname` and `pathlib.PurePath` satisfy it without naming
it, and nothing subclasses it. It is the pure half that `glob`, `walk` and
`sync` read.

### `fs.FileStatLike`

```python
class FileStatLike(Protocol):
    st_mode: int
    st_size: int
    st_mtime: float
```

`st_mode`, `st_size`, `st_mtime`.

### `fs.Stat`

```python
class Stat(Protocol):
    def stat(self, *, follow_symlinks=True) -> FileStatLike: ...
    def lstat(self) -> FileStatLike: ...
    def exists(self, *, follow_symlinks=True): ...
    def is_dir(self, *, follow_symlinks=True): ...
    def is_file(self, *, follow_symlinks=True): ...
    def is_symlink(self): ...
    def is_block_device(self): ...
    def is_char_device(self): ...
    def is_fifo(self): ...
    def is_socket(self): ...
    info
    def is_junction(self) -> bool: ...
    def is_mount(self) -> bool: ...
    def is_dir_binding(self) -> bool: ...
```

`stat()` (stub). A stat may report `size_known=False` (`FileStat`): the
backend knows the entry's type and not its size or time, as TFTP without the
`tsize` option or an HTTP reply without `Content-Length` do; `st_size` and
`st_mtime` are then placeholders, and a consumer that needs them asks again or
does without. Derives `lstat()`,
`exists()`, `is_dir()`,
`is_file()`, `is_symlink()`, `is_block_device()`,
`is_char_device()`, `is_fifo()`, `is_socket()`; any `OSError`/`ValueError`
from `stat()` reads as `False`. `info`, `is_junction()`, `is_mount()` and
`is_dir_binding()` are described under `Path`.

### `fs.Chmod`

```python
class Chmod(Chown, Utime, Protocol):
    def chmod(self, mode, *, follow_symlinks=True): ...
    def lchmod(self, mode): ...
```

`chmod()` (stub; every
implementation normalizes through `providers.parse_mode()`, so `"0755"` works)
and `lchmod()`. `supports(x, Chmod)` asks for `chmod()` alone. `Chmod`
inherits `Chown` and `Utime` for compatibility, because `chown()`, `_chown()`
and `_utime()` were documented with it: a class that lists `Chmod` has them,
and a new class lists the protocol it means.

### `fs.Owned`

```python
class Owned(Stat, Protocol):
    def owner(self, *, follow_symlinks=True) -> str: ...
    def group(self, *, follow_symlinks=True) -> str: ...
    def _user_name(self, uid) -> str: ...
    def _group_name(self, gid) -> str: ...
```

`owner()` and `group()` (the 3.13 signature) take `st_uid` and `st_gid` from
`stat()` and map them through `_user_name(uid)` and `_group_name(gid)` (stubs);
a stat without the field, or a class without the mapping, raises
`NotImplementedError`. `LocalPath` keeps pathlib's.

### `fs.Chown`

```python
class Chown(Protocol):
    def _chown(self, uid, gid, *, follow_symlinks=True) -> None: ...
    def chown(self, uid=None, gid=None, *, follow_symlinks=True) -> None: ...
```

`_chown()` (stub, receives a
canonical pair) / `chown()` —
`None` or `-1` leaves a field unchanged, `int` is an id, `str` a name; an
all-unchanged call returns without touching the backend.

### `fs.Utime`

```python
class Utime(Protocol):
    def _utime(self, *, atime=None, mtime=None) -> None: ...
```

`_utime(*, atime=None,
mtime=None)` (stub) is the primitive for setting an existing entry's times, in
seconds since the epoch (float): `None` leaves that time alone, a backend that
keeps whole seconds or no access time sets what it can, and one that cannot set
a time at all raises `NotImplementedError`. `touch()` of an existing file and
`PathSyncer(preserve_mtime=True)` call it.

### `fs.Create`

```python
class Create(Stat, Chmod, Utime, Protocol):
    def touch(self, mode=None, exist_ok=True): ...
    def _mkdir(self, mode): ...
    def mkdir(self, mode=0o777, parents=False, exist_ok=False): ...
```

`_mkdir(mode)` (stub) creates this one directory; `mkdir()` and `touch()` are
described under `Path`.

### `io.BinaryOpen`

```python
class BinaryOpen(Protocol):
    def open(
        self, mode="r", buffering=-1, encoding=None, errors=None,
        newline=None,
    ) -> _io.IOBase: ...
    def read_bytes(self) -> bytes: ...
    def read_text(self, encoding=None, errors=None, newline=None) -> str: ...
    def write_bytes(self, data): ...
    def write_text(self, data, encoding=None, errors=None, newline=None): ...
    def copy(self, target, *, progress=None, chunk_size=...): ...
    def _open(self, mode="r", buffering=-1) -> _io.IOBase: ...
```

`_open()` (stub).
`open()`
validates the mode like builtin `open()` (`ValueError`) and passes
`_open()` a canonical `r`/`w`/`x`/`a` plus optional `+` (never `b`/`t`);
text mode wraps in `TextIOWrapper` and closes the handle if wrapping fails.
An `_open()` that cannot honor a mode raises `NotImplementedError` per
call: one `_open(mode)` primitive serves reading and writing, so there is no
separate read-only protocol. A stream
that commits on close inherits `providers.DiscardOnError`, so a `with` block
over the file that raised commits nothing (see "For path implementers");
`write_bytes()`, `write_text()` and `copy()` whose data fails half way commit
nothing there either. Binary mode returns the stream `_open()` gave it as it
is. Derives
`read_bytes()`, `read_text()`,
`write_bytes(data)`, `write_text()`, `copy()` (`chunk_size` defaults to `shutil.COPY_BUFSIZE`; `progress(bytes_copied, total_size | None)`; an empty file reports once).
This `copy()` streams into any target that has `open()`; `Copy.copy()` is the
path copy, and a class that lists `Copy` before `BinaryOpen` has that one.

### `tree.Listing`

```python
class Listing(Stat, Protocol):
    def iterdir(self) -> Iterator[Self]: ...
    def _scandir(self) -> Iterator[Tuple[str, Optional[FileStat]]]: ...
    def glob(
        self, pattern, *, case_sensitive=None, include_hidden=True,
        recursive=None, dironly=None, recurse_symlinks=False, native=True,
        on_error=None, bound_loops=False,
    ): ...
    def rglob(
        self, pattern, *, case_sensitive=None, include_hidden=True,
        recursive=True, dironly=None, recurse_symlinks=False, native=True,
        on_error=None, bound_loops=False,
    ): ...
    def walk(self, top_down=True, on_error=None, follow_symlinks=False): ...
```

`iterdir()` (stub). `_scandir()`, `glob()`, `rglob()`, `walk()` and
`__iter__()` derive from it and from `stat()`; their contracts are under
`Path`.

### `tree.Remove`

```python
class Remove(Listing, Protocol):
    def unlink(self, missing_ok=False): ...
    def rmdir(self): ...
    def rm(
        self, recursive=False, missing_ok=False, ignore_error=False, *,
        follow_symlinks=False, follow_binds=False,
    ): ...
```

`unlink()` and `rmdir()` (stubs); `rm()` derives from them and from
`_scandir()` (see `Path`).

### `links.Readlink`

```python
class Readlink(Stat, Protocol):
    def readlink(self) -> Self: ...
```

`readlink()` (stub) returns a path of this class whose `str()` and
`as_posix()` are the link text, unresolved (`OSError(EINVAL)` where the path
is not a link). A read-only backend such as `ZipPath` and `TarPath` has this
and not `Symlink`.

### `links.Symlink`

```python
class Symlink(Remove, Protocol):
    def _symlink_to(self, target, target_is_directory=False) -> None: ...
    def _symlink_target(self, target) -> Self: ...
    def symlink_to(
        self, target, target_is_directory=False, *, force=False,
    ) -> None: ...
```

`_symlink_to()` (stub) creates a link, and `symlink_to()` is described under
`Path`.

### `links.Hardlink`

```python
class Hardlink(Stat, Protocol):
    def _hardlink_to(self, target) -> None: ...
    def hardlink_to(self, target) -> None: ...
```

`_hardlink_to(target)` (stub) makes this path a hard link to `target`, a
path of this class; `hardlink_to()` turns a `str` target into a path of the
same backend first. `LocalPath` has pathlib's `hardlink_to()` and, before
3.10, makes the link with `os.link`.

### `resolve.Resolve`

```python
class Resolve(Readlink, Protocol):
    def _cwd(self) -> Self: ...
    def absolute(self) -> Self: ...
    def resolve(self, strict=False) -> Self: ...
```

`_cwd()` (stub) is the directory a relative path is relative to; `MemPath`
and the tree paths answer with their root. `absolute()` is the path itself
when it is absolute, else `_cwd()` joined with it. `resolve(strict=False)`
makes it absolute, applies `..` and replaces every link by its `readlink()`
text, at most 40 of them (a rooted link text restarts at the root). It is
lexical where the class cannot read links. `strict=True` raises
`FileNotFoundError` for a missing component. A link loop raises what `pathlib`
raises on the running interpreter: `RuntimeError` before 3.13, and from 3.13
`OSError(ELOOP)` when `strict` (otherwise the looping link is kept).
`LocalPath` keeps pathlib's.

### `transfer.Identity`

```python
class Identity(Stat, Protocol):
    def _same_filesystem(self, other) -> bool: ...
    def _node_key(self) -> tuple[object, tuple[str, ...]] | None: ...
    def samefile(self, other_path): ...
```

No primitives: the hooks are described under `Path`.

### `transfer.Copy`

```python
class Copy(Identity, Create, Symlink, Readlink, BinaryOpen, Protocol):
    def copy(
        self, target, *, overwrite=False, follow_symlinks=True,
        preserve_metadata=True, recursive=False, ignore_error=None,
        progress=None, on_error=None,
    ): ...
    def copy_into(
        self, target_dir, *, overwrite=False, follow_symlinks=True,
        preserve_metadata=True, recursive=False, ignore_error=None,
        progress=None, on_error=None,
    ) -> Path: ...
    def _copy_from(
        self, source, follow_symlinks=True, preserve_metadata=False,
    ) -> None: ...
    def _coerce_target(self, target) -> Path: ...
    def _write_batch(self) -> ContextManager[object]: ...
```

No primitives: `copy()` is built from the protocols it lists. Contracts are
under `Path`.

### `transfer.Move`

```python
class Move(Copy, Remove, Protocol):
    def rename(self, target): ...
    def move(self, target, *, overwrite=False): ...
    def move_into(self, target_dir, *, overwrite=False) -> Path: ...
    def replace(self, target) -> Path: ...
    def _rename_compatible(self, target) -> bool: ...
```

`rename()` (stub). `move()` falls back to a copy and a removal where it is
missing, so `supports(x, Move)` says whether the backend renames natively, not
whether `move()` works.

### `checksum.NativeChecksum`

```python
class NativeChecksum(Protocol):
    def checksum(self, algorithm="md5") -> str: ...
    def supported_checksums(self) -> FrozenSet[str]: ...
```

Opt-in (not on `Path`):
`checksum()` MUST raise `NotImplementedError` whenever
it cannot return a genuine content digest under exactly that algorithm
(never a different algorithm, never a hash-shaped value such as a
multipart ETag); callers catch only `NotImplementedError`. `supported_checksums()` (default
empty) is advisory and never raises.

## Syncing trees (`pathapi.sync`)

```python
class PathSyncer:
    EVENT_LOG_FORMAT = "[%s] Source:%s Target:%s DryRun:%s"
    def __init__(
        self, checksum=None, /, remove_missing=False, follow_symlinks=True,
        symlink_mode="preserve", hook=None, ignore_error=False,
        quick_check=True, preserve_mtime=True, follow_mounts=True,
    ) -> None: ...
    def sync(self, source, target, /, dry_run=False, ignore_error=None): ...
    def log(self, msg, *args): ...
```

One-way tree sync between any two
`Path` implementations.

- `.sync()` — `None` uses
  the constructor policy; a bool or callable overrides it for this call.
- `checksum=None`: a native digest when both sides share an algorithm
  (`NativeChecksum`), else a streamed md5 on both sides; a callable
  `checksum(entry: PathAndStat)` is compared with `==`.
- `quick_check=True`: when either side is non-local (`is_local()`), equal
  `st_size` and `st_mtime` skip the checksum; `st_mtime == 0` never matches;
  a mismatch still checksums. Times match at the resolution of the coarser
  side: in whole seconds when either has no fractional part (SFTP, FTP and the
  object stores report seconds), else to a microsecond. So 100.0 and 100.9
  match, 100.0 and 101.0 do not, and neither do 100.25 and 100.5.
- `preserve_mtime=True`: after it copies a file, the sync gives the target the
  source's `st_mtime` through `_utime()` (not for a directory or a link),
  so the next run's quick check can match. A source that reports no time and a
  target that cannot set one (`NotImplementedError`), or refuses
  (`OSError`), keep the time the copy gave them, and the sync does not
  report it. `False` leaves every copy with the time it was written at.
- A `sync()` that is not a dry run runs inside the root target's
  `_write_batch()`: onto a zip archive the whole tree is one rewrite
  of the file, and a sync that raises leaves the archive as it was.
- `hook(source: PathAndStat, target: PathAndStat, event: SyncEvent,
  dry_run: bool)` is called for every event (structural ones included) with
  the call's `dry_run`.
- `ignore_error`: bool or `callable(error, source, target, event) -> bool`
  (`source` and `target` are `PathAndStat`), offered once per error, and a
  callable that raises ends the sync with its own exception, which is not
  offered back to it; a tolerated error is logged at WARNING on logger
  `pathapi.sync` and reported to `hook` as `SyncEvent.Error`.
  `.log(msg, *args)` (INFO on the same logger) is overridable by a subclass;
  `EVENT_LOG_FORMAT` (`"[%s] Source:%s Target:%s DryRun:%s"`) is the format
  it receives for each event.
- Safety, all through `ignore_error`: a missing root `source` →
  `FileNotFoundError`; overlapping `source`/`target` (one inside the other,
  or two names of one file: decided like `copy()`/`move()` decide "same
  file", so two files of this machine whatever their classes, two spellings
  of one `MemPath` or archive node, and else one type that
  `_same_filesystem()` places together) → `ValueError`; a child
  name that would leave `target` (`..`, or on a Windows target `\`, `:`, a
  trailing dot or space, a device name) → `ValueError`, decided on the name
  as listed, in the source's listing and the target's alike; a symlink
  inside `target` is replaced, never followed, and so is a Windows
  junction below its root (event `TypeMismatch`; `rmdir()` removes the
  junction and nothing behind it). A mount point inside `target` is part
  of the tree and is synced into (`follow_mounts=True`, the default).
  With `follow_mounts=False` it is an error for that entry instead:
  `OSError(EXDEV)` naming it, offered to `ignore_error` with event
  `TypeMismatch`, and nothing behind it is listed, written or removed, in a
  dry run too. A junction is replaced as above under either value; the root
  `target` is used as given.
  Listing entries with unknown
  stats are re-stat'd. A name the source lists is never "missing": a listed
  link that cannot be resolved (dangling, a loop, an unmounted volume) is an
  error for that entry (`FileNotFoundError` or the stat's `OSError`, event
  `SyncStart`) and the same-named target entry is left alone, whatever
  `remove_missing` is; only a name the listing no longer holds is removed.
- Cost: each root is stat'd once and each directory of each side is listed
  once; the listing's stat answers for every entry that is not a link, so a
  sync that changes nothing makes no other `stat()` call on a backend whose
  `_scandir()` returns stats. A link, an entry whose listing stat is `None`
  and a target name the listing did not report are stat'd, one call each. A
  backend with no `_scandir()` of its own pays the default's `stat()` per
  entry of every directory it lists.
- `remove_missing=True` deletes target entries absent from the source. With
  `False`, a non-empty target directory whose source became a file or link
  is kept (`IsADirectoryError`, event `TypeMismatch`).
- `follow_symlinks=False` + `symlink_mode="preserve"` recreates source links
  with the raw `readlink()` text (target must implement `symlink_to()`, else
  `NotImplementedError` through `ignore_error`, in a dry run too);
  `"reject"` raises `NotImplementedError`.
- Changed files are written to a hidden temporary sibling
  (`.NAME.<12 hex>.pathapi-tmp`, NAME cut to keep the whole within 255
  bytes) and renamed over the target where the target supports `rename()`;
  where `rename()` refuses an existing target the old file is removed first,
  and if the second rename fails the new version is kept under its
  temporary name and the error (an `OSError`) names it. A name of exactly
  that form is the library's own: never a source, never removed as
  "missing"; a stale one (not being written by this process, modification
  time known and over a day old) is removed from a target directory when a
  changed file or link in it is next written, and by `remove_missing=True`
  (event `RemovedMissing`). FIFOs, sockets and devices are skipped. A dry run makes the same
  decisions without changes.

### `SyncEvent`

```python
class SyncEvent(Enum):
    Copy = ...
    RemovedMissing = ...
    Synced = ...
    CreatedDirectory = ...
    SyncStart = ...
    TypeMismatch = ...
    CheckTargetChild = ...
    CheckTargetChildren = ...
    SyncChild = ...
    SyncChildren = ...
    Symlink = ...
    Compare = ...
    Skipped = ...
    Error = ...
```

Members: `Copy`, `RemovedMissing`, `Synced`,
`CreatedDirectory`, `SyncStart`, `TypeMismatch`, `CheckTargetChild`,
`CheckTargetChildren`, `SyncChild`, `SyncChildren`, `Symlink`, `Compare`
(a comparison failed; only passed to `ignore_error`), `Skipped` (not a
file, directory or link), `Error` (an error was tolerated).

### `PathAndStat`

```python
class PathAndStat:
    def __init__(self, path, *, follow_symlink=True) -> None: ...
    @classmethod
    def from_stat(cls, path, stat) -> PathAndStat: ...
    def exists(self): ...
    def refresh(self, follow_symlink=True): ...
```

`path` plus cached
`stat` (`FileStat | None`); `from_stat(path, stat)`, `exists()`,
`refresh(follow_symlink=True)`; `is_*` attributes delegate to the stat
(always-`False` callables when missing).

## Globbing (`pathapi.glob`)

```python
def glob(
    path, *, dironly=False, root_dir=None, recursive=False,
    include_hidden=False, case_sensitive=None, native=True, on_error=None,
    bound_loops=False, recurse_symlinks=False,
) -> Iterator[_Globable]: ...
def compile_pattern(pat, case_sensitive): ...
WILCARD_PATTERN = WILDCARD_PATTERN
def parse_pattern(pattern, *, native=True) -> Tuple[List[str], bool]: ...
def select(
    base, parts, *, dironly=False, recursive=True, include_hidden=True,
    case_sensitive=None, native=True, on_error=None, bound_loops=False,
    recurse_symlinks=False,
) -> Iterator[_Globable]: ...
def full_match(segments, pattern, case_sensitive) -> bool: ...

RECURSIVE = '**'
```

`glob.glob()`: the pattern
is itself a path (`LocalPath("/x/**/*.py")`); like stdlib `glob`, hidden
names need `include_hidden=True`. It is split at the first wildcard, never
in the path's anchor (a drive, `\\?\C:\`); a bad pattern raises when it is
called, the selection is lazy. `glob.parse_pattern()` (`ValueError`/`NonRelativePatternError`),
`glob.select()` (the engine behind
`Path.glob()`), `glob.full_match()`,
`glob.RECURSIVE = "**"`, `glob.compile_pattern()` (one shell-style component
as a compiled regular expression, cached), `glob.ANY_PATTERN` (a lone `*`) and
`glob.WILDCARD_PATTERN` (a wildcard character), both compiled expressions, and
`glob.WILCARD_PATTERN`, the old misspelling of `WILDCARD_PATTERN`.

## Checksums (`pathapi.checksum`)

```python
def md5(path, chunk_size=65536) -> str: ...
def sha256(path, chunk_size=65536) -> str: ...
def stream(path, algorithm="md5", chunk_size=65536) -> str: ...
def native(path, algorithm="md5") -> str | None: ...
```

`md5()`, `sha256()`, `stream()`
(streamed through `open("rb")`, `usedforsecurity=False`),
`native()` (`None` when the protocol is
missing or raises `NotImplementedError`).

## Providers (`pathapi.providers`)

```python
def is_safe_child_name(name, *, windows=False) -> bool: ...
def is_windows_flavoured(path) -> bool: ...
def name_stem(name) -> str: ...
def name_suffix(name) -> str: ...
def name_suffixes(name) -> list[str]: ...
class TreePath(Path):
    @classmethod
    def _join(cls, segments) -> tuple[str, TreePath | None]: ...
    def _rebuild(self, text) -> Self: ...
    segments; parts; parent
    def with_segments(self, *segments) -> Self: ...
    def relative_to(self, other, *, walk_up=False) -> Self: ...
def parse_mode(mode) -> int: ...
def parse_owner(uid, gid) -> Tuple[Optional[int], Optional[int]]: ...
def as_error_handler(
    ignore_error, *, default=False,
) -> Callable[..., bool]: ...
def as_error_predicate(on_error) -> OfferedOnce: ...
class OfferedOnce:
    def __call__(self, error, path) -> bool: ...
def notimplemented(method): ...
def derived_from(*primitives): ...
def primitive(method): ...

class DiscardOnError:
    def discard(self) -> None: ...

class CommitOnClose(DiscardOnError, BytesIO):
    def __init__(self, commit, initial=None): ...
    def discard(self) -> None: ...

class LRU:
    def __init__(self, func, maxsize=128, on_evict=None): ...
    def discard(self, *args) -> bool: ...
    def invalidate(self, *args) -> V: ...
    maxsize
UNCHANGED = None
```

- **`is_safe_child_name()`** — `False` for a
  non-`str`, `""`, `.`, `..`, or a name containing `/` or NUL; with
  `windows=True` also `\`, `:`, a trailing dot or space, and the reserved
  device names `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` (any
  case, with or without an extension: `nul.txt`).
  **`is_windows_flavoured()`**
  — `True` for a `PureWindowsPath` or a path whose `filepath` is one.
- **`TreePath`** — the base of a path whose names are POSIX segments below the
  root of a tree a backend serves: a remote directory, the members of an
  archive. `FTPPath`, `ZipPath`, `TarPath` and `ArchivePath` are built on it.
  It holds the names only: joined and normalized like `PurePosixPath` (empty
  and `.` segments collapse, an absolute argument restarts the join), and a
  relative and a rooted spelling name the same node because the tree has no
  working directory (`_cwd()` answers the root). A backend where the two
  spellings are different nodes, because it has a working directory, does not
  use `TreePath`, or overrides `_cwd()`. A subclass supplies the constructor, the backend it
  carries and `_rebuild(text)` (a path of its class and backend for a joined
  text); `_join(segments)` returns the joined text and the first segment that
  is a path of the same family, whose backend a join keeps.
- **`name_stem()`, `name_suffix()`, `name_suffixes()`** — one path component
  split as `pathlib` of the running interpreter splits it (`"a.tar.gz"` →
  `"a.tar"`, `".gz"`, `[".tar", ".gz"]`; `".bashrc"` has no suffix). From
  Python 3.14 a trailing dot is a suffix (`"a."` → `"."`), as it is there.
  A class that computes `stem` and `suffix` itself calls these to agree with
  `Pathname`.
- **`LRU`** — thread-safe memoizing cache,
  called like `func`. `on_evict(key_tuple, value)` runs for every dropped
  value (overflow, `maxsize` shrink, `invalidate`/`discard`, a losing
  concurrent miss), outside the lock, exceptions suppressed. `discard(*args)
  -> bool`, `invalidate(*args)` (discard + recompute), settable `maxsize`.
  `maxsize=None` is unbounded; `maxsize=0` (or less) stores nothing, so every
  call runs `func` and its result never reaches `on_evict`.
- **`parse_mode()`** — `int` passes through; `str` is octal (optional
  `0o`), any non-octal digit → `ValueError`. A `bool` → `TypeError`; a number
  below 0 or above `0o177777` (the 16 bits of a `st_mode`, type bits
  included) → `ValueError`.
- **`parse_owner()`** — `-1` → `None`;
  `str` names pass through; a `bool` → `TypeError`, an id outside
  0..2**32-1 → `ValueError`. **`UNCHANGED = None`**.
- **`as_error_handler()`** — a
  callable passes through untouched (arity is the call site's: `rm` and
  `copy(on_error=)` `(error, path)`, `copy(ignore_error=)` `(error)`,
  `PathSyncer` `(error, source, target, event)`); a bool (or `None` →
  `default`) becomes a constant-returning callable.
  **`as_error_predicate()`** — `on_error` as an `OfferedOnce`: a bool becomes a
  constant answer, a callable is called as `(error, path)`, and an
  `OfferedOnce` is returned as it is. An `OfferedOnce` answers `False`, without
  calling the predicate, for an error it already answered `False` for and
  for an exception the predicate itself raised, so a recursive operation that
  passes it down never offers one failure twice.
- **`notimplemented()`** — decorator that marks a method as the stub of a
  primitive, bare or with a reason: `@notimplemented` or `@notimplemented("why")`.
  Calling it raises `NotImplementedError` with the reason, or
  `"Method not implemented: <name>"`. `protocols.supports()` reads it as absent.
- **`primitive()`** — decorator that marks a function as the default of a
  primitive that raises an error of its own (a `data:` URI's `_listdir()`
  raises `NotADirectoryError`): `supports()` reads it as absent, as it reads
  a `notimplemented` stub.
- **`derived_from(*primitives)`** — decorator for a method that only wraps
  other primitives of its class, such as an `iterdir()` that lists through
  `_listdir()`: `supports()` counts the method as implemented only when one
  of the named primitives is, so a base class can define the wrapper without
  claiming a protocol its subclasses do not have.
- **`CommitOnClose`** and **`DiscardOnError`** — the write buffer that commits
  when it is closed, and the mixin that makes a failed `with` block commit
  nothing; see "For path implementers".

## Testing helpers (`pathapi.testing`, needs `pytest`)

### `FIXTURE_TREE`

```python
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
```

Directories are the entries whose value is `None`.

### `populate_fixture_tree`

```python
def populate_fixture_tree(root): ...
```

Builds `FIXTURE_TREE` under an
existing empty directory through the path's own `mkdir()`/`write_text()`
(any `Path`, or a stdlib `pathlib.Path`).

### `PurePathContract`

```python
class PurePathContract: ...
```

Name/suffix/stem, parents, join, `match()`; needs a
`root` fixture only.

### `ReadPathContract`

```python
class ReadPathContract(PurePathContract):
    supports_listing = None
    supports_empty_directories = True
    distinguishes_file_types = True
    supports_pickle = True
```

Exists/types, reads and read
modes, partial `read(n)`, `iterdir()`, `stat()` (the size of a longer file, a
directory told from a file, a numeric `st_mtime`), `_scandir()` agreeing with
`stat()` for every child, nothing existing below a file, `glob()`/`rglob()`,
`walk()`, with pathlib's exception types, and a pickle round trip that keeps
`==` and `hash()`. Capability attributes: `supports_listing`,
`supports_empty_directories`, `distinguishes_file_types`, `supports_pickle`.

### `PathContract`

```python
class PathContract(ReadPathContract):
    supports_rename = None
    supports_append = True
    supports_exclusive_create = True
    enforces_directory_hierarchy = True
    supports_mkdir = None
    supports_delete = None
    supports_move = True
    supports_unusual_names = True
    supports_utime = None
    supports_question_mark_names = False
```

`mkdir()`, writes and write/append/
exclusive modes, a payload of about 290 KiB holding every byte value and
runs of CR/LF written whole and in pieces and read back whole and in parts,
names holding a space, `#`, `%`, `+`, `&`, `=` and mixed case written,
listed, renamed and removed as written, `unlink()`, `rmdir()`, `rm()` (also
with `follow_symlinks=`/`follow_binds=`: an implementation that overrides
`rm()` must accept both), `copy()` (recursive), `move()`, `rename()` (also
onto a non-empty directory, which must raise), `touch()`, copying a file
onto a separately built spelling of itself (`OSError`, content kept) and
`_same_filesystem()` within one root. Capability attributes:
`supports_rename`, `supports_append`, `supports_exclusive_create`,
`enforces_directory_hierarchy` (also covers rename onto a directory),
`supports_mkdir` (every test that creates a directory), `supports_delete`
(`unlink()`, `rmdir()`, `rm()`, and the overwriting half of `copy()`),
`supports_move` (`move()`, by `rename()` or copy and delete),
`supports_unusual_names`, `supports_utime` (`_utime()` sets an existing
file's modification time and `touch()` moves it; set it `False` for a backend
that cannot set a time and leaves `_utime()` raising `NotImplementedError`),
and `supports_question_mark_names` (names holding `?`; **False by default**,
because a Windows file system cannot store one: set it `True` for a URI
scheme or a remote store).

- Five switches default to `None`, which means "ask `protocols.supports()` on
  `root`": `supports_listing` (`Listing`), `supports_mkdir` (`Create`),
  `supports_delete` (`Remove`), `supports_utime` (`Utime`) and `supports_rename`
  (`Move`). A class without the protocol skips those tests with no switch set,
  and the skip reason names it (`MyPath does not support Remove`). Set the
  attribute `True` or `False` on the test class to override the answer, for a
  class that implements the protocol but cannot honour the test.

- Both I/O contracts need `root` to be a **fresh, function-scoped** directory
  populated with `populate_fixture_tree()` (which uses `mkdir()` and
  `write_text()`: a backend without them seeds `root` another way); two
  contract classes must not share one. Capability attributes default to
  `True` except `supports_question_mark_names`; setting one `False` makes the
  tests it covers skip. `DIRECTORY_ERRORS = (IsADirectoryError,
  PermissionError)` and `NOT_EMPTY_ERRNOS = (ENOTEMPTY, EEXIST)` are the
  accepted platform variants.

```python
import pytest
from pathapi import LocalPath as MyPath  # your Path subclass
from pathapi.testing import PathContract, populate_fixture_tree

class TestMyPath(PathContract):
    @pytest.fixture
    def root(self, tmp_path):
        return populate_fixture_tree(MyPath(tmp_path))
```

### Testing support: the HTTP transport suite (`pathapi.http.testing`, needs `pytest`)

This is testing support, not part of the stable API: it is importable and
documented here, but its contents, including test names, fixtures and helper
signatures, may change in any release. `pathapi.http` never imports it and
the root does not export it.

```python
# pathapi.http.testing
def suite_directory() -> pathlib.Path: ...
def run(arguments=None) -> int: ...
```

A distribution that provides a transport holds it to the promises of the
built-in one by running the tests `pathapi` runs over its own, with the
distribution and `pytest` installed:

```
python -m pathapi.http.testing --http-transports requests -q
```

Everything after `python -m pathapi.http.testing` goes to pytest.
`--http-transports` takes names that `transport_names()` lists, separated by
commas (default `builtin`): each test that depends on the transport runs once
per name, with `PATHAPI_HTTP_TRANSPORT` set to it, so the clients the
test builds use `get_transport(name)()`. The tests are `HTTPClient` over
loopback servers (redirects and the rule for `Authorization`, replies cut short
or too large, errors, headers) and the transport contract against servers
scripted at the byte level (what the server received is what is asserted). The
modules also hold tests that do not depend on the transport; they pass whatever
the name. Every connection stays on the loopback interface, a test that tries
another host fails, and the proxy and certificate settings of the environment
are removed for the run. The TLS tests need `cryptography` and skip without it.
Importing `pathapi.http.testing` loads `pathapi.http` but not `pytest`.

- `run(arguments=None)` runs the suite with pytest and returns the exit status;
  it raises `ImportError` without pytest.
- `suite_directory()` is the directory that holds the test modules,
  `MODULES` (their file names) and `HELPERS` (the files they import):
  `pathapi/http/testing/suite` in an installed package, `tests/transport`
  in a source checkout; `FileNotFoundError` when neither is there.

## Exceptions

A failure is the exception `pathlib` raises for the same condition
(`FileNotFoundError`, `FileExistsError`, `IsADirectoryError`,
`NotADirectoryError`, `PermissionError`, or an `OSError` with an `errno`),
`NotImplementedError` for an operation a class does not support, and
`ValueError`/`TypeError` for a bad argument. Those the package defines are
listed here; the conditions that raise them are in the entries.

### `pathapi.exceptions` (also exported from `pathapi`)

```python
class PathError(Exception): ...
class NonRelativePatternError(PathError, NotImplementedError, ValueError): ...
```

`PathError` is the base of every exception the core defines for a failure at
run time that no builtin describes; an I/O failure is never a `PathError`.
`glob()` raises `NonRelativePatternError` for an absolute pattern, and it is
caught as `PathError`, `NotImplementedError` or `ValueError`.

## Environment variables

The package reads one.

### `PATHAPI_HTTP_TRANSPORT` (`pathapi.http`)

`builtin` (the default when unset), or the name of a
transport an installed distribution declares in the group
`pathapi.http_transports` (`requests`, from `pathapi-requests`): the
transport an `HTTPClient` builds when it was given none. `requests` without
that distribution raises `ImportError` naming it; the value is case-sensitive,
and an empty or unrecognised one raises `ValueError` naming the choices
(`transport_names()`). It is read when a client first needs its transport,
once per client, so a change reaches clients that need theirs afterwards.

## Gotchas

- A `str` argument to `is_relative_to()` is parsed standalone via
  `self.with_segments(other)`, which keeps per-instance state (a `MemPath`
  backend). Normalize strings the same way in subclasses: `type(self)(x)`
  drops that state.

- **A binding is not a symlink.** `is_junction()` (pathlib 3.12 parity:
  a Windows junction) and `is_mount()` (a mount point, bind mounts
  included) report a directory that is another tree's second NAME.
  Neither is a symlink: `is_symlink()` is False, `readlink()` says
  nothing, and a non-following stat calls it an ordinary directory — which
  is why a symlink check cannot protect a walk from one. `is_dir_binding()`
  is the pair, and is what `rm(recursive=True)` consults: it removes the
  binding itself rather than the contents behind it (`rmdir()` on a live
  mount fails instead of emptying the mounted filesystem).
  Default False everywhere; `LocalPath` answers for real.
