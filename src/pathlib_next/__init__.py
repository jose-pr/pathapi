from __future__ import annotations

import importlib as _importlib
from importlib import metadata as _metadata

__all__ = [
    "BinaryOpen",
    "Chmod",
    "FTPPath",
    "FileStat",
    "FsPathLike",
    "LocalPath",
    "Path",
    "PathLike",
    "Pathname",
    "PosixPathname",
    "PurePathLike",
    "Stat",
    "WindowsPathname",
    "__version__",
    "glob",
    "sync",
]

try:
    __version__ = _metadata.version("pathlib-next")
except _metadata.PackageNotFoundError:  # a source tree that is not installed
    __version__ = "0+unknown"

_uri_unavailable: "ImportError | None" = None
try:
    from .uri import Uri as Uri
    from .uri import UriPath as UriPath
except ImportError as _error:
    _uri_unavailable = _error
else:
    __all__ += ["Uri", "UriPath"]


#: Path classes of the standard-library providers, imported on first use so
#: that `import pathlib_next` loads neither `ftplib` nor `ssl`.
_LAZY = {"FTPPath": ".ftp"}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is not None:
        value = getattr(_importlib.import_module(module, __name__), name)
        globals()[name] = value
        return value
    # `hasattr(pathlib_next, "UriPath")` stays False without the extra: this
    # is an AttributeError that says why.
    if name in ("Uri", "UriPath") and _uri_unavailable is not None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}: {_uri_unavailable}"
        ) from _uri_unavailable
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .fspath import LocalPath, PosixPathname, WindowsPathname
from .path import FsPathLike, Path, PathLike, Pathname, PurePathLike
from .protocols import BinaryOpen, Chmod, Stat
from .utils import glob as glob
from .utils import sync as sync
from .utils.stat import FileStat
