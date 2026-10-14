"""How a failed request becomes the `OSError` a path raises."""

from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import os as _os

from ._headers import peer_text
from ._status import HTTPStatusError as _HTTPStatusError

_ERRNOS = {
    FileNotFoundError: _errno.ENOENT,
    PermissionError: _errno.EACCES,
    FileExistsError: _errno.EEXIST,
    IsADirectoryError: _errno.EISDIR,
    NotADirectoryError: _errno.ENOTDIR,
}


def os_error(error_class, subject) -> OSError:
    """`error_class` built as pathlib builds it -- `(errno, strerror,
    filename)` -- for `subject` (a path or a URL, shown through `str()`), so
    `e.errno` and `e.filename` are set."""
    code = _ERRNOS.get(error_class, _errno.EIO)
    return error_class(code, _os.strerror(code), str(subject))


def server_size(text: "str | None") -> "int | None":
    """A byte count a server stated (`Content-Length`, `getcontentlength`) as
    an int, or `None` when it is not a plain non-negative decimal number."""
    text = (text or "").strip()
    if text.isascii() and text.isdigit() and len(text) <= 19:
        return int(text)
    return None


@_contextlib.contextmanager
def translate_errors(subject, *, conflict=FileExistsError):
    """Map a failed request to the exception `pathlib` raises for `subject`.

    An `HTTPStatusError` becomes `FileNotFoundError` (404, 410),
    `PermissionError` (401, 403; 405 and 501 too, naming the status),
    `conflict` (409) or `OSError(EIO)` naming the status; a `TimeoutError` or
    `ConnectionError` from the transport is raised again with a message that
    holds `subject` and the exception's type name. `conflict` is what a 409
    means: for a write (PUT, MKCOL, MOVE) RFC 4918 uses 409 for a missing
    parent collection, so writers pass `FileNotFoundError`. The cause is not
    chained: a traceback would otherwise print a URL, a header or a
    credential that the transport's own error carries."""
    try:
        yield
    except _HTTPStatusError as e:
        response = e.response
        status = response.status
        reason = peer_text(response.reason or "")
        if status in (404, 410):
            raise os_error(FileNotFoundError, subject) from None
        elif status in (401, 403):
            raise os_error(PermissionError, subject) from None
        elif status == 409:
            raise os_error(conflict, subject) from None
        elif status in (405, 501):
            raise PermissionError(
                _errno.EACCES,
                f"Method not allowed (HTTP {status})",
                str(subject),
            ) from None
        else:
            raise OSError(
                _errno.EIO,
                f"HTTP Error {status} {reason}".rstrip() + f" for {subject}",
            ) from None
    except TimeoutError as e:
        raise TimeoutError(f"Timeout for {subject} ({type(e).__name__})") from None
    except ConnectionError as e:
        raise ConnectionError(
            f"Connection error for {subject} ({type(e).__name__})"
        ) from None
