"""Synchronizing a tree of paths onto another, across any two `Path` classes."""

from __future__ import annotations

from ._events import PathAndStat, SyncEvent
from ._syncer import PathSyncer

__all__ = ["PathAndStat", "PathSyncer", "SyncEvent"]
