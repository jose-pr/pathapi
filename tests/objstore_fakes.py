"""What the loopback Google and Azure fakes in `conftest.py` share: paging, the
two batch wire formats, the digests the services report, and a tuple that
carries a fake's state beside the `(base_url, ...)` values the fixtures yield."""

import base64
import email.parser
import hashlib
import urllib.parse


class ServerInfo(tuple):
    """The values a server fixture yields (so `base_url, bucket = fixture`
    unpacks as before), with the fake's `state` beside them."""

    def __new__(cls, values, state):
        info = super().__new__(cls, values)
        info.state = state
        return info


class FakeState:
    """What a test may read or set on a loopback fake: the stored `objects`
    (name -> bytes), the `requests` it served as `(method, path-and-query)`,
    `page_size` (entries per listing page, None for one page), the names it
    answers with a given status (`refuse`) and `batches`, the object names of
    each batch request it served. `copies` lists the `(source, target)` object
    names of the copy requests it served; `copy_status`, when set, is the
    status it answers every copy request with."""

    def __init__(self, objects):
        self.objects = objects
        self.requests = []
        self.page_size = None
        self.refuse = {}
        self.batches = []
        self.batch_status = 200
        self.metadata = {}
        self.copies = []
        self.copy_status = None


def page_of(entries, offset, limit, page_size):
    """`(page, next offset or None)`: the entries a listing page holds, from
    `offset` on, at most `limit` (the caller's page size) and `page_size` (the
    server's)."""
    sizes = [size for size in (limit, page_size) if size]
    stop = len(entries) if not sizes else offset + min(sizes)
    page = entries[offset:stop]
    return page, (stop if stop < len(entries) else None)


def md5_base64(content):
    return base64.b64encode(hashlib.md5(content).digest()).decode()


def crc32c(content):
    """CRC-32C (Castagnoli) of `content`, as the 32-bit integer."""
    crc = 0xFFFFFFFF
    for byte in content:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def crc32c_base64(content):
    return base64.b64encode(crc32c(content).to_bytes(4, "big")).decode()


def mime_parts(content_type, body):
    """The parts of a multipart body as `(headers, text)` pairs."""
    head = b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n"
    message = email.parser.BytesParser().parsebytes(head + body)
    parts = message.get_payload()
    return [(dict(part.items()), part.get_payload()) for part in parts]


def gcs_batch_deletes(content_type, body):
    """The object names a Google batch request asks to delete, in order."""
    names = []
    for _headers, text in mime_parts(content_type, body):
        method, url, _version = text.split("\n", 1)[0].strip().split(" ", 2)
        assert method == "DELETE", method
        path = urllib.parse.urlsplit(url).path
        names.append(urllib.parse.unquote(path.rsplit("/o/", 1)[1]))
    return names


def gcs_batch_reply(statuses):
    """The `multipart/mixed` body and its content type for a Google batch
    reply with one part per status."""
    boundary = "batch_fake_reply"
    reasons = {204: "No Content", 403: "Forbidden", 404: "Not Found"}
    parts = []
    for index, status in enumerate(statuses, 1):
        reason = reasons.get(status, "Error")
        if status < 300:
            sub = f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n"
        else:
            payload = '{"error": {"code": %d, "message": "%s"}}' % (status, reason)
            sub = (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json; charset=UTF-8\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n{payload}"
            )
        parts.append(
            f"--{boundary}\r\nContent-Type: application/http\r\n"
            f"Content-ID: <response-{index}>\r\n\r\n{sub}\r\n"
        )
    body = "".join(parts) + f"--{boundary}--\r\n"
    return body.encode(), f"multipart/mixed; boundary={boundary}"


def azure_batch_deletes(account, container, body):
    """The blob names an Azure batch request asks to delete, in order."""
    names = []
    for _headers, text in mime_parts(
        "multipart/mixed; boundary=" + _azure_boundary(body), body
    ):
        method, target, _version = text.strip().split("\n", 1)[0].strip().split(" ", 2)
        assert method == "DELETE", method
        path = urllib.parse.unquote(urllib.parse.urlsplit(target).path)
        for prefix in (f"/{account}/{container}/", f"/{container}/"):
            if path.startswith(prefix):
                names.append(path[len(prefix) :])
                break
        else:
            raise AssertionError(path)
    return names


def _azure_boundary(body):
    first = body.split(b"\r\n", 1)[0]
    assert first.startswith(b"--"), first
    return first[2:].decode()


def azure_batch_reply(statuses):
    """The `multipart/mixed` body and content type of an Azure batch reply
    with one part per status."""
    boundary = "batchresponse_fake"
    reasons = {202: "Accepted", 403: "Forbidden", 404: "Not Found"}
    parts = []
    for index, status in enumerate(statuses):
        reason = reasons.get(status, "Error")
        sub = f"HTTP/1.1 {status} {reason}\r\nx-ms-version: 2025-01-05\r\n"
        if status >= 300:
            sub += "x-ms-error-code: BlobError\r\n"
        parts.append(
            f"--{boundary}\r\nContent-Type: application/http\r\n"
            f"Content-ID: {index}\r\n\r\n{sub}\r\n\r\n"
        )
    body = "".join(parts) + f"--{boundary}--\r\n"
    return body.encode(), f"multipart/mixed; boundary={boundary}"
