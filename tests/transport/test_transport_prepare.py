"""What a transport reads from the arguments of `send()` before it sends, for a
transport that is not the built-in one: `prepare_request()` and the response it
builds."""

from __future__ import annotations

import io

import pytest

from pathapi.http import HTTPResponse, PreparedRequest, prepare_request


def prepare(method="GET", url="http://h/x", **arguments):
    arguments.setdefault("headers", {})
    arguments.setdefault("timeout", (5, 5))
    return prepare_request(method, url, **arguments)


def test_the_method_is_upper_case_and_the_fragment_is_dropped():
    request = prepare("put", "http://h/a/b?x=1#frag")
    assert (request.method, request.url) == ("PUT", "http://h/a/b?x=1")


def test_the_result_is_a_named_tuple_of_what_to_send():
    request = prepare(body=b"abc", headers={"X-A": "1"}, timeout=3)
    assert isinstance(request, PreparedRequest)
    method, url, headers, body, timeout, decode = request
    assert (method, url, body, timeout, decode) == (
        "GET",
        "http://h/x",
        b"abc",
        (3, 3),
        True,
    )
    assert headers["X-A"] == "1"


@pytest.mark.parametrize("timeout", [None, 4, (1, 2), (None, 7)])
def test_the_timeout_is_a_connect_and_read_pair(timeout):
    expected = {None: (None, None), 4: (4, 4), (1, 2): (1, 2), (None, 7): (None, 7)}
    assert prepare(timeout=timeout).timeout == expected[timeout]


@pytest.mark.parametrize("timeout", [0, -1, "5", (1, 2, 3), True, (5, -1)])
def test_a_timeout_that_is_not_a_positive_number_is_refused(timeout):
    with pytest.raises(ValueError):
        prepare(timeout=timeout)


def test_gzip_and_deflate_are_asked_for_unless_the_caller_names_a_coding():
    assert prepare().headers["Accept-Encoding"] == "gzip, deflate"
    chosen = prepare(headers={"accept-encoding": "br"})
    assert chosen.headers == {"accept-encoding": "br"}
    assert chosen.decode is False


def test_identity_is_not_a_coding_of_its_own():
    request = prepare(headers={"Accept-Encoding": "identity"})
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.decode is True


def test_a_body_of_known_length_is_sent_under_its_length():
    assert prepare("PUT", body=b"payload").headers["Content-Length"] == "7"
    assert prepare("PUT", body=[b"ab", b"cde"]).headers["Content-Length"] == "5"
    assert prepare("PUT", body=io.BytesIO(b"from a file")).headers[
        "Content-Length"
    ] == str(len(b"from a file"))


@pytest.mark.parametrize("method", ["PUT", "POST", "PATCH"])
def test_a_request_that_expects_a_body_and_has_none_says_so(method):
    request = prepare(method)
    assert request.headers["Content-Length"] == "0"
    assert request.body is None


@pytest.mark.parametrize("method", ["GET", "HEAD", "DELETE", "PROPFIND"])
def test_a_request_that_does_not_expect_a_body_sends_no_length(method):
    assert "Content-Length" not in prepare(method).headers


def test_an_empty_body_is_sent_under_a_length_of_zero():
    assert prepare("PUT", body=b"").headers["Content-Length"] == "0"


def test_a_body_that_has_no_known_length_is_left_to_be_chunked():
    pieces = (piece for piece in (b"in ", b"pieces"))
    request = prepare("PUT", body=pieces)
    assert "Content-Length" not in request.headers
    assert b"".join(request.body) == b"in pieces"


def test_a_file_sends_exactly_the_length_it_was_measured_at():
    source = io.BytesIO(b"0123456789")
    request = prepare("PUT", body=source)
    source.seek(0, 2)
    source.write(b"grown after the measurement")
    source.seek(0)
    assert request.headers["Content-Length"] == "10"
    assert b"".join(request.body) == b"0123456789"


def test_a_length_the_caller_set_is_trusted_and_a_longer_body_is_cut():
    request = prepare(
        "PUT", body=io.BytesIO(b"0123456789"), headers={"Content-Length": "4"}
    )
    assert request.headers["Content-Length"] == "4"
    assert b"".join(request.body) == b"0123"


@pytest.mark.parametrize("length", ["11", "ten", "-1", "1, 2"])
def test_a_length_that_is_not_a_count_of_the_body_is_refused(length):
    with pytest.raises(ValueError):
        prepare("PUT", body=b"0123456789", headers={"Content-Length": length})


def test_a_body_that_is_text_is_refused():
    with pytest.raises(TypeError):
        prepare("PUT", body="text")


@pytest.mark.parametrize(
    "headers",
    [{"X-A": "line\r\nbreak"}, {"X A": "v"}, {"X-A": "\x00"}, {"X-A": "☃"}],
)
def test_a_header_that_cannot_be_sent_is_refused_and_not_quoted(headers):
    with pytest.raises(ValueError) as refused:
        prepare(headers=headers)
    for text in headers.values():
        assert text not in str(refused.value) or text == "v"


@pytest.mark.parametrize(
    "url",
    [
        "http://h/\r\nx",
        "http://user:secret@h/x",
        "ftp://h/x",
        "http:///x",
        "/relative",
        "http://h:notaport/x",
    ],
)
def test_a_url_that_is_not_an_http_url_without_credentials_is_refused(url):
    with pytest.raises(ValueError) as refused:
        prepare(url=url)
    assert "secret" not in str(refused.value)


@pytest.mark.parametrize("method", ["", "GE T", "GET\r\n", None])
def test_a_method_that_is_not_a_token_is_refused(method):
    with pytest.raises(ValueError):
        prepare(method=method)


# --- the response ----------------------------------------------------------


class _Source:
    def __init__(self, data):
        self._data = io.BytesIO(data)
        self.closed = False

    def read(self, amount):
        return self._data.read(amount)

    def close(self):
        self.closed = True


def test_buffered_reads_the_body_closes_the_source_and_returns_the_response():
    source = _Source(b"the whole body")
    response = HTTPResponse(200, "OK", {}, "http://h/x", source)
    assert response.buffered() is response
    assert source.closed
    assert response.read() == b"the whole body"


def test_a_buffered_response_is_readable_after_it_is_closed():
    response = HTTPResponse(200, "OK", {}, "http://h/x", _Source(b"abc")).buffered()
    response.close()
    assert response.read() == b"abc"


def test_buffered_decodes_a_coded_body():
    import gzip

    response = HTTPResponse(
        200,
        "OK",
        {"Content-Encoding": "gzip"},
        "http://h/x",
        _Source(gzip.compress(b"decoded")),
    ).buffered()
    assert response.read() == b"decoded"


def test_a_body_that_ends_early_is_an_error_when_it_is_buffered():
    class Cut(_Source):
        def read(self, amount):
            raise OSError(5, "cut short")

    response = HTTPResponse(200, "OK", {}, "http://h/x", Cut(b""))
    with pytest.raises(OSError):
        response.buffered()
    assert response._source.closed
