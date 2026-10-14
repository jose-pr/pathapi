# pathapi

[![Version](https://img.shields.io/pypi/v/pathapi.svg)](https://pypi.org/project/pathapi/)
[![Python versions](https://img.shields.io/pypi/pyversions/pathapi.svg)](https://pypi.org/project/pathapi/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/pathapi/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/pathapi/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/pathapi/test.yml)](https://github.com/jose-pr/pathapi/actions/workflows/test.yml)

A **robust, extensible pathlib-like base** for any resource addressable as a
path. Same method names, signatures, semantics and exception types as
`pathlib.Path` wherever a `pathlib.Path` equivalent exists: write code once
against `Path` and it runs against your local disk, an in-memory tree, an FTP
server or the members of a zip or tar file, and against any path class built on
it. It has no dependencies. Deliberate differences from `pathlib` are listed in
[Divergences from pathlib](https://jose-pr.github.io/pathapi/divergences/).

## Features

| Class | Read | Write | List | Stat | mkdir | Delete | rename |
| --- | --- | --- | --- | --- | --- | --- | --- |
| local path (`LocalPath`) | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| in-memory (`MemPath`) | Yes | Yes | Yes | Yes | Yes | Yes | No (`move()` copies) |
| FTP and FTPS (`FTPPath`) | Yes | Yes | Yes (MLSD, NLST fallback) | Yes | Yes | Yes | Yes |
| zip members (`ZipPath`) | Yes | Local archive only | Yes | Yes | Local archive only | Local archive only | Local archive only |
| tar members (`TarPath`, incl. gz/bz2/xz) | Yes | No | Yes | Yes | No | No | No |
| `ArchivePath` | As `ZipPath`/`TarPath` for the format it detects | | | | | | |

Every class gets `glob()`, `walk()`, `copy()`/`move()`, `rm()` and `PathSyncer`
from the base classes; `LocalPath` and the other classes replace some of them
where the backend has a faster way.

- **One path interface** over local files, in-memory trees, FTP servers and
  archive members, and over any class built on `Path`.
- **`MemPath`**: a virtual filesystem for tests, mocks and transient data.
- **`FTPPath`, `ZipPath`, `TarPath`, `ArchivePath`**: an FTP server and the
  members of a zip or tar file as paths, on the standard library alone.
- **`pathapi.http`**: `HTTPClient` and a transport contract for HTTP
  requests on the standard library (redirects that never carry
  `Authorization` to another origin, TLS verified by default, a `proxies=`
  provider); the `requests` library plugs in as a transport through the
  `pathapi-requests` distribution.
- **`PathSyncer`**: one-way, content-compared tree sync between any two
  implementations, with dry runs and event hooks.
- **Extensible**: subclass `Path` for any resource, and verify it with the
  bundled pytest contracts.

Paths for other places are separate distributions built on this one:

| Distribution | Provides |
| --- | --- |
| [`uripath`](https://github.com/jose-pr/uripath) | `URIPath`: one class for any URI, with the schemes `file:`, `data:`, `ftp:`, `zip:`, `tar:`, `http:` and `dav:`, and the `uripath` command |
| [`sftppath`](https://github.com/jose-pr/sftppath) | `SFTPPath` (paramiko or asyncssh) |
| [`objpath`](https://github.com/jose-pr/objpath) | `S3Path`, `GSPath` and `AZPath` (Amazon S3, Google Cloud Storage, Azure Blob Storage) |
| [`repopath`](https://github.com/jose-pr/repopath) | `RepoPath`, `GitHubPath` and `GitLabPath` (the REST APIs of the hosting services) |
| [`pathapi-requests`](https://github.com/jose-pr/pathapi-requests) | `RequestsTransport`, the HTTP transport that sends through a `requests.Session` |

## Installation

```bash
pip install pathapi
```

There are no extras and no dependencies: everything above runs on the standard
library. `pytest` is needed only to import `pathapi.testing` and
`pathapi.http.testing`.

## Quick start

**Local filesystem**, as `pathlib.Path`:

```python
from pathapi import Path

p = Path("data") / "report.txt"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("hello")
print(p.read_text())
```

**In memory**, no disk I/O:

```python
from pathapi.mempath import MemPath

p = MemPath("/config/settings.json")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text('{"debug": true}')
print([child.name for child in p.parent.iterdir()])
```

**Archive members**: the segments are the member path and the archive is any
path:

```python
import zipfile
from pathapi import ZipPath

with zipfile.ZipFile("backup.zip", "w") as archive:
    archive.writestr("etc/config.ini", "[main]\n")

member = ZipPath("etc/config.ini", archive="backup.zip")
print(member.read_text())
print([child.name for child in member.parent.iterdir()])
```

**FTP**: nothing connects until the first operation:

<!-- example: skip: reads from a remote host -->
```python
from pathapi import FTPPath

for child in FTPPath("/pub", host="ftp.example.com", user="anonymous").iterdir():
    print(child.name, child.stat().st_size)
```

**Copy and sync across classes**:

```python
from pathapi import Path
from pathapi.mempath import MemPath
from pathapi.sync import PathSyncer

source = MemPath("/site")
(source / "css").mkdir(parents=True)
(source / "index.html").write_text("<h1>hi</h1>")

PathSyncer(remove_missing=True).sync(source, Path("site-copy"))
print(sorted(p.name for p in Path("site-copy").iterdir()))
```

## API overview

| Module | Purpose |
| --- | --- |
| `pathapi.path` | `Pathname`/`Path` base classes and the derived operations |
| `pathapi.fspath` | `LocalPath`, `PosixPathname`, `WindowsPathname` |
| `pathapi.mempath` | `MemPath` in-memory filesystem |
| `pathapi.ftp` | `FTPPath`, `FTPBackend` (FTP and FTPS on `ftplib`) |
| `pathapi.archive` | `ZipPath`, `TarPath`, `ArchivePath` (members of a zip or tar file), `make_archive`, `unpack_archive` |
| `pathapi.http` | `HTTPClient`, `HTTPTransport`, `HTTPResponse` (HTTP requests on `http.client`) |
| `pathapi.protocols` | `Stat`, `Chmod`, `BinaryOpen`, `NativeChecksum` |
| `pathapi.sync` | `PathSyncer`, `SyncEvent`, `PathAndStat` |
| `pathapi.glob` | the glob engine behind `Path.glob()` |
| `pathapi.checksum` | `md5`, `sha256`, `stream`, `native` |
| `pathapi.providers` | the helpers a class built on `Path` calls |
| `pathapi.exceptions` | `PathError`, `NonRelativePatternError` |
| `pathapi.testing` | pytest contracts for custom implementations |
| `pathapi.http.testing` | testing support (not stable API): the pytest suite that holds an HTTP transport to the promises of the built-in one |

The full reference is on the [documentation site](https://jose-pr.github.io/pathapi/);
an agent-oriented API summary ships inside the package as
`pathapi/AGENTS.md`.

## Extending

Subclass `Path` for a resource of your own (`MemPath` is the reference), as
worked through in
[Extending](https://jose-pr.github.io/pathapi/guides/extending/).
`pathapi.testing` provides the pytest contracts (`PurePathContract`,
`ReadPathContract`, `PathContract`) and `populate_fixture_tree()` that every
class of the package and of the distributions above is verified with.

## Supported Python versions

Python 3.9 through 3.14. CI runs the full suite on Ubuntu for every version
from 3.9 to 3.14, on Windows and macOS for 3.9 and 3.14, and with nothing but
`pytest` and `pyftpdlib` installed on 3.9 and 3.14.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q -rs
```

Tests that need an optional package are skipped when it is missing, so check
the skip list (`-rs`). Contributor notes (virtual environments, formatting, CI,
releases) are in the repository's
[`AGENTS.md`](https://github.com/jose-pr/pathapi/blob/main/AGENTS.md);
benchmarks are described in
[Benchmarks](https://jose-pr.github.io/pathapi/benchmarks/). To preview
the documentation: `pip install -e ".[docs]"` then `mkdocs serve`.

Changes are recorded in the
[changelog](https://github.com/jose-pr/pathapi/blob/main/CHANGELOG.md).

### Releasing

1. Move the `[Unreleased]` entries of `CHANGELOG.md` under a new
   `## [x.y.z] - <date>` heading and add its link definition at the bottom.
2. Bump `version` in `pyproject.toml` in the same commit.
3. Before 1.0 the minor version changes only when the documented API breaks;
   new methods, new optional arguments and fixes are patch releases.
4. Run the full suite on the oldest and the newest supported Python,
   `mkdocs build --strict` and `python -m build`.
5. Push `main`, then the `v*` tag: the release workflow tests, builds and
   publishes to PyPI and creates the GitHub release.

## License

MIT; see [LICENSE](https://github.com/jose-pr/pathapi/blob/main/LICENSE).
