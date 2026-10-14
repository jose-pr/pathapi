"""`pathlib-next` is now `pathapi`; this package re-exports it.

`import pathlib_next` warns and then behaves as `import pathapi`: every name,
and every submodule (`pathlib_next.protocols` is `pathapi.protocols`, the same
module object), so classes and `isinstance` checks agree across both names.
New code imports `pathapi`.
"""

from __future__ import annotations

import importlib as _importlib
import importlib.abc as _abc
import importlib.machinery as _machinery
import importlib.util as _util
import os as _os
import sys as _sys
import typing as _ty
import warnings as _warnings

# The old variable is honoured until the caller moves to the new one.
if "PATHLIB_NEXT_HTTP_TRANSPORT" in _os.environ:
    _os.environ.setdefault(
        "PATHAPI_HTTP_TRANSPORT", _os.environ["PATHLIB_NEXT_HTTP_TRANSPORT"]
    )

_warnings.warn(
    "pathlib-next has been renamed pathapi: install pathapi and import pathapi "
    "instead of pathlib_next",
    DeprecationWarning,
    stacklevel=2,
)

import pathapi as _pathapi  # noqa: E402

if _ty.TYPE_CHECKING:
    from pathapi import *  # noqa: F401,F403

__all__ = list(_pathapi.__all__)
__path__: "list[str]" = []  # a package, so that submodules go to the finder below
__version__ = "0.10.1"


def __getattr__(name: str) -> _ty.Any:
    return getattr(_pathapi, name)


def __dir__() -> "list[str]":
    return sorted(set(globals()) | set(dir(_pathapi)))


_OLD = __name__
_NEW = _pathapi.__name__


class _Alias(_abc.Loader):
    """Hands the import system the `pathapi` module itself, not a copy."""

    def create_module(self, spec):
        return _importlib.import_module(_NEW + spec.name[len(_OLD) :])

    def exec_module(self, module) -> None:
        pass


class _AliasFinder(_abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        target_name = _NEW + fullname[len(_OLD) :]
        try:
            found = _util.find_spec(target_name)
        except (ImportError, ValueError):
            return None
        if found is None:
            return None
        return _machinery.ModuleSpec(
            fullname, _Alias(), is_package=found.submodule_search_locations is not None
        )


if not any(isinstance(finder, _AliasFinder) for finder in _sys.meta_path):
    _sys.meta_path.insert(0, _AliasFinder())
