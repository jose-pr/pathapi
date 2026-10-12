from __future__ import annotations

import contextlib as _contextlib
import errno as _errno
import html.parser as _html_parser
import io as _io
import os as _os
import re as _re
import time as _time
import typing as _ty
import urllib.parse as _urlparse

from ... import providers as _providers
from ... import FileStat
from .. import DerivedBackend, UriPath
from . import _httpdate
from ._http import (
    HTTPResponse,
    HTTPTransport,
    RequestsTransport,
    TransportOwner,
)
from ._http import _follow
from ._http._follow import (
    FOLLOWED_METHODS as _FOLLOWED_METHODS,
    basic_authorization as _basic_authorization,
    split_userinfo as _split_userinfo,
)
from ._http._status import HTTPStatusError as _HTTPStatusError
from ._http._status import raise_for_status as _raise_for_status
from ._http._headers import check_headers as _check_headers
from ._http._headers import check_url as _check_url
from ._http._headers import has_header as _has_header
from ._http._headers import merge_headers as _merge_headers
from ._http._headers import peer_text

DEFAULT_TIMEOUT = (10, 60)
"""`(connect, read)` timeout, in seconds, `HttpBackend` sends with every
request unless the caller supplies `timeout` (`HttpBackend(timeout=...)`,
`with_transport(timeout=...)`, `with_session(..., timeout=...)`, or per
request); `timeout=None` there waits without a limit."""

MAX_LISTING_BYTES = 8 * 1024 * 1024
"""Most bytes of a directory-index body `HttpPath` reads to list a directory
(after any content decoding); a longer body raises `OSError(EFBIG)`. Raise it
for an index that is legitimately larger; parsing costs about a second per
megabyte. Read at the time of each listing."""

_RE_URL_SCHEME = _re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")

_IDENTITY_ENCODING = {"Accept-Encoding": "identity"}
"""Sent on content GETs and `stat()` probes: a transport advertises
gzip/deflate by default, and a stored blob must not come back as its encoded
form, while `Content-Length` would count the encoded bytes."""


_ERRNOS = {
    FileNotFoundError: _errno.ENOENT,
    PermissionError: _errno.EACCES,
    FileExistsError: _errno.EEXIST,
    IsADirectoryError: _errno.EISDIR,
    NotADirectoryError: _errno.ENOTDIR,
}


def _path_error(error_cls, path_obj) -> OSError:
    """`error_cls` built as pathlib builds it -- `(errno, strerror,
    filename)` -- so `e.errno` and `e.filename` are set."""
    code = _ERRNOS.get(error_cls, _errno.EIO)
    return error_cls(code, _os.strerror(code), str(path_obj))


def _server_size(text: "str | None") -> "int | None":
    """A byte count a server stated (`Content-Length`, `getcontentlength`) as
    an int, or `None` when it is not a plain non-negative decimal number."""
    text = (text or "").strip()
    if text.isascii() and text.isdigit() and len(text) <= 19:
        return int(text)
    return None


@_contextlib.contextmanager
def _translate_http_errors(path_obj, *, conflict=FileExistsError):
    """Map a failed request to a pathlib exception for `path_obj`.
    `conflict` is what a 409 means: for a write (PUT, MKCOL, MOVE) RFC 4918
    uses 409 for a missing parent collection, so writers pass
    FileNotFoundError."""
    # `from None`: a chained cause is printed by every traceback, and a
    # transport's own error must not be what a log shows. The status or the
    # exception type stays in the message.
    try:
        yield
    except _HTTPStatusError as e:
        response = e.response
        status = response.status
        reason = peer_text(response.reason or "")
        if status in (404, 410):
            raise _path_error(FileNotFoundError, path_obj) from None
        elif status in (401, 403):
            raise _path_error(PermissionError, path_obj) from None
        elif status == 409:
            raise _path_error(conflict, path_obj) from None
        elif status in (405, 501):
            raise PermissionError(
                _errno.EACCES,
                f"Method not allowed (HTTP {status})",
                str(path_obj),
            ) from None
        else:
            raise OSError(
                _errno.EIO,
                f"HTTP Error {status} {reason}".rstrip() + f" for {path_obj}",
            ) from None
    except TimeoutError as e:
        raise TimeoutError(f"Timeout for {path_obj} ({type(e).__name__})") from None
    except ConnectionError as e:
        raise ConnectionError(
            f"Connection error for {path_obj} ({type(e).__name__})"
        ) from None


_RE_ISO8601 = _re.compile(r"\d{4}-\d+-\d+T\d+:\d{2}:\d{2}Z")
_DATETIME_FMTs = (
    (_re.compile(r"\d+-[A-S][a-y]{2}-\d{4} \d+:\d{2}:\d{2}"), "%d-%b-%Y %H:%M:%S"),
    (_re.compile(r"\d+-[A-S][a-y]{2}-\d{4} \d+:\d{2}"), "%d-%b-%Y %H:%M"),
    (_re.compile(r"\d{4}-\d+-\d+ \d+:\d{2}:\d{2}"), "%Y-%m-%d %H:%M:%S"),
    (_RE_ISO8601, "%Y-%m-%dT%H:%M:%SZ"),
    (_re.compile(r"\d{4}-\d+-\d+ \d+:\d{2}"), "%Y-%m-%d %H:%M"),
    (_re.compile(r"\d{4}-[A-S][a-y]{2}-\d+ \d+:\d{2}:\d{2}"), "%Y-%b-%d %H:%M:%S"),
    (_re.compile(r"\d{4}-[A-S][a-y]{2}-\d+ \d+:\d{2}"), "%Y-%b-%d %H:%M"),
    (
        _re.compile(r"[F-W][a-u]{2} [A-S][a-y]{2} +\d+ \d{2}:\d{2}:\d{2} \d{4}"),
        "%a %b %d %H:%M:%S %Y",
    ),
    (
        _re.compile(r"[F-W][a-u]{2}, \d+ [A-S][a-y]{2} \d{4} \d{2}:\d{2}:\d{2} \S+"),
        "%a, %d %b %Y %H:%M:%S %Z",
    ),
    (_re.compile(r"\d{4}-\d+-\d+"), "%Y-%m-%d"),
    (_re.compile(r"\d+/\d+/\d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}"), "%d/%m/%Y %H:%M:%S %z"),
    (_re.compile(r"\d{2} [A-S][a-y]{2} \d{4}"), "%d %b %Y"),
)

_RE_FILESIZE = _re.compile(r"\d[\d,]*(\.\d+)? ?[BKMGTPEZY]|\d[\d,]*|-", _re.I)
_RE_COMMONHEAD = _re.compile(
    "Name|(Last )?modifi(ed|cation)|date|Size|Description|Metadata|Type|Parent Directory",
    _re.I,
)
_RE_HEAD_NAME = _re.compile("name$|^file|^download")
_RE_HEAD_MOD = _re.compile("modifi|^uploaded|date|time")
_RE_HEAD_SIZE = _re.compile("size|bytes$")


def _human2bytes(s):
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        symbols = "BKMGTPEZY"
        letter = s[-1:].strip().upper()
        num = float(s[:-1])
        prefix = {symbols[0]: 1}
        for i, sym in enumerate(symbols[1:]):
            prefix[sym] = 1 << (i + 1) * 10
        return int(num * prefix.get(letter, 1))


def _aherf2filename(a_href):
    isdir = "/" if a_href.endswith("/") else ""
    path = _urlparse.urlsplit(a_href).path
    # The URI layer's handler: a name that is not UTF-8 (`caf%E9`) keeps its
    # bytes, so the child's request names the file the server listed.
    name = _urlparse.unquote(path.rstrip("/"), errors="surrogateescape")
    return name.rsplit("/", 1)[-1] + isdir


_CHUNK = 64 * 1024

_DEFAULT_PORTS = {"http": 80, "https": 443}

# A listing is parsed only from an HTML reply (or one with no Content-Type).
_LISTING_TYPES = ("text/html", "application/xhtml+xml")


def _origin(split: _urlparse.SplitResult) -> "tuple[str, str, int | None]":
    scheme = split.scheme.lower()
    try:
        port = split.port
    except ValueError:
        port = -1
    return scheme, (split.hostname or ""), port or _DEFAULT_PORTS.get(scheme)


class _DirectoryListingParser(_html_parser.HTMLParser):
    """Scrapes an HTML directory index into `_FileEntry`s.

    `base_url` is the URL the listing was fetched from (after redirects).
    When given, an entry is kept only if its href, resolved against that URL
    as a browser resolves it, names a direct child of the listed directory
    on the same origin -- so a page's links elsewhere, and a reverse proxy
    whose `<title>` names a different path than the request, are handled.
    Without it, absolute hrefs are scoped by the "Index of ..." title."""

    def __init__(self, base_url: "str | None" = None):
        super().__init__()
        self.listing = []
        self.base_url = base_url
        self._base_origin = None
        self._base_segments = None
        if base_url:
            split = _urlparse.urlsplit(base_url)
            self._base_origin = _origin(split)
            self._base_segments = [
                _urlparse.unquote(segment)
                for segment in split.path.rstrip("/").split("/")
            ]

        self.in_title = False
        self.in_pre = False
        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.in_a = False

        self.title_text = ""
        self.cwd = None

        self.table_rows = []
        self.current_row = []
        self.current_cell_text = []
        self.current_cell_href = None
        self.headers = None

        self.last_href = None
        self.pre_collect_data = False
        self.pre_data_buffer = []

        self.all_links = []
        self.current_a_href = None
        self.current_a_text = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self.in_title = True
            self.title_text = ""
        elif tag == "pre":
            self.in_pre = True
        elif tag == "table":
            self.in_table = True
            self.table_rows = []
            self.headers = None
        elif tag == "tr" and self.in_table:
            self.in_tr = True
            self.current_row = []
        elif (tag == "td" or tag == "th") and self.in_tr:
            self.in_td = True
            self.current_cell_text = []
            self.current_cell_href = None
        elif tag == "a":
            self.in_a = True
            href = attrs_dict.get("href")
            if href:
                self.current_a_href = href
                self.current_a_text = []
                if self.in_td:
                    self.current_cell_href = href
                elif self.in_pre:
                    self._flush_pre_entry()
                    self.last_href = href
                    self.pre_collect_data = False

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            title = self.title_text.strip()
            if title.startswith("Index of "):
                self.cwd = title[9:]
        elif tag == "pre":
            self._flush_pre_entry()
            self.in_pre = False
        elif tag == "table":
            self.in_table = False
            self._process_table()
        elif tag == "tr" and self.in_tr:
            self.in_tr = False
            self.table_rows.append(self.current_row)
        elif (tag == "td" or tag == "th") and self.in_td:
            self.in_td = False
            cell_text = "".join(self.current_cell_text).strip()
            self.current_row.append((cell_text, self.current_cell_href))
        elif tag == "a":
            self.in_a = False
            if self.current_a_href:
                text = "".join(self.current_a_text).strip()
                self.all_links.append((text, self.current_a_href))
                self.current_a_href = None
            if self.in_pre and self.last_href:
                self.pre_data_buffer = []
                self.pre_collect_data = True

    def handle_data(self, data):
        if self.in_title:
            self.title_text += data
        elif self.in_td:
            self.current_cell_text.append(data)
        elif self.in_pre and self.pre_collect_data:
            self.pre_data_buffer.append(data)
        if self.in_a:
            self.current_a_text.append(data)

    def _is_child_href(self, href) -> bool:
        """Whether `href`, resolved against `base_url`, is a direct child of
        the listed directory on the same scheme, host and port."""
        split = _urlparse.urlsplit(_urlparse.urljoin(self.base_url, href))
        if _origin(split) != self._base_origin:
            return False
        segments = [
            _urlparse.unquote(segment) for segment in split.path.rstrip("/").split("/")
        ]
        base = self._base_segments
        return (
            len(segments) == len(base) + 1
            and segments[:-1] == base
            and segments[-1] != ""
        )

    def _is_ancestor_href(self, href):
        # With a `base_url`, anything but a direct child is skipped: the parent link is
        # not always "../" (it may be the absolute "/files/"), yet a server that renders
        # every entry as an absolute href must keep its listing, so the filter is scoped.
        if self.base_url:
            return not self._is_child_href(href)
        if not href.startswith("/"):
            return False
        if not self.cwd:
            return True
        path = _urlparse.unquote(_urlparse.urlsplit(href).path)
        cwd = self.cwd if self.cwd.endswith("/") else self.cwd + "/"
        # a strict descendant of cwd is a real child; cwd itself (a
        # self-referencing "up" link, e.g. at the site root) or anything
        # outside cwd is the ancestor/parent link.
        return path == cwd or not path.startswith(cwd)

    def _flush_pre_entry(self):
        if not self.last_href:
            return

        name = _aherf2filename(self.last_href)
        if (
            name in ("Parent Directory", "..", "../")
            or self.last_href.startswith("?")
            or self._is_ancestor_href(self.last_href)
        ):
            self.last_href = None
            return

        modified = None
        size = None
        size_exact = True
        description = None

        text = (
            "".join(self.pre_data_buffer).replace("\r", "").split("\n", 1)[0].lstrip()
        )
        if text:
            for regex, fmt in _DATETIME_FMTs:
                match = regex.match(text)
                if match:
                    try:
                        modified = _time.strptime(match.group(0), fmt)
                    except ValueError:
                        pass
                    text = text[match.end() :].lstrip()
                    break

            match = _RE_FILESIZE.match(text)
            if match:
                sizestr = match.group(0)
                if sizestr != "-":
                    size = _human2bytes(sizestr.replace(" ", "").replace(",", ""))
                    size_exact = _is_exact_size(sizestr)
                text = text[match.end() :].lstrip()

            if text:
                description = text.rstrip()
                if description == "/":
                    name += "/"
                    description = None

        self.listing.append(_FileEntry(name, modified, size, description, size_exact))
        self.last_href = None

    def _process_table(self):
        started = False
        for row in self.table_rows:
            has_head = False
            cell_texts = [cell[0] for cell in row]
            for text in cell_texts:
                if _RE_COMMONHEAD.search(text):
                    has_head = True
                    break

            if has_head and not started:
                self.headers = []
                name_found = False
                for text in cell_texts:
                    norm = text.strip(" \t\n\r\x0b\x0c\xa0↑↓").lower()
                    if not norm:
                        continue
                    if not name_found and _RE_HEAD_NAME.search(norm):
                        self.headers.append("name")
                        name_found = True
                    elif norm in ("size", "description"):
                        self.headers.append(norm)
                    elif _RE_HEAD_MOD.search(norm):
                        self.headers.append("modified")
                    elif _RE_HEAD_SIZE.search(norm):
                        self.headers.append("size")
                    elif norm.endswith("signature"):
                        self.headers.append("signature")
                    else:
                        self.headers.append("description")
                if not self.headers:
                    self.headers = ["name", "modified", "size", "description"]
                elif not name_found:
                    self.headers[0] = "name"
                started = True
                continue

            if started:
                file_name = None
                file_mod = None
                file_size = None
                file_size_exact = True
                file_desc = None

                status = 0
                for cell_text, cell_href in row:
                    if status >= len(self.headers):
                        break

                    header = self.headers[status]
                    if header == "name":
                        if not cell_href or cell_href.startswith("#"):
                            continue
                        name_val = cell_text.strip()
                        if name_val == "Parent Directory" or cell_href == "../":
                            break
                        if self.base_url and not self._is_child_href(cell_href):
                            break
                        file_name = _aherf2filename(cell_href)
                        status = 1
                    elif header == "modified":
                        timestr = cell_text.strip()
                        if timestr:
                            for regex, fmt in _DATETIME_FMTs:
                                match = regex.match(timestr)
                                if match:
                                    try:
                                        file_mod = _time.strptime(match.group(0), fmt)
                                    except ValueError:
                                        pass
                                    break
                        status += 1
                    elif header == "size":
                        sizestr = cell_text.strip().replace(",", "")
                        if sizestr and sizestr != "-":
                            match = _RE_FILESIZE.match(sizestr)
                            if match:
                                file_size = _human2bytes(
                                    match.group(0).replace(" ", "")
                                )
                                file_size_exact = _is_exact_size(match.group(0))
                        status += 1
                    elif header == "description":
                        file_desc = cell_text or None
                        status += 1
                    else:
                        status += 1

                if file_name:
                    self.listing.append(
                        _FileEntry(
                            file_name, file_mod, file_size, file_desc, file_size_exact
                        )
                    )

    def close(self):
        super().close()
        self._flush_pre_entry()
        if not self.listing:
            for text, href in self.all_links:
                name = _aherf2filename(href)
                if (
                    name in ("Parent Directory", "..", "../")
                    or href.startswith("?")
                    or self._is_ancestor_href(href)
                ):
                    continue
                self.listing.append(_FileEntry(name, None, None, None))


class _FileEntry(_ty.NamedTuple):
    name: str
    modified: _ty.Optional[_time.struct_time]
    size: _ty.Optional[int]
    description: _ty.Optional[str]
    # False when `size` came from a human-readable column ("1.2K"): the
    # value is approximate and must not be reported as `st_size`.
    size_exact: bool = True


def _is_exact_size(sizestr: str) -> bool:
    sizestr = sizestr.replace(" ", "").replace(",", "")
    if sizestr[-1:] in ("b", "B"):
        sizestr = sizestr[:-1]
    return sizestr.isdigit()


def _charset(headers) -> str:
    """The charset a `Content-Type` names, `utf-8` when it names none."""
    for part in (headers.get("Content-Type") or "").split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip("\"'") or "utf-8"
    return "utf-8"


class _ResponseReader(_io.RawIOBase):
    """Raw read stream over a streamed response body.

    The transport has decoded a body the server encoded anyway (despite
    `Accept-Encoding: identity`), and a failure in the middle of the body
    surfaces as `TimeoutError`/`ConnectionError`/`OSError` naming the path.
    A decoded chunk larger than the caller's buffer is kept for the next
    `readinto()`."""

    def __init__(self, path, response: HTTPResponse, chunk_size: int):
        super().__init__()
        self._path = path
        self._response = response
        self._chunks = response.iter_bytes(chunk_size)
        self._pending = b""
        self._offset = 0

    def readable(self):
        return True

    def _next_chunk(self) -> bytes:
        path = self._path
        with _translate_http_errors(path):
            try:
                return next(self._chunks, b"")
            except OSError as error:
                if type(error) is OSError and error.errno == _errno.EIO:
                    raise OSError(_errno.EIO, f"{error.strerror} for {path}") from None
                raise

    def readinto(self, buffer):
        view = memoryview(buffer).cast("B")
        if not view:
            return 0
        while self._offset >= len(self._pending):
            chunk = self._next_chunk()
            if not chunk:
                return 0
            self._pending, self._offset = chunk, 0
        count = min(len(view), len(self._pending) - self._offset)
        view[:count] = self._pending[self._offset : self._offset + count]
        self._offset += count
        return count

    def close(self):
        if not self.closed:
            try:
                self._response.close()
            finally:
                super().close()


def _response_reader(path, response: HTTPResponse, buffering: int):
    buffer_size = _io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering
    raw = _ResponseReader(path, response, buffer_size or _io.DEFAULT_BUFFER_SIZE)
    return raw if buffer_size == 0 else _io.BufferedReader(raw, buffer_size)


_EXCLUSIVE_CREATE = {"headers": {"If-None-Match": "*"}}
"""Request keywords that make a `PUT` create the file only if it is absent
(RFC 9110 13.1.2)."""


class _UploadStream(_io.BytesIO):
    """A write buffer whose `close()` uploads its content through `_upload()`.

    `_ready` is set by a subclass as the last step of its constructor. A
    stream whose constructor raised stays inert: `IOBase.__del__` still calls
    `close()` on it, and that must neither upload a half-built buffer nor
    fail on state the constructor never reached."""

    _ready = False

    def __init__(self, path: "HttpPath"):
        super().__init__()
        self._path = path

    def _upload(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self):
        if self.closed:
            return
        try:
            if self._ready:
                self._upload(self.getvalue())
        finally:
            # Closed even when the upload fails: a later close() (context
            # manager cleanup, or garbage collection) must not send it again.
            super().close()


class HttpWriteStream(_UploadStream):
    def __init__(self, path: "HttpPath", exclusive: bool = False):
        super().__init__(path)
        # `exclusive` (mode "x"): the upload carries `If-None-Match: *`, and a
        # 412 reply means the file appeared since the caller looked.
        self._exclusive = exclusive
        self._ready = True

    def _upload(self, data):
        backend = self._path.backend
        extra = _EXCLUSIVE_CREATE if self._exclusive else {}
        with _translate_http_errors(self._path, conflict=FileNotFoundError):
            resp = backend.request(
                backend.write_method, self._path.as_uri(), data=data, **extra
            )
            if self._exclusive and resp.status == 412:
                raise _path_error(FileExistsError, self._path)
            _raise_for_status(resp)


class HttpAppendStream(_UploadStream):
    def __init__(self, path: "HttpPath"):
        super().__init__(path)
        if path.backend.append_mode == "rewrite":
            try:
                existing = path.read_bytes()
            except FileNotFoundError:
                existing = b""
            self.write(existing)
        else:
            # "patch" mode: stat the resource to get its size as start offset.
            # Drop a listing-derived hint first: an offset taken from a hint
            # would overwrite the file wherever the listing was wrong.
            path._pop_stat_hint()
            try:
                stat, size_stated = path._head_stat()
            except FileNotFoundError:
                self._start_offset = 0
                self._existed = False
            else:
                if not size_stated:
                    raise OSError(
                        _errno.EIO,
                        f"Cannot append to {path} in patch mode: the HEAD reply "
                        "has no Content-Length, so the end of the file is unknown",
                    )
                self._start_offset = stat.st_size
                self._existed = True
        self._ready = True

    def _upload(self, data):
        backend = self._path.backend
        with _translate_http_errors(self._path, conflict=FileNotFoundError):
            if backend.append_mode == "rewrite":
                resp = backend.request(
                    backend.write_method, self._path.as_uri(), data=data
                )
                _raise_for_status(resp)
            else:
                # "patch" mode: send only new content via Content-Range
                if not data:
                    # `bytes N-(N-1)/*` is not a valid range: there is
                    # nothing to append, only a missing file to create.
                    if not self._existed:
                        resp = backend.request(
                            backend.write_method, self._path.as_uri(), data=b""
                        )
                        _raise_for_status(resp)
                    return
                start = self._start_offset
                end = start + len(data) - 1
                headers = {"Content-Range": f"bytes {start}-{end}/*"}
                resp = backend.request(
                    "PATCH", self._path.as_uri(), data=data, headers=headers
                )
                _raise_for_status(resp)


def _redirect_target(uri: "HttpPath|str", resp) -> "HttpPath|str":
    """The URL for `HttpBackend.request` that the redirect `resp`, the answer
    to `uri`, leads to. Within the same scheme, host and port it keeps the
    userinfo of `uri` (the credentials continue); `uri` itself when there is
    no usable `Location`."""
    full = uri if isinstance(uri, str) else uri.as_uri(False)
    url = _split_userinfo(full)[0]
    location = _follow.redirect_location(url, resp)
    if location is None:
        return uri
    target = _urlparse.urlsplit(location)
    if _origin(target) == _origin(_urlparse.urlsplit(url)):
        target = target._replace(netloc=_urlparse.urlsplit(full).netloc)
    return target.geturl()


_UNSET = object()
"""The `timeout` of a call that did not give one: the backend's own."""

_BASE_HEADERS = {"User-Agent": "pathlib-next", "Accept": "*/*"}


class HttpBackend(TransportOwner):
    """The transport and the settings shared by an `HttpPath` tree (see
    `with_transport()` and `with_session()`).

    `transport` is any object with a `send()` method as
    `HTTPTransport.send()`; without one the backend builds the built-in
    `HTTPTransport` on first use (`PATHLIB_NEXT_HTTP_TRANSPORT=requests`
    builds `RequestsTransport` instead) and closes it with `close()` or at the
    end of a `with` block. A transport it was given stays the caller's.
    `headers` are sent with every request, request-specific headers merged
    over them by name; every request gets `timeout=DEFAULT_TIMEOUT`
    (`(10, 60)` seconds) unless `timeout` here or in the call says otherwise
    (`None` waits without limit). URL userinfo (`http://user:password@host/`)
    is never sent inside the request URL: it is stripped and sent as an
    `Authorization: Basic` header unless `headers` or the call name one.

    This backend, not the transport, follows redirects. `GET`, `HEAD` and
    `OPTIONS` follow up to 30; a redirect that changes the scheme, host or
    port drops `Authorization` (and nothing else). Any other method
    (`PROPFIND`, `PUT`, `PATCH`, `DELETE`, `MKCOL`, `MOVE`, a `write_method`
    such as `POST`) is sent without following: a 307 or 308 (for `PROPFIND` also
    a 301, 302 or 303) to the same scheme, host and port is sent once more
    with the same method and body, and any other 3xx, a redirect to another
    origin or a second redirect raises `OSError(EIO)`."""

    __slots__ = ("headers", "timeout", "write_method", "append_mode", "__weakref__")

    def __init__(
        self,
        transport=None,
        *,
        headers=None,
        timeout=DEFAULT_TIMEOUT,
        write_method: str = "PUT",
        append_mode: str = "rewrite",
    ):
        super().__init__(transport)
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.write_method = write_method
        self.append_mode = append_mode

    def __repr__(self) -> str:
        return f"{type(self).__name__}(write_method={self.write_method!r})"

    def request(
        self,
        method,
        uri: "HttpPath|str",
        *,
        data=None,
        headers=None,
        stream: bool = False,
        allow_redirects=None,
        timeout=_UNSET,
    ) -> HTTPResponse:
        """Send `method` to `uri` and return the final response. A `data`
        that is not `bytes` (a file, an iterable) cannot be sent twice, so it
        is not re-sent after a redirect or a dropped connection.
        `allow_redirects=False` returns the first answer of a `GET`, `HEAD`
        or `OPTIONS` as it is. `ValueError` for a URL or header with a control
        character, before anything is sent."""
        url, auth = _split_userinfo(uri if isinstance(uri, str) else uri.as_uri(False))
        sent = _merge_headers(_BASE_HEADERS, self.headers, headers)
        if auth is not None and not _has_header(sent, "Authorization"):
            sent["Authorization"] = _basic_authorization(auth)
        _check_url(url)
        _check_headers(sent)
        if timeout is _UNSET:
            timeout = self.timeout
        if isinstance(data, str):
            data = data.encode("utf-8")
        transport = self.transport
        if method.upper() in _FOLLOWED_METHODS and data is None:
            if allow_redirects is False:
                return transport.send(
                    method, url, headers=sent, timeout=timeout, stream=stream
                )
            return _follow.send_following(
                transport, method, url, headers=sent, timeout=timeout, stream=stream
            )
        return _follow.send_once(
            transport,
            method,
            url,
            headers=sent,
            body=data,
            timeout=timeout,
            stream=stream,
        )


class _DerivedHttpBackend(HttpBackend, DerivedBackend):
    """What `HttpPath._initbackend()` returns: the class itself says the path
    derived this backend for itself (see `UriPath._supplied_backend()`)."""

    __slots__ = ()


class HttpPath(UriPath):
    """`http`/`https` scheme: read/write access over HTTP (`PUT`/`DELETE`
    for writes/deletes, configurable via `with_transport()`), listing
    directories by scraping an Apache/nginx-style HTML index with a
    zero-dependency in-house parser (`_DirectoryListingParser`). Needs no
    third-party library: requests go through `HTTPTransport` unless a
    transport is given."""

    __SCHEMES = ("http", "https")
    __slots__ = ()

    if _ty.TYPE_CHECKING:
        backend: HttpBackend

    def _initbackend(self):
        return _DerivedHttpBackend(timeout=DEFAULT_TIMEOUT)

    def _listdir(self) -> list[_FileEntry]:
        # The slash form first: a server redirects a directory's slash-less URL to it, at a
        # round trip per listing. One that answers 404 for it (a file, or a server that
        # serves the bare path only) is asked again for the path as given.
        slashed = self if self.path.endswith("/") else self.with_path(self.path + "/")
        try:
            req = self._get(slashed)
        except FileNotFoundError:
            if slashed is self:
                raise
            req = self._get(self)
        try:
            content_type = req.headers.get("Content-Type") or ""
            mime = content_type.split(";", 1)[0].strip().lower()
            if mime and mime not in _LISTING_TYPES:
                # A file: pathlib's iterdir() raises, and its body -- maybe
                # gigabytes -- is never downloaded to look for links.
                raise _path_error(NotADirectoryError, self)
            text = self._read_listing(req)
        finally:
            req.close()
        # Scoped by the URL actually answered (after redirects), not by the
        # page's own <title>.
        parser = _DirectoryListingParser(base_url=req.url)
        parser.feed(text)
        parser.close()
        return parser.listing

    def _read_listing(self, req: HTTPResponse) -> str:
        """The body of the directory index `req` as text, read the way
        `open()` reads a file: a cut-short, stalled or undecodable body
        raises `TimeoutError`/`OSError` naming the path, and one longer than
        `MAX_LISTING_BYTES` raises `OSError(EFBIG)`."""
        limit = MAX_LISTING_BYTES
        too_large = OSError(
            _errno.EFBIG,
            f"Directory index of {self} is larger than {limit} bytes "
            "(MAX_LISTING_BYTES)",
        )
        stated = _server_size(req.headers.get("Content-Length"))
        if stated is not None and stated > limit:
            raise too_large
        reader = _ResponseReader(self, req, _CHUNK)
        chunks = []
        size = 0
        while chunk := reader.read(_CHUNK):
            size += len(chunk)
            if size > limit:
                raise too_large
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            return body.decode(_charset(req.headers), "replace")
        except LookupError:
            return body.decode("utf-8", "replace")

    def _get(self, uri, **kwargs) -> HTTPResponse:
        """A streamed `GET` of `uri` that succeeded, its body unread. A
        failure is translated to the pathlib exception for this path, and the
        connection is released first: `stream=True` leaves it held."""
        with _translate_http_errors(self):
            req = self.backend.request("GET", uri, stream=True, **kwargs)
            try:
                _raise_for_status(req)
            except BaseException:
                req.close()
                raise
        return req

    def _scandir(self):
        # `_listdir()`'s single GET already carries type/size/mtime for
        # every child -- reuse it instead of `iterdir()` + a HEAD per child.
        for entry in self._listdir():
            # A subdirectory entry carries a trailing "/" (htmllistparse's name is "sub/"):
            # strip it, or the child's path ends in "/" and `Pathname.name` (the last segment)
            # is "".
            is_dir = entry.name.endswith("/")
            name = entry.name.removesuffix("/")
            if not _providers.is_safe_child_name(name):
                # A listing is untrusted: wsgidav renders its parent row as `<a href="..">` and a
                # `<pre>` index can carry `./`. "." or ".." as a child would make `unlink()` DELETE
                # the parent collection and `walk()` loop forever. Filtered here for every parser.
                continue
            if is_dir:
                stat = FileStat(
                    st_size=0, st_mtime=_httpdate.parsedate(entry.modified), is_dir=True
                )
            elif entry.size is not None and entry.size_exact and entry.modified:
                stat = FileStat(
                    st_size=entry.size,
                    st_mtime=_httpdate.parsedate(entry.modified),
                    is_dir=False,
                )
            else:
                # No exact byte count or no date in the listing (the stdlib `<ul>` index, a "1.2K"
                # column): the entry is a file, but a size or time would be made up, so the hint is
                # type-only and `stat()` asks the server.
                stat = FileStat(is_dir=False, size_known=False)
            yield name, stat

    def _is_dir(self, resp: HTTPResponse):
        # Judged on the FINAL response, after redirects (http->https, CDN and "latest"
        # download links redirect to files), by the path of that URL: a query or fragment
        # does not make a directory a file.
        return _urlparse.urlsplit(resp.url).path.endswith(("/", "/..", "/."))

    def stat(self, *, follow_symlinks=True, walk_up_last_modified=False):
        hint = self._pop_stat_hint()
        if hint is not None:
            return hint
        return self._head_stat(walk_up_last_modified)[0]

    def _head_stat(self, walk_up_last_modified=False) -> "tuple[FileStat, bool]":
        """`stat()` without the listing hint, and whether the server stated
        the size: a file whose HEAD reply has no `Content-Length` reports
        `st_size` 0, which is not its length."""
        with _translate_http_errors(self):
            # The caller's own spelling first: a trailing-slash path the
            # server answers is a directory, even when the slash-less URL
            # answers too (wsgidav serves both `/d` and `/d/` with 200).
            check = (
                [self, self.with_path(self.path.removesuffix("/"))]
                if self.path.endswith("/")
                else [self]
            )
            for uri in check:
                resp = self.backend.request(
                    "HEAD", uri, allow_redirects=False, headers=_IDENTITY_ENCODING
                )
                resp.close()
                if resp.status == 405:
                    # Some servers reject HEAD outright; fall back to GET.
                    resp = self.backend.request(
                        "GET",
                        uri,
                        allow_redirects=False,
                        stream=True,
                        headers=_IDENTITY_ENCODING,
                    )
                    resp.close()
                if resp.status < 400:
                    break

            if _follow.is_redirect(resp):
                # Where the first answer pointed, not the same request again.
                target = _redirect_target(uri, resp)
                resp = self.backend.request("HEAD", target, headers=_IDENTITY_ENCODING)
                resp.close()
                if resp.status == 405:
                    # The HEAD-405 fallback again after a redirect: a server or proxy that rejects HEAD
                    # everywhere would otherwise raise PermissionError for an existing path.
                    resp = self.backend.request(
                        "GET", target, stream=True, headers=_IDENTITY_ENCODING
                    )
                    resp.close()
            _raise_for_status(resp)
            # From the final URL, once any redirect has been followed.
            is_dir = self._is_dir(resp)

        # A missing, non-numeric or negative Content-Length is no size.
        stated = None if is_dir else _server_size(resp.headers.get("Content-Length"))
        st_size = stated or 0
        lm = resp.headers.get("Last-Modified")
        if lm is None and walk_up_last_modified:
            parent = self.parent
            if self != parent:
                try:
                    entry = next(
                        filter(
                            lambda p: p.name.removesuffix("/") == self.name,
                            parent._listdir(),
                        )
                    )
                    if entry and entry.modified:
                        lm = entry.modified
                except (StopIteration, OSError):
                    pass

        size_stated = is_dir or stated is not None
        stat = FileStat(
            st_size=st_size, st_mtime=_httpdate.parsedate(lm), is_dir=is_dir
        )
        return stat, size_stated

    def _ensure_missing(self) -> None:
        """Raise `FileExistsError` unless `stat()` says the path is not
        found. Any other failure of the probe (a 5xx, a 403, a timeout)
        propagates: it must not read as permission to create."""
        try:
            self.stat()
        except FileNotFoundError:
            return
        raise _path_error(FileExistsError, self)

    def _open(
        self,
        mode="r",
        buffering=-1,
    ):
        if mode == "r":
            req = self._get(self.as_uri(), headers=_IDENTITY_ENCODING)
            return _response_reader(self, req, buffering)
        if mode == "a":
            return HttpAppendStream(self)
        if mode not in ("w", "x"):
            raise NotImplementedError(f"open(mode={mode!r})")
        if mode == "x":
            self._ensure_missing()
        return HttpWriteStream(self, exclusive=mode == "x")

    def unlink(self, missing_ok=False):
        # A server that honours DELETE on a collection (WebDAV, RFC 4918) removes the whole
        # tree, and unlink() never removes a directory: refuse one. Over http: this uses the
        # HEAD `stat()` heuristic, which misses some collections (see `dav:` for reliable ones).
        if self.is_dir():
            raise IsADirectoryError(
                _errno.EISDIR, _os.strerror(_errno.EISDIR), str(self)
            )
        self._delete(missing_ok=missing_ok)

    def _delete(self, missing_ok=False):
        # The raw DELETE, with no collection guard: `rmdir()` has already
        # verified an empty directory before calling it.
        with _translate_http_errors(self):
            resp = self.backend.request("DELETE", self)
            if resp.status == 404:
                if missing_ok:
                    return
                raise FileNotFoundError(self)
            _raise_for_status(resp)

    def rmdir(self):
        # An empty directory's listing and a file whose body parses to zero entries look
        # alike from `_listdir()`: without this check rmdir() of a file would DELETE it
        # instead of raising NotADirectoryError.
        if not self.is_dir():
            raise NotADirectoryError(self)
        # `_scandir()`, not the raw `_listdir()`: a listing's own "."/".."
        # rows are not children and must not make an empty directory
        # look non-empty.
        for _ in self._scandir():
            raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(self))
        self._delete()

    def with_transport(
        self,
        transport,
        *,
        headers=None,
        timeout=DEFAULT_TIMEOUT,
        write_method: str = "PUT",
        append_mode: str = "rewrite",
    ):
        """This path with a backend that sends through `transport` (any object
        with a `send()` method as `HTTPTransport.send()`; the caller closes
        it). `headers` go with every request; `write_method` (`"PUT"`, or
        e.g. `"POST"`) uploads on close, and `append_mode` is `"rewrite"` or
        `"patch"`."""
        backend = HttpBackend(
            transport,
            headers=headers,
            timeout=timeout,
            write_method=write_method,
            append_mode=append_mode,
        )
        return type(self)(self, backend=backend)

    def with_session(
        self,
        session,
        write_method: str = "PUT",
        append_mode: str = "rewrite",
        **requests_args,
    ):
        """This path with a backend that sends through a `requests.Session`
        (a `RequestsTransport`). `headers` and `timeout` in `requests_args`
        are the backend's; the others (`auth`, `verify`, `proxies`, `cert`, ...) go
        to every `session.request()`. Needs the `http` extra."""
        headers = requests_args.pop("headers", None)
        timeout = requests_args.pop("timeout", DEFAULT_TIMEOUT)
        return self.with_transport(
            RequestsTransport(session, **requests_args),
            headers=headers,
            timeout=timeout,
            write_method=write_method,
            append_mode=append_mode,
        )
