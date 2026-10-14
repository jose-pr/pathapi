"""The client: which transport it sends with when it is not given one (the
built-in one, the `requests` one only when asked, and nothing imported that is
not needed), what it closes, and the headers and timeout every request gets."""

import base64
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

import pathapi
import pathapi.http as http
from pathapi.http import HTTPClient, HTTPTransport
from transport_fakes import FakeTransport, reply
from transport_wire import Hang, Wire

_SRC = pathlib.Path(pathapi.__file__).resolve().parent.parent
VARIABLE = "PATHAPI_HTTP_TRANSPORT"


def test_the_variable_in_the_module_is_the_one_the_code_reads():
    assert http.TRANSPORT_ENV == VARIABLE


def test_without_a_setting_the_built_in_transport_is_used(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with HTTPClient() as client:
        assert type(client.transport) is HTTPTransport


def test_a_client_builds_its_transport_on_first_use_and_keeps_it(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with HTTPClient() as client:
        assert client._transport is None
        first = client.transport
        assert client.transport is first


def test_the_variable_chooses_the_transport(monkeypatch):
    monkeypatch.setenv(VARIABLE, "builtin")
    with HTTPClient() as client:
        assert type(client.transport) is HTTPTransport


def test_the_uripath_variable_does_not_choose_the_transport(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    monkeypatch.setenv("URIPATH_HTTP_TRANSPORT", "requests")
    with HTTPClient() as client:
        assert type(client.transport) is HTTPTransport


@pytest.mark.parametrize("value", ["", "Requests", "BUILTIN", "urllib3", " builtin"])
def test_a_value_that_is_not_a_choice_is_a_value_error_naming_the_choices(
    monkeypatch, value
):
    monkeypatch.setenv(VARIABLE, value)
    client = HTTPClient()
    with pytest.raises(ValueError) as excinfo:
        client.transport
    message = str(excinfo.value)
    assert VARIABLE in message and "'builtin'" in message and "'requests'" in message


def test_a_bad_setting_is_reported_when_the_client_sends_not_when_it_is_built(
    monkeypatch,
):
    monkeypatch.setenv(VARIABLE, "nonsense")
    client = HTTPClient()
    with pytest.raises(ValueError):
        client.request("GET", "http://127.0.0.1:1/x")


def test_a_client_closes_the_transport_it_built_and_not_one_it_was_given(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    given = FakeTransport(default=reply(200))
    client = HTTPClient(given)
    client.close()
    assert given.closed == 0

    built = HTTPClient()
    transport = built.transport
    closed = []
    transport.close = lambda: closed.append(True)
    built.close()
    built.close()
    assert closed == [True]


def test_a_transport_is_an_object_with_a_send_method():
    with pytest.raises(TypeError):
        HTTPClient(object())


def test_the_representation_of_a_client_shows_no_header():
    client = HTTPClient(headers={"Authorization": "Bearer SECRET"})
    assert "SECRET" not in repr(client) and "Authorization" not in repr(client)


# --- what every request carries -------------------------------------------------


def test_a_request_names_the_library_and_its_version_as_its_user_agent():
    transport = FakeTransport(default=reply(200))
    HTTPClient(transport).request("GET", "http://h/x")
    sent = transport.sent[0].headers
    assert sent["User-Agent"] == f"pathapi/{pathapi.__version__}"
    assert sent["Accept"] == "*/*"


def test_the_user_agent_can_be_named_by_the_caller_of_the_client():
    transport = FakeTransport(default=reply(200))
    client = HTTPClient(transport, user_agent="tool/1.2")
    assert client.user_agent == "tool/1.2"
    client.request("GET", "http://h/x")
    assert transport.sent[0].headers["User-Agent"] == "tool/1.2"


def test_headers_are_merged_by_name_the_call_over_the_client_over_the_base():
    transport = FakeTransport(default=reply(200))
    client = HTTPClient(transport, headers={"accept": "text/html", "X-A": "client"})
    client.request("GET", "http://h/x", headers={"x-a": "call", "X-B": "call"})
    sent = transport.sent[0].headers
    assert {k.lower(): v for k, v in sent.items()} == {
        "user-agent": f"pathapi/{pathapi.__version__}",
        "accept": "text/html",
        "x-a": "call",
        "x-b": "call",
    }
    assert len(sent) == 4


def test_a_header_set_to_none_in_the_call_is_not_sent():
    transport = FakeTransport(default=reply(200))
    HTTPClient(transport).request("GET", "http://h/x", headers={"Accept": None})
    assert "Accept" not in transport.sent[0].headers


def test_credentials_given_as_auth_are_sent_as_a_basic_authorization():
    transport = FakeTransport(default=reply(200))
    HTTPClient(transport).request("GET", "http://h/x", auth=("al@ice", "s3cr3t"))
    expected = "Basic " + base64.b64encode(b"al@ice:s3cr3t").decode("ascii")
    assert transport.sent[0].headers["Authorization"] == expected
    assert transport.sent[0].url == "http://h/x"


def test_a_string_given_as_auth_is_the_authorization_value():
    transport = FakeTransport(default=reply(200))
    HTTPClient(transport).request("GET", "http://h/x", auth="Bearer TOKEN")
    assert transport.sent[0].headers["Authorization"] == "Bearer TOKEN"


@pytest.mark.parametrize("where", ["client", "call"])
def test_an_authorization_header_wins_over_auth(where):
    transport = FakeTransport(default=reply(200))
    options = {"headers": {"authorization": "Bearer B"}}
    client = HTTPClient(transport, **(options if where == "client" else {}))
    client.request(
        "GET",
        "http://h/x",
        auth=("alice", "pw"),
        **(options if where == "call" else {}),
    )
    sent = transport.sent[0].headers
    assert [v for k, v in sent.items() if k.lower() == "authorization"] == ["Bearer B"]


def test_a_url_with_credentials_is_refused_before_anything_is_sent():
    transport = FakeTransport(default=reply(200))
    with pytest.raises(ValueError) as excinfo:
        HTTPClient(transport).request("GET", "http://alice:s3cr3t@h/x")
    assert "s3cr3t" not in str(excinfo.value)
    assert transport.sent == []


def test_a_header_or_url_with_a_line_break_is_refused_before_any_transport_call():
    transport = FakeTransport(default=reply(200))
    for headers in ({"X-A": "1\r\nX-B: 2"}, {"X-A": "1\n2"}):
        with pytest.raises(ValueError):
            HTTPClient(transport, headers=headers).request("GET", "http://h/x")
        with pytest.raises(ValueError):
            HTTPClient(transport).request("GET", "http://h/x", headers=headers)
    with pytest.raises(ValueError):
        HTTPClient(transport).request("GET", "http://h/x\r\nX-A: 1")
    assert transport.sent == []


def test_a_text_body_is_sent_as_utf_8():
    transport = FakeTransport(default=reply(200))
    HTTPClient(transport).request("PUT", "http://h/x", data="café")
    assert transport.sent[0].body == "café".encode("utf-8")


def test_a_reply_is_asked_for_streamed_whatever_the_caller_asked():
    transport = FakeTransport(default=reply(200, b"abc"))
    client = HTTPClient(transport)
    assert client.request("GET", "http://h/x").read() == b"abc"
    client.request("GET", "http://h/x", stream=True).close()
    assert [call.stream for call in transport.sent] == [True, True]


# --- the timeout -----------------------------------------------------------------


def test_a_request_without_a_timeout_gets_the_default():
    transport = FakeTransport()
    HTTPClient(transport).request("GET", "http://h/x")
    assert transport.sent[-1].timeout == (10, 60) == http.DEFAULT_TIMEOUT


@pytest.mark.parametrize("timeout", [5, None])
def test_a_timeout_of_the_client_or_the_call_wins(timeout):
    transport = FakeTransport()
    HTTPClient(transport, timeout=timeout).request("GET", "http://h/x")
    assert transport.sent[-1].timeout == timeout
    HTTPClient(transport).request("GET", "http://h/x", timeout=timeout)
    assert transport.sent[-1].timeout == timeout


def test_a_stalled_server_times_out_when_the_client_has_a_timeout(http_transport_kind):
    with Wire(lambda r: Hang()) as wire:
        with HTTPClient(timeout=(2, 0.5)) as client:
            with pytest.raises(TimeoutError):
                client.request("GET", wire.url + "/x")


# --- no third-party library ---------------------------------------------------

_CHILD = """
import sys

import pathapi

assert not {"ssl", "http.client"} & set(sys.modules)
sys.modules["requests"] = None
sys.modules["urllib3"] = None

import http.server, threading


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

from pathapi.http import HTTPClient

with HTTPClient() as client:
    reply = client.request("GET", "http://127.0.0.1:%d/f" % server.server_port)
    print(reply.status, reply.read())
print(sorted(m for m in ("requests", "urllib3") if sys.modules.get(m) is not None))
server.shutdown()
server.server_close()
"""


def test_a_request_works_with_requests_and_urllib3_hidden_and_the_root_loads_no_ssl():
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
    assert result.stdout.splitlines() == ["200 b'hello'", "[]"]
