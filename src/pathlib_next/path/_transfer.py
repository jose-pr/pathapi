"""The transfer helpers live with the protocols that call them; this name
stays importable and is the same module."""

from __future__ import annotations

import sys as _sys

from ..protocols import _transfer

_sys.modules[__name__] = _transfer
