"""Which transport a backend sends with when it is not given one: the built-in
one, `requests` only when asked, and nothing imported that is not needed."""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

import pathlib_next
from http_fakes import FakeTransport, reply
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes._http import _select
from pathlib_next.uri.schemes.http import (
    HttpBackend,
    HTTPTransport,
    RequestsTransport,
)

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
VARIABLE = "PATHLIB_NEXT_HTTP_TRANSPORT"


def test_without_a_setting_the_built_in_transport_is_used(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with HttpBackend() as backend:
        assert type(backend.transport) is HTTPTransport
    assert type(UriPath("http://h/x").backend) is not None


def test_a_backend_builds_its_transport_on_first_use_and_keeps_it(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with HttpBackend() as backend:
        assert backend._transport is None
        first = backend.transport
        assert backend.transport is first


def test_the_variable_chooses_the_transport(monkeypatch):
    pytest.importorskip("requests")
    monkeypatch.setenv(VARIABLE, "requests")
    with HttpBackend() as backend:
        assert type(backend.transport) is RequestsTransport
    monkeypatch.setenv(VARIABLE, "builtin")
    with HttpBackend() as backend:
        assert type(backend.transport) is HTTPTransport


@pytest.mark.parametrize("value", ["", "Requests", "BUILTIN", "urllib3", " builtin"])
def test_a_value_that_is_not_a_choice_is_a_value_error_naming_the_choices(
    monkeypatch, value
):
    monkeypatch.setenv(VARIABLE, value)
    backend = HttpBackend()
    with pytest.raises(ValueError) as excinfo:
        backend.transport
    message = str(excinfo.value)
    assert VARIABLE in message and "'builtin'" in message and "'requests'" in message


def test_a_path_reports_a_bad_setting_when_it_sends_not_when_it_is_built(monkeypatch):
    monkeypatch.setenv(VARIABLE, "nonsense")
    path = UriPath("http://127.0.0.1:1/x")
    assert path.name == "x"
    with pytest.raises(ValueError):
        path.stat()


def test_the_variable_in_the_header_is_the_one_the_code_reads():
    header = (pathlib.Path(pathlib_next.__file__).parent / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    section = header.split("\n## Environment variables\n")[1]
    assert _select.TRANSPORT_ENV == VARIABLE
    assert f"### `{VARIABLE}`" in section


def test_a_backend_closes_the_transport_it_built_and_not_one_it_was_given(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    given = FakeTransport(default=reply(200))
    backend = HttpBackend(given)
    backend.close()
    assert given.closed == 0

    built = HttpBackend()
    transport = built.transport
    closed = []
    transport.close = lambda: closed.append(True)
    built.close()
    built.close()
    assert closed == [True]


def test_a_transport_is_an_object_with_a_send_method():
    with pytest.raises(TypeError):
        HttpBackend(object())


# --- no third-party library ---------------------------------------------------

_CHILD = """
import http.server, sys, threading

sys.modules["requests"] = None
sys.modules["urllib3"] = None

MULTISTATUS = (
    '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response>'
    "<D:href>/f.txt</D:href><D:propstat><D:prop><D:resourcetype/>"
    "<D:getcontentlength>5</D:getcontentlength></D:prop>"
    "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
    "</D:multistatus>"
).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, body, kind="application/octet-stream"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/repos/"):
            self.reply(200, b"from github")
        else:
            self.reply(200, b"hello")

    do_HEAD = do_GET

    def do_PROPFIND(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.reply(207, MULTISTATUS, "application/xml")


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
host = "127.0.0.1:%d" % server.server_port

from pathlib_next.uri import UriPath

page = UriPath("http://" + host + "/f.txt")
print(page.exists(), page.read_bytes(), page.stat().st_size)
print(UriPath("dav://" + host + "/f.txt").stat().st_size)
github = UriPath("github://github.com/o/r/f.txt")
github.backend.api_base = "http://" + host
print(github.read_bytes())
print(sorted(m for m in ("requests", "urllib3") if sys.modules.get(m) is not None))
try:
    page.with_session(None)
except ImportError as error:
    print(error)
server.shutdown()
server.server_close()
"""


def test_http_dav_and_git_work_with_requests_and_urllib3_hidden():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(_SRC), env.get("PYTHONPATH")])
    )
    env.pop(VARIABLE, None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_CHILD)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:4] == ["True b'hello' 5", "5", "b'from github'", "[]"]
    assert "pathlib-next[http]" in lines[4]
