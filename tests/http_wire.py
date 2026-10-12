"""Loopback servers for the HTTP transports, scripted at the byte level: an
origin that records what it receives and answers from a script, a proxy, and a
test certificate authority. Nothing here leaves the loopback interface."""

from __future__ import annotations

import datetime
import ipaddress
import pathlib
import socket
import ssl
import threading
import typing

import pytest

CRLF = b"\r\n"


class Request(typing.NamedTuple):
    """One request as the server received it."""

    method: str
    target: str
    headers: "list[tuple[str, str]]"
    body: bytes
    connection: int  # which accepted connection carried it, from 0

    def header(self, name: str):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class Reply(typing.NamedTuple):
    """Bytes to send, and whether to close the connection after them."""

    raw: bytes
    close: bool = False


class Drop:
    """Close the connection without answering."""


class Hang:
    """Never answer; the connection stays open until the server closes."""


def response(status=200, headers=None, body=b"", reason="OK", length=True) -> bytes:
    """A complete HTTP/1.1 response; `Content-Length` is added unless
    `length` is false or `headers` names one."""
    head = [f"HTTP/1.1 {status} {reason}"]
    names = {k.lower() for k in (headers or {})}
    for key, value in (headers or {}).items():
        head.append(f"{key}: {value}")
    if length and "content-length" not in names and "transfer-encoding" not in names:
        head.append(f"Content-Length: {len(body)}")
    return "\r\n".join(head).encode("latin-1") + CRLF + CRLF + body


def _read_until(sock, marker: bytes, limit=1 << 20) -> "tuple[bytes, bytes] | None":
    data = b""
    while marker not in data:
        chunk = sock.recv(65536)
        if not chunk:
            return None
        data += chunk
        if len(data) > limit:
            return None
    head, _, rest = data.partition(marker)
    return head, rest


def _read_exactly(sock, rest: bytes, count: int) -> bytes:
    while len(rest) < count:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    return rest


def _read_chunked(sock, rest: bytes) -> bytes:
    body = b""
    while True:
        while CRLF not in rest:
            chunk = sock.recv(65536)
            if not chunk:
                return body
            rest += chunk
        line, _, rest = rest.partition(CRLF)
        size = int(line.split(b";")[0], 16)
        rest = _read_exactly(sock, rest, size + 2)
        body += rest[:size]
        rest = rest[size + 2 :]
        if size == 0:
            return body


class Wire:
    """An origin on a loopback port. `script(request)` returns what to send:
    `bytes`, a `Reply`, `Drop()` or `Hang()`; the default answers `200 ok`.
    `requests` lists what was received, `connections` how many connections
    were accepted, and `closed` the connections the client closed. With `tls`
    (an `ssl.SSLContext`) every connection is wrapped by the server first."""

    def __init__(self, script=None, tls: "ssl.SSLContext | None" = None):
        self.script = script or (lambda request: response(body=b"ok"))
        self.tls = tls
        self.requests: "list[Request]" = []
        self.connections = 0
        self.closed: "list[int]" = []
        self.handshakes_failed = 0
        self._lock = threading.Lock()
        self._sockets: "list[socket.socket]" = []
        self._stop = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self.host = "127.0.0.1"
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            sockets = list(self._sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._thread.join(timeout=5)

    def _accept(self):
        while not self._stop.is_set():
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                index = self.connections
                self.connections += 1
                self._sockets.append(sock)
            threading.Thread(
                target=self._serve, args=(sock, index), daemon=True
            ).start()

    def _serve(self, sock, index):
        try:
            if self.tls is not None:
                try:
                    sock = self.tls.wrap_socket(sock, server_side=True)
                except (OSError, ssl.SSLError):
                    with self._lock:
                        self.handshakes_failed += 1
                    return
                with self._lock:
                    self._sockets.append(sock)
            while not self._stop.is_set():
                parsed = _read_until(sock, CRLF + CRLF)
                if parsed is None:
                    return
                head, rest = parsed
                lines = head.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                headers = [tuple(line.split(":", 1)) for line in lines[1:]]
                headers = [(k, v.strip()) for k, v in headers]
                length = next(
                    (int(v) for k, v in headers if k.lower() == "content-length"), 0
                )
                chunked = any(
                    k.lower() == "transfer-encoding" and "chunked" in v.lower()
                    for k, v in headers
                )
                if chunked:
                    body = _read_chunked(sock, rest)
                else:
                    body = _read_exactly(sock, rest, length)[:length]
                request = Request(method, target, headers, body, index)
                with self._lock:
                    self.requests.append(request)
                action = self.script(request)
                if isinstance(action, Drop):
                    return
                if isinstance(action, Hang):
                    self._stop.wait(30)
                    return
                if isinstance(action, bytes):
                    action = Reply(action)
                sock.sendall(action.raw)
                if action.close:
                    return
        except (OSError, ssl.SSLError):
            return
        finally:
            with self._lock:
                self.closed.append(index)
            try:
                sock.close()
            except OSError:
                pass


class Proxy:
    """An HTTP proxy on a loopback port. A request in absolute form is
    answered by `script(request)` as an origin would; `CONNECT host:port` is
    answered `200` and then relayed to `upstream` (a `(host, port)`), whatever
    the host asked for, or refused with `refuse` (a status) when given.
    `requests` lists what it received, `CONNECT`s included."""

    def __init__(self, script=None, upstream=None, refuse=None):
        self.script = script or (lambda request: response(body=b"via proxy"))
        self.upstream = upstream
        self.refuse = refuse
        self.requests: "list[Request]" = []
        self._lock = threading.Lock()
        self._sockets: "list[socket.socket]" = []
        self._stop = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            sockets = list(self._sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._thread.join(timeout=5)

    def _accept(self):
        while not self._stop.is_set():
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self._sockets.append(sock)
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock):
        try:
            while not self._stop.is_set():
                parsed = _read_until(sock, CRLF + CRLF)
                if parsed is None:
                    return
                head, rest = parsed
                lines = head.decode("latin-1").split("\r\n")
                method, target, _version = lines[0].split(" ", 2)
                headers = [
                    (k, v.strip()) for k, v in (l.split(":", 1) for l in lines[1:])
                ]
                length = next(
                    (int(v) for k, v in headers if k.lower() == "content-length"), 0
                )
                body = _read_exactly(sock, rest, length)[:length]
                request = Request(method, target, headers, body, 0)
                with self._lock:
                    self.requests.append(request)
                if method == "CONNECT":
                    if self.refuse:
                        sock.sendall(response(self.refuse, reason="Refused"))
                        return
                    self._tunnel(sock)
                    return
                action = self.script(request)
                if isinstance(action, Drop):
                    return
                if isinstance(action, bytes):
                    action = Reply(action)
                sock.sendall(action.raw)
                if action.close:
                    return
        except OSError:
            return
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _tunnel(self, client):
        upstream = socket.create_connection(self.upstream)
        with self._lock:
            self._sockets.append(upstream)
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

        def pump(source, sink):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    sink.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    sink.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        thread = threading.Thread(target=pump, args=(upstream, client), daemon=True)
        thread.start()
        pump(client, upstream)
        thread.join(timeout=5)
        upstream.close()


def refusing_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Authority(typing.NamedTuple):
    """A test certificate authority and a server certificate it signed."""

    ca_file: str
    cert_file: str
    key_file: str

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert_file, self.key_file)
        return context

    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca_file)


def make_authority(directory, *names: str) -> Authority:
    """A CA, and a server certificate for `names` (host names or IP
    addresses) signed by it, written under `directory`."""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def build(subject, issuer, key, signer, extensions):
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
        )
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical=critical)
        return builder.sign(signer, hashes.SHA256())

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = build(
        "test authority",
        "test authority",
        ca_key,
        ca_key,
        [
            (x509.BasicConstraints(ca=True, path_length=None), True),
            (
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                True,
            ),
            (x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False),
        ],
    )
    key = ec.generate_private_key(ec.SECP256R1())
    alt = []
    for name in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt.append(x509.DNSName(name))
    leaf = build(
        names[0],
        "test authority",
        key,
        ca_key,
        [
            (x509.SubjectAlternativeName(alt), False),
            (x509.BasicConstraints(ca=False, path_length=None), True),
            (x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False),
            (
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                False,
            ),
        ],
    )
    directory = pathlib.Path(directory)
    ca_file = directory / "ca.pem"
    cert_file = directory / "server.pem"
    key_file = directory / "server.key"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return Authority(str(ca_file), str(cert_file), str(key_file))
