"""Transport security defaults of `FTPPath`, asserted at the boundary: what the
server received, and how long a stalled loopback peer can hold a call. Never
contacts an external host.

- A 30 s default timeout, and a stalled server that times out.
- FTPS: certificate and host name verification by default with an explicit
  opt-out; TLS session reuse on data connections.
"""

import datetime
import ipaddress
import pathlib
import socket
import ssl
import threading
import time

import pytest

from pathapi.ftp import FTPBackend, FTPPath

# --- loopback servers -------------------------------------------------------


@pytest.fixture
def stalled_server():
    """Accepts TCP connections and never sends a byte. Yields the port."""
    listener = socket.create_server(("127.0.0.1", 0))
    held = []
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            held.append(conn)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=5)
        for conn in held:
            conn.close()
        listener.close()


# --- FTP: timeout -----------------------------------------------------------


def test_ftp_default_timeout_reaches_ftplib(monkeypatch):
    import ftplib

    received = []

    class _FakeClient:
        def __init__(self, timeout=None):
            received.append(timeout)

        def connect(self, host, port):
            pass

        def login(self, user, password):
            pass

        def set_pasv(self, value):
            pass

    monkeypatch.setattr(ftplib, "FTP", _FakeClient)
    FTPBackend("host").client()
    assert received == [30.0]
    assert FTPPath("/x", host="host").backend.timeout == 30.0


def test_ftp_stalled_server_times_out(stalled_server):
    p = FTPPath("/x", host="127.0.0.1", port=stalled_server, timeout=0.5)
    started = time.monotonic()
    with pytest.raises((socket.timeout, TimeoutError)):
        p.stat()
    assert time.monotonic() - started < 10


# --- FTPS: verification, opt-out, session reuse -----------------------------


def _make_self_signed_cert(directory):
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certfile = pathlib.Path(directory, "cert.pem")
    keyfile = pathlib.Path(directory, "key.pem")
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


class _FtpsServer:
    """Minimal explicit-FTPS server on stdlib `ssl` (pyftpdlib's TLS handler
    needs pyOpenSSL). Enforces TLS session reuse on data connections the
    way vsftpd's default `require_ssl_reuse=YES` does: a data connection
    that does not resume the control session gets `522`."""

    def __init__(self, certfile, keyfile, files):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certfile, keyfile)
        self.files = files
        self.commands = []
        self.data_session_reused = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self._wrapped = []
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self.listener.close()
        # `wrap_socket` detaches the plain socket, which `_handle` then
        # closes to no effect: the TLS control sockets are closed here.
        for control in self._wrapped:
            control.close()

    def _serve(self):
        while True:
            try:
                sock, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock,), daemon=True).start()

    @staticmethod
    def _readline(sock):
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                return None
            line += chunk
        return line[:-2].decode()

    def _handle(self, sock):
        try:
            self._session(sock)
        except (OSError, ssl.SSLError):
            pass
        finally:
            sock.close()

    def _session(self, sock):
        def send(text):
            sock.sendall(text.encode() + b"\r\n")

        send("220 ready")
        prot_p = False
        data_listener = None
        while True:
            line = self._readline(sock)
            if line is None:
                return
            self.commands.append(line)
            verb, _, arg = line.partition(" ")
            verb = verb.upper()
            if verb == "AUTH":
                send("234 proceed")
                sock = self.context.wrap_socket(sock, server_side=True)
                self._wrapped.append(sock)
            elif verb == "USER":
                send("331 password")
            elif verb == "PASS":
                send("230 logged in")
            elif verb in ("PBSZ", "TYPE", "NOOP"):
                send("200 ok")
            elif verb == "PROT":
                prot_p = arg.upper() == "P"
                send("200 ok")
            elif verb == "PASV":
                data_listener = socket.create_server(("127.0.0.1", 0))
                port = data_listener.getsockname()[1]
                send(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})")
            elif verb == "RETR":
                if arg not in self.files:
                    send("550 not found")
                    continue
                send("150 opening data connection")
                conn, _ = data_listener.accept()
                data_listener.close()
                if prot_p:
                    conn = self.context.wrap_socket(conn, server_side=True)
                    reused = conn.session_reused
                    self.data_session_reused.append(reused)
                    if not reused:
                        conn.close()
                        send("522 SSL connection failed: session reuse required")
                        continue
                    conn.sendall(self.files[arg])
                    conn = conn.unwrap()
                else:
                    conn.sendall(self.files[arg])
                conn.close()
                send("226 transfer complete")
            elif verb == "QUIT":
                send("221 bye")
                return
            else:
                send("502 not implemented")


@pytest.fixture
def ftps_server(tmp_path):
    certfile, keyfile = _make_self_signed_cert(tmp_path)
    server = _FtpsServer(certfile, keyfile, {"/a.txt": b"hello"})
    try:
        yield server, certfile
    finally:
        server.close()


def test_ftps_self_signed_rejected_by_default_before_password(ftps_server):
    server, _certfile = ftps_server
    p = FTPPath(
        "/a.txt",
        host="127.0.0.1",
        port=server.port,
        user="alice",
        password="s3cret",
        tls=True,
        timeout=5,
    )
    with pytest.raises(ssl.SSLCertVerificationError):
        p.read_bytes()
    assert server.commands == ["AUTH TLS"]
    assert not any(cmd.startswith(("USER", "PASS")) for cmd in server.commands)


def test_ftps_verify_false_opt_out_accepts(ftps_server):
    server, _certfile = ftps_server
    p = FTPPath(
        "/a.txt",
        host="127.0.0.1",
        port=server.port,
        user="alice",
        password="s3cret",
        tls=True,
        timeout=5,
        verify=False,
    )
    assert p.read_bytes() == b"hello"
    assert "PASS s3cret" in server.commands
    assert "PROT P" in server.commands


def test_ftps_custom_ssl_context_verifies_against_its_ca(ftps_server):
    server, certfile = ftps_server
    context = ssl.create_default_context(cafile=certfile)
    p = FTPPath(
        "/a.txt",
        host="127.0.0.1",
        port=server.port,
        user="alice",
        password="s3cret",
        tls=True,
        timeout=5,
        ssl_context=context,
    )
    assert p.read_bytes() == b"hello"
    assert server.data_session_reused == [True]


def test_ftps_default_context_verifies():
    context = FTPBackend("host", tls=True)._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_ftps_data_connection_reuses_tls_session(ftps_server):
    server, _certfile = ftps_server
    p = FTPPath(
        "/a.txt",
        host="127.0.0.1",
        port=server.port,
        user="alice",
        password="s3cret",
        tls=True,
        timeout=5,
        verify=False,
    )
    assert p.read_bytes() == b"hello"
    assert p.read_bytes() == b"hello"
    assert server.data_session_reused == [True, True]
