"""HTTP and WebDAV date headers as epoch seconds."""

from __future__ import annotations

import calendar as _calendar
import time as _time
import typing as _ty
from email.utils import parsedate_tz as _parsedate_tz


def parsedate(date: _ty.Union[str, _time.struct_time, tuple, int, float, None]):
    """Convert a date to UTC epoch seconds, independent of the host timezone.

    - `str` (RFC 1123, RFC 850 or asctime, as in `Last-Modified`): parsed
      with `email.utils.parsedate_tz`, and its zone offset applied. A string
      with no zone is read as UTC -- HTTP dates are GMT by definition.
    - `int`/`float`: already epoch seconds, returned unchanged.
    - `struct_time`/tuple: read as UTC (`calendar.timegm`), minus its
      `tm_gmtoff` (or a `parsedate_tz`-style 10th element) when one is set.
    - `None` or an unparseable string: `0`. A string naming a date that does
      not exist ("31 Feb") is not refused: it rolls over to the next month.

    Nothing else is accepted: `bytes`, a `datetime` and a tuple of fewer than
    six items raise `ValueError` or `TypeError` instead of returning `0`.

    Local time (`time.mktime`) is never involved: it shifted every HTTP/DAV
    `st_mtime` by the host's UTC offset and overflowed on Windows for dates
    near the epoch.
    """
    # Missing/unparseable dates yield epoch 0, not "now" -- a caller with no
    # Last-Modified header shouldn't have that read as "just modified" and
    # poison checksum/sync freshness comparisons.
    if date is None:
        return 0
    if isinstance(date, (int, float)):
        return date
    if isinstance(date, str):
        try:
            parsed = _parsedate_tz(date)
            if parsed is None:
                return 0
            return _calendar.timegm(parsed[:6]) - (parsed[9] or 0)
        except (TypeError, ValueError, IndexError, OverflowError):
            return 0
    offset = getattr(date, "tm_gmtoff", None)
    if offset is None and len(date) > 9:
        offset = date[9]
    return _calendar.timegm(tuple(date)[:6]) - (offset or 0)
