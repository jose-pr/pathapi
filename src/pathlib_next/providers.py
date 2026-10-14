"""What a class built on `Path` calls: error and follow policies, name and
mode checks, the split of a name into stem and suffix, the commit-on-close
buffer, the LRU cache and the `contains` test.
"""

from __future__ import annotations

from ._commit import CommitOnClose, DiscardOnError
from ._errorpolicy import (
    OfferedOnce,
    as_error_handler,
    as_error_predicate,
    check_follow,
    copy_error_predicate,
)
from ._lru import LRU
from ._names import is_safe_child_name, is_windows_flavoured
from ._permissions import UNCHANGED, parse_mode, parse_owner
from ._stubs import derived_from, notimplemented
from ._tree import TreePath
from .path._pure import _name_stem as name_stem
from .path._pure import _name_suffix as name_suffix
from .path._pure import _name_suffixes as name_suffixes
from .protocols._relation import contains

__all__ = [
    "CommitOnClose",
    "DiscardOnError",
    "LRU",
    "OfferedOnce",
    "TreePath",
    "UNCHANGED",
    "as_error_handler",
    "as_error_predicate",
    "check_follow",
    "contains",
    "copy_error_predicate",
    "is_safe_child_name",
    "is_windows_flavoured",
    "name_stem",
    "name_suffix",
    "name_suffixes",
    "derived_from",
    "notimplemented",
    "parse_mode",
    "parse_owner",
]
