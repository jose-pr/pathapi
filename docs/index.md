# pathapi

A **robust, extensible pathlib-like base** for any resource addressable as a
path. Same method names, signatures, semantics and exception types as
`pathlib.Path` wherever a `pathlib.Path` equivalent exists: write code against
`Path` once and it works against your local disk, an in-memory tree, an FTP
server or the members of a zip or tar file, and against any path class built on
it. It has no dependencies.

Deliberate behavioral differences from `pathlib` are listed, with their
reasons, in [Divergences from pathlib](divergences.md).

`pathapi` was published as `pathlib-next` up to 0.10.0; `pip install pathlib-next`
now installs a shim that depends on this package. The [changelog](changelog.md)
has the migration from `pathlib_next`.

## Installation

```bash
pip install pathapi
```

There are no extras: everything runs on the standard library. `pytest` is
needed only to import `pathapi.testing` and `pathapi.http.testing`.

Paths for other places are separate distributions built on this one:

| Distribution | Provides |
| --- | --- |
| [`uripath`](https://github.com/jose-pr/uripath) | `URIPath`: one class for any URI (`file:`, `data:`, `ftp:`, `zip:`, `tar:`, `http:`, `dav:`), and the `uripath` command |
| [`sftppath`](https://github.com/jose-pr/sftppath) | `SFTPPath` (paramiko or asyncssh) |
| [`objpath`](https://github.com/jose-pr/objpath) | `S3Path`, `GSPath` and `AZPath` |
| [`repopath`](https://github.com/jose-pr/repopath) | `RepoPath`, `GitHubPath` and `GitLabPath` |
| [`pathapi-requests`](https://github.com/jose-pr/pathapi-requests) | `RequestsTransport` for `pathapi.http` |

## Tour

**Local filesystem**, as `pathlib.Path`:

```python
from pathapi import Path

p = Path("data") / "report.txt"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("hello")
print(p.read_text())
```

**In memory**, a virtual filesystem for tests and mocks:

```python
from pathapi.mempath import MemPath

p = MemPath("/config/settings.json")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text('{"debug": true}')
print(p.stat().st_size, [child.name for child in p.parent.iterdir()])
```

**Archives**: a member inside a zip or tar, the archive itself any path:

```python
import zipfile
from pathapi import ZipPath

with zipfile.ZipFile("backup.zip", "w") as archive:
    archive.writestr("etc/config.ini", "[main]\n")

member = ZipPath("etc/config.ini", archive="backup.zip")
print(member.read_text())
```

**FTP** shares the same contract (this needs a reachable server):

<!-- example: skip: reads from a remote host -->
```python
from pathapi import FTPPath

for child in FTPPath("/pub", host="ftp.example.com", user="anonymous").iterdir():
    print(child.name, child.stat().st_size)
```

`exists()`, `is_dir()`, `iterdir()`, `glob()`, `walk()`,
`read_text()`/`write_text()`, `copy()`/`move()` and `rm()` behave the same way
on every class; what a backend cannot do (for example `mkdir()` on a tar file)
raises `NotImplementedError`.

## Where to go next

- **[Extending](guides/extending.md)**: add a path type by subclassing `Path`,
  and verify it with the bundled contracts.
- **[Divergences from pathlib](divergences.md)**: every deliberate behavioral
  difference from `pathlib.Path`.
- **[API Reference](api/path.md)**: generated from the docstrings.
- **[Changelog](changelog.md)**.
