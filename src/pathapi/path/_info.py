"""`Path.info`'s object lives with the protocol that returns it; this name
stays importable and is the same module."""

from __future__ import annotations

import sys as _sys

from ..protocols import _info

_sys.modules[__name__] = _info
