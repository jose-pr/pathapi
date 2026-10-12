"""`parsedate`: HTTP and WebDAV dates as UTC epoch seconds."""

import calendar
import time

import pytest

from pathlib_next.uri.schemes._httpdate import parsedate


def test_parsedate_none_returns_epoch_zero():
    # Epoch 0, never "now": a response with no Last-Modified header must not
    # read as just modified in a freshness check.
    assert parsedate(None) == 0


def test_parsedate_unparseable_string_returns_epoch_zero():
    assert parsedate("not a date") == 0


def test_parsedate_valid_string():
    # Exact UTC epoch seconds. Expected values come from calendar.timegm
    # (UTC by definition), so the machine's timezone cannot matter.
    assert parsedate("Thu, 01 Jan 1970 00:00:00 GMT") == 0


_OCT_21_2015_0728_UTC = calendar.timegm((2015, 10, 21, 7, 28, 0))


@pytest.mark.parametrize(
    "text",
    [
        "Wed, 21 Oct 2015 07:28:00 GMT",  # RFC 1123 (Last-Modified)
        "Wednesday, 21-Oct-15 07:28:00 GMT",  # RFC 850
        "Wed Oct 21 07:28:00 2015",  # asctime: no zone, GMT per HTTP
        "Wed, 21 Oct 2015 09:28:00 +0200",  # offset east of UTC
        "Wed, 21 Oct 2015 02:28:00 -0500",  # offset west of UTC
    ],
)
def test_parsedate_string_is_utc_epoch_seconds(text):
    assert _OCT_21_2015_0728_UTC == 1445412480
    assert parsedate(text) == _OCT_21_2015_0728_UTC


def test_parsedate_numbers_pass_through():
    assert parsedate(1445412480) == 1445412480
    assert parsedate(1445412480.5) == 1445412480.5
    assert parsedate(0) == 0


def test_parsedate_struct_time_without_zone_is_utc():
    zoneless = time.strptime("2015-10-21 07:28:00", "%Y-%m-%d %H:%M:%S")
    assert parsedate(zoneless) == _OCT_21_2015_0728_UTC
    assert parsedate((2015, 10, 21, 7, 28, 0, 0, 0, 0)) == 1445412480
    # Near the epoch, where a local-time conversion overflows on Windows east
    # of UTC.
    assert parsedate(time.gmtime(0)) == 0


def test_parsedate_tuple_with_offset_applies_it():
    # A parsedate_tz-style 10-tuple carries its offset in seconds.
    assert parsedate((2015, 10, 21, 9, 28, 0, 0, 1, -1, 7200)) == 1445412480


def test_parsedate_states_what_it_accepts_and_what_it_does_not():
    text = " ".join((parsedate.__doc__ or "").split())
    assert "Nothing else is accepted" in text
    for word in ("bytes", "datetime", "fewer than six"):
        assert word in text
    assert "31 Feb" in text
