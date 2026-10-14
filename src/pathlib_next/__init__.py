from __future__ import annotations

import importlib as _importlib
from importlib import metadata as _metadata

__all__ = [
    "ArchivePath",
    "BinaryOpen",
    "Chmod",
    "FTPPath",
    "FileStat",
    "FsPathLike",
    "LocalPath",
    "MemPath",
    "NonRelativePatternError",
    "Path",
    "PathError",
    "PathLike",
    "Pathname",
    "PosixPathname",
    "PurePathLike",
    "Stat",
    "TarPath",
    "WindowsPathname",
    "ZipPath",
    "__version__",
    "glob",
    "sync",
]

try:
    __version__ = _metadata.version("pathlib-next")
except _metadata.PackageNotFoundError:  # a source tree that is not installed
    __version__ = "0+unknown"

from .fspath import LocalPath, PosixPathname, WindowsPathname
from .mempath import MemPath
from .path import FsPathLike, Path, PathLike, Pathname, PurePathLike
from .protocols import BinaryOpen, Chmod, Stat
from . import glob as glob
from . import sync as sync
from ._filestat import FileStat
from .exceptions import NonRelativePatternError, PathError

#: Path classes of the standard-library providers, imported on first use so
#: that `import pathlib_next` loads neither `ftplib` nor `ssl`, and no archive
#: code.
_LAZY = {
    "ArchivePath": ".archive",
    "FTPPath": ".ftp",
    "TarPath": ".archive",
    "ZipPath": ".archive",
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is not None:
        value = getattr(_importlib.import_module(module, __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
