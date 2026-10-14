"""The write buffer `ftp:` and the archive schemes share: its content goes to
`commit` when it is closed, once, and only if it was written to."""

import gc
import io
import warnings

import pytest

from pathapi.protocols.io import BinaryOpen
from pathapi.providers import CommitOnClose, DiscardOnError


def _recording():
    seen = []
    return seen, lambda buffer: seen.append(buffer.getvalue())


def test_a_new_file_is_committed_at_close_even_when_nothing_was_written():
    seen, commit = _recording()
    stream = CommitOnClose(commit)
    assert seen == []
    stream.close()
    stream.close()
    assert seen == [b""]


def test_what_was_written_is_committed_once():
    seen, commit = _recording()
    with CommitOnClose(commit) as stream:
        stream.write(b"ab")
        stream.writelines([b"c", b"d"])
    assert seen == [b"abcd"]


def test_a_file_opened_with_content_is_committed_only_if_it_was_modified():
    seen, commit = _recording()
    CommitOnClose(commit, initial=b"keep").close()
    assert seen == []
    stream = CommitOnClose(commit, initial=b"keep")
    assert stream.read() == b"keep"
    stream.truncate(2)
    stream.close()
    assert seen == [b"ke"]


def test_the_commit_reads_the_buffer_before_it_is_closed():
    states = []

    def commit(buffer):
        states.append(buffer.closed)
        buffer.seek(0)
        states.append(buffer.read())

    with CommitOnClose(commit) as stream:
        stream.write(b"xyz")
    assert states == [False, b"xyz"]


def test_a_failed_commit_is_not_tried_again_at_garbage_collection():
    calls = []

    def commit(buffer):
        calls.append(1)
        raise OSError("refused")

    stream = CommitOnClose(commit)
    stream.write(b"x")
    with pytest.raises(OSError):
        stream.close()
    assert stream.closed
    del stream
    gc.collect()
    assert calls == [1]


def _collect(make):
    """The ResourceWarnings raised by letting go of the unclosed stream that
    `make()` returns."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        make()
        gc.collect()
    return [w for w in caught if issubclass(w.category, ResourceWarning)]


def _written(commit, data=b"unfinished", **kw):
    stream = CommitOnClose(commit, **kw)
    stream.write(data)
    return stream


def test_an_unclosed_written_buffer_is_discarded_at_collection_with_a_warning():
    seen, commit = _recording()
    caught = _collect(lambda: _written(commit))
    assert seen == []
    assert len(caught) == 1
    assert "CommitOnClose" in str(caught[0].message)


def test_an_unclosed_new_file_is_not_created_at_collection():
    seen, commit = _recording()
    caught = _collect(lambda: CommitOnClose(commit))
    assert seen == []
    assert len(caught) == 1


def test_an_unclosed_buffer_that_was_not_modified_is_collected_silently():
    seen, commit = _recording()
    assert _collect(lambda: CommitOnClose(commit, initial=b"keep")) == []
    assert seen == []


def test_a_closed_buffer_commits_and_collects_without_a_warning():
    seen, commit = _recording()

    def make():
        stream = _written(commit, b"done")
        stream.close()
        return stream

    assert _collect(make) == []
    assert seen == [b"done"]


def test_a_discarded_buffer_is_collected_silently():
    seen, commit = _recording()

    def make():
        stream = _written(commit, b"dropped")
        stream.discard()
        return stream

    assert _collect(make) == []
    assert seen == []


def test_an_unclosed_text_file_over_a_buffer_is_discarded_at_collection():
    seen, commit = _recording()
    opener = type(
        "Opener",
        (BinaryOpen,),
        {"_open": lambda self, mode, buffering=-1: CommitOnClose(commit)},
    )()

    def make():
        text = opener.open("w", encoding="utf-8")
        text.write("unfinished")
        return text

    caught = _collect(make)
    assert seen == []
    assert len(caught) >= 1


def test_a_subclass_of_the_mixin_is_discarded_unclosed_unless_it_says_otherwise():
    log = []

    class Upload(DiscardOnError, io.BytesIO):
        def discard(self):
            log.append("discard")

    class Quiet(Upload):
        def _holds_uncommitted(self):
            return False

    assert len(_collect(Upload)) == 1
    assert log == ["discard"]
    assert _collect(Quiet) == []
