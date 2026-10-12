"""What a class built on `Path` calls: error and follow policies, name and
mode checks, the commit-on-close buffer, the LRU cache and the `contains` test.
"""

from __future__ import annotations

from ._commit import CommitOnClose
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
from ._stubs import notimplemented
from .path._relation import contains

__all__ = [
    "CommitOnClose",
    "LRU",
    "OfferedOnce",
    "UNCHANGED",
    "as_error_handler",
    "as_error_predicate",
    "check_follow",
    "contains",
    "copy_error_predicate",
    "is_safe_child_name",
    "is_windows_flavoured",
    "notimplemented",
    "parse_mode",
    "parse_owner",
]
