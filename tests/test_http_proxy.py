"""Proxies for the built-in transport: from a provider or the environment,
in absolute form for `http:` and through `CONNECT` for `https:`, in order
when a connection cannot be made, and never with a credential in a message."""

import base64
import errno
import traceback

import pytest

from http_wire import Proxy, Wire, make_authority, refusing_port, response
from pathlib_next.uri.schemes.http import HTTPTransport

TIMEOUT = (5, 5)


def get(transport, url, **kwargs):
    return transport.send("GET", url, headers={}, timeout=TIMEOUT, **kwargs)


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class Provider:
    """A proxy resolver as the transport sees it: indexed with a URL,
    it returns one proxy, a sequence of them, `None`, or raises `KeyError`."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __getitem__(self, url):
        self.asked.append(url)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class Proxied:
    """An object with a `url`, as a proxy resolver's proxies have."""

    def __init__(self, url, username=None, password=None):
        self.url = url
        self.username = username
        self.password = password


@pytest.fixture
def transport_for():
    made = []

    def make(proxies=None, **kwargs):
        transport = HTTPTransport(proxies=proxies, **kwargs)
        made.append(transport)
        return transport

    yield make
    for transport in made:
        transport.close()


# --- http: through a proxy --------------------------------------------------


def test_an_http_request_goes_to_the_proxy_in_absolute_form(transport_for):
    # `backend.test` is never resolved: the proxy is the only host connected to.
    with Proxy() as proxy:
        transport = transport_for(Provider(proxy.url))
        reply = get(transport, "http://backend.test/dir/f?x=1#frag")
        assert reply.read() == b"via proxy"
    (request,) = proxy.requests
    assert request.target == "http://backend.test/dir/f?x=1"
    assert request.header("Host") == "backend.test"


def test_the_provider_is_asked_with_the_request_url(transport_for):
    provider = Provider(None)
    with Wire() as wire:
        get(transport_for(provider), wire.url + "/a b?q=1#frag").read()
    assert provider.asked == [f"http://127.0.0.1:{wire.port}/a%20b?q=1"]


def test_proxy_credentials_become_a_proxy_authorization_header(transport_for):
    with Proxy() as proxy:
        url = f"http://us%40er:p%3Aw@127.0.0.1:{proxy.port}"
        get(transport_for(Provider(url)), "http://backend.test/f").read()
    assert proxy.requests[0].header("Proxy-Authorization") == basic("us@er", "p:w")


def test_credentials_of_a_proxy_object_without_them_in_its_url_are_used(transport_for):
    with Proxy() as proxy:
        found = Proxied(proxy.url, username="alice", password="s3cret")
        get(transport_for(Provider(found)), "http://backend.test/f").read()
    assert proxy.requests[0].header("Proxy-Authorization") == basic("alice", "s3cret")


def test_the_proxy_credentials_do_not_go_to_a_server_reached_directly(transport_for):
    with Wire() as wire:
        get(transport_for(Provider(None)), wire.url + "/").read()
    assert wire.requests[0].header("Proxy-Authorization") is None


def test_a_proxy_authorization_the_request_sets_is_kept(transport_for):
    with Proxy() as proxy:
        transport = transport_for(Provider(f"http://u:p@127.0.0.1:{proxy.port}"))
        transport.send(
            "GET",
            "http://backend.test/f",
            headers={"Proxy-Authorization": "Bearer mine"},
            timeout=TIMEOUT,
        ).read()
    assert proxy.requests[0].header("Proxy-Authorization") == "Bearer mine"


def test_a_plain_dict_may_use_the_keys_requests_uses(transport_for):
    with Proxy() as proxy:
        transport = transport_for({"http": proxy.url})
        get(transport, "http://backend.test/f").read()
    assert len(proxy.requests) == 1


def test_a_dict_that_names_no_proxy_for_the_url_sends_it_direct(transport_for):
    with Wire() as wire:
        get(transport_for({"https": "http://127.0.0.1:1"}), wire.url + "/").read()
    assert len(wire.requests) == 1


def test_a_proxy_url_without_a_scheme_is_an_http_proxy(transport_for):
    with Proxy() as proxy:
        get(
            transport_for(Provider(f"127.0.0.1:{proxy.port}")), "http://backend.test/f"
        ).read()
    assert len(proxy.requests) == 1


# --- https: through CONNECT ----------------------------------------------------


@pytest.fixture
def secure_origin(tmp_path):
    authority = make_authority(tmp_path, "backend.test")
    with Wire(
        lambda r: response(body=b"secure"), tls=authority.server_context()
    ) as wire:
        yield authority, wire


def test_an_https_request_is_tunnelled_with_connect(transport_for, secure_origin):
    authority, origin = secure_origin
    with Proxy(upstream=("127.0.0.1", origin.port)) as proxy:
        transport = transport_for(
            Provider(f"http://carol:PROXYSECRET@127.0.0.1:{proxy.port}"),
            ssl_context=authority.client_context(),
        )
        assert get(transport, "https://backend.test/secret?a=b").read() == b"secure"
    (connect,) = proxy.requests
    assert (connect.method, connect.target) == ("CONNECT", "backend.test:443")
    assert connect.header("Proxy-Authorization") == basic("carol", "PROXYSECRET")
    (request,) = origin.requests
    assert (request.method, request.target) == ("GET", "/secret?a=b")
    assert request.header("Host") == "backend.test"
    assert request.header("Proxy-Authorization") is None


def test_the_tunnelled_server_is_verified_for_the_name_asked_for(
    transport_for, secure_origin
):
    authority, origin = secure_origin
    with Proxy(upstream=("127.0.0.1", origin.port)) as proxy:
        # The certificate names backend.test, not other.test.
        transport = transport_for(
            Provider(proxy.url), ssl_context=authority.client_context()
        )
        with pytest.raises(Exception) as excinfo:
            get(transport, "https://other.test/")
    import ssl

    assert isinstance(excinfo.value, ssl.SSLCertVerificationError)
    assert origin.requests == []


def test_a_proxy_that_refuses_connect_is_a_connection_error(transport_for):
    with Proxy(refuse=407) as proxy:
        transport = transport_for(
            Provider(f"http://carol:PROXYSECRET@127.0.0.1:{proxy.port}")
        )
        with pytest.raises(ConnectionError) as excinfo:
            get(transport, "https://backend.test/")
    assert "407" in str(excinfo.value)
    text = "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.tb)
    )
    assert "PROXYSECRET" not in text and "carol" not in text


def test_a_refused_tunnel_does_not_try_the_next_proxy(transport_for):
    with Proxy(refuse=403) as refusing, Proxy() as second:
        transport = transport_for(Provider([refusing.url, second.url]))
        with pytest.raises(ConnectionError):
            get(transport, "https://backend.test/")
    assert second.requests == []


# --- an order of proxies -----------------------------------------------------


def test_proxies_are_tried_in_order_when_the_first_cannot_be_reached(transport_for):
    dead = f"http://127.0.0.1:{refusing_port()}"
    with Proxy() as proxy:
        provider = Provider([dead, proxy.url])
        assert (
            get(transport_for(provider), "http://backend.test/f").read() == b"via proxy"
        )
    assert len(proxy.requests) == 1


def test_a_later_proxy_is_not_contacted_once_one_has_answered(transport_for):
    with Proxy() as first, Proxy() as second:
        get(
            transport_for(Provider([first.url, second.url])), "http://backend.test/f"
        ).read()
    assert (len(first.requests), len(second.requests)) == (1, 0)


def test_none_in_the_order_means_a_direct_connection(transport_for):
    dead = f"http://127.0.0.1:{refusing_port()}"
    with Wire() as wire:
        reply = get(transport_for(Provider([dead, None])), wire.url + "/")
        assert reply.read() == b"ok"
    assert len(wire.requests) == 1


def test_when_every_proxy_fails_the_last_error_is_raised(transport_for):
    dead = [f"http://127.0.0.1:{refusing_port()}" for _ in range(2)]
    with pytest.raises(ConnectionError):
        get(transport_for(Provider(dead)), "http://backend.test/f")


def test_an_empty_answer_means_no_proxy(transport_for):
    with Wire() as wire:
        get(transport_for(Provider([])), wire.url + "/").read()
    assert len(wire.requests) == 1


# --- no opinion, and the environment -------------------------------------------


def test_a_keyerror_from_the_provider_means_a_direct_connection(transport_for):
    with Wire() as wire:
        get(transport_for(Provider(KeyError("no opinion"))), wire.url + "/").read()
    assert len(wire.requests) == 1


def test_an_empty_mapping_forces_direct_and_the_environment_is_not_read(
    transport_for, monkeypatch
):
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{refusing_port()}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with Wire() as wire:
        get(transport_for({}), wire.url + "/").read()
    assert len(wire.requests) == 1


def test_without_a_provider_the_environment_chooses_the_proxy(
    transport_for, monkeypatch
):
    with Proxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        assert get(transport_for(), "http://backend.test/f").read() == b"via proxy"
    assert proxy.requests[0].target == "http://backend.test/f"


def test_no_proxy_in_the_environment_sends_a_host_direct(transport_for, monkeypatch):
    with Proxy() as proxy, Wire() as wire:
        monkeypatch.setenv("HTTP_PROXY", proxy.url)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        get(transport_for(), wire.url + "/").read()
    assert proxy.requests == [] and len(wire.requests) == 1


def test_all_proxy_in_the_environment_serves_a_scheme_with_none_of_its_own(
    transport_for, monkeypatch
):
    with Proxy() as proxy:
        monkeypatch.setenv("ALL_PROXY", proxy.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        get(transport_for(), "http://backend.test/f").read()
    assert len(proxy.requests) == 1


# --- what is not supported, and what is never shown ------------------------------


@pytest.mark.parametrize(
    "url, scheme",
    [
        ("socks5://carol:PROXYSECRET@127.0.0.1:1080", "socks5"),
        ("https://carol:PROXYSECRET@127.0.0.1:3128", "https"),
        ("socks4://127.0.0.1:1080", "socks4"),
    ],
)
def test_a_proxy_scheme_that_is_not_http_is_refused_naming_the_other_transport(
    transport_for, url, scheme
):
    with pytest.raises(OSError) as excinfo:
        get(transport_for(Provider(url)), "http://backend.test/f")
    assert excinfo.value.errno == errno.EPROTONOSUPPORT
    message = str(excinfo.value)
    assert scheme in message and "RequestsTransport" in message
    assert "PROXYSECRET" not in message and "carol" not in message


def test_proxy_credentials_are_in_no_error_of_an_unreachable_proxy(transport_for):
    url = f"http://carol:PROXYSECRET@127.0.0.1:{refusing_port()}"
    with pytest.raises(ConnectionError) as excinfo:
        get(transport_for(Provider(url)), "http://backend.test/f")
    text = "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.tb)
    )
    assert "PROXYSECRET" not in text and "carol" not in text
    assert excinfo.value.__cause__ is None


def test_proxy_credentials_are_in_no_repr(transport_for):
    transport = transport_for(Provider("http://carol:PROXYSECRET@127.0.0.1:1"))
    assert "PROXYSECRET" not in repr(transport) + str(transport)
    from pathlib_next.uri.schemes._http._proxies import ProxyAddress, _address

    address = _address("http://carol:PROXYSECRET@proxy.example:3128")
    assert isinstance(address, ProxyAddress)
    shown = repr(address) + str(address)
    # The password, and the Proxy-Authorization value that encodes it.
    assert "PROXYSECRET" not in shown and basic("carol", "PROXYSECRET") not in shown
    assert "Basic" not in shown


@pytest.mark.parametrize("bad", ["http://", "", 42])
def test_a_proxy_that_is_not_a_usable_url_is_a_value_error(transport_for, bad):
    with pytest.raises(ValueError):
        get(transport_for(Provider(bad)), "http://backend.test/f")
