"""A write stream that commits on close is not committed when the `with` block
over it raised. `open()` tells the stream to `discard()` before it closes it,
in binary and in text mode; then each core path that commits on close
(`FTPPath` uploads on close, `ZipPath` rewrites its archive) is held to what
the server or the archive contains afterwards."""

from __future__ import annotations

import errno
import gc
import io
import pickle
import zipfile

import pytest

from ftp_loopback import LoopbackFtp

from pathapi import LocalPath, ZipPath
from pathapi.ftp import FTPPath
from pathapi.mempath import MemPath
from pathapi.protocols.io import BinaryOpen
from pathapi.providers import CommitOnClose, DiscardOnError
from pathapi.sync import PathSyncer


class Recording(io.BytesIO):
    """A write stream that commits its content when it is closed, unless it
    was told to discard it; `log` says what happened, in order. It has
    `discard()` and does not inherit `DiscardOnError`."""

    def __init__(self, log):
        super().__init__()
        self.log = log
        self.dropped = False
        self.written = []

    def write(self, data):
        self.written.append(bytes(data))
        return super().write(data)

    def discard(self):
        self.log.append("discard")
        self.dropped = True

    def close(self):
        if not self.closed:
            self.log.append("dropped" if self.dropped else ("commit", self.getvalue()))
        super().close()


class Stream(DiscardOnError, Recording):
    """`Recording` with the mixin a stream that commits on close inherits."""

    discard = Recording.discard


def opener(stream):
    """An object whose `open()` hands out `stream` from `_open()`."""
    return type(
        "Opener", (BinaryOpen,), {"_open": lambda self, mode, buffering=-1: stream}
    )()


class Boom(Exception):
    pass


# --- the hook ------------------------------------------------------------------------------


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed(
    mode, data
):
    log = []
    stream = Stream(log)
    with pytest.raises(Boom):
        with opener(stream).open(mode) as f:
            f.write(data)
            raise Boom
    assert log == ["discard", "dropped"]
    assert stream.closed


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_a_block_that_ended_normally_commits_without_a_discard(mode, data):
    log = []
    with opener(Stream(log)).open(mode) as f:
        f.write(data)
    assert log == [("commit", b"part")]


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_a_stream_closed_without_a_block_commits(mode, data):
    log = []
    f = opener(Stream(log)).open(mode)
    f.write(data)
    f.close()
    f.close()
    assert log == [("commit", b"part")]


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_any_exception_leaving_the_block_discards(mode, data, error):
    log = []
    with pytest.raises(error):
        with opener(Stream(log)).open(mode) as f:
            f.write(data)
            raise error
    assert log == ["discard", "dropped"]


def test_text_pending_in_the_wrapper_never_reaches_the_stream_of_a_failed_block():
    log = []
    stream = Stream(log)
    with pytest.raises(Boom):
        with opener(stream).open("w") as f:
            f.write("a line the wrapper has not written yet")
            raise Boom
    assert stream.written == []
    assert log == ["discard", "dropped"]


def test_a_failed_block_leaves_nothing_to_commit_when_the_wrapper_is_collected():
    log = []
    try:
        with opener(Stream(log)).open("w") as f:
            f.write("x")
            raise Boom
    except Boom:
        pass
    del f
    gc.collect()
    assert log == ["discard", "dropped"]


def test_a_binary_stream_is_handed_out_untouched():
    stream = Stream([])
    f = opener(stream).open("wb")
    assert f is stream and type(f) is Stream
    f.close()


def test_the_text_wrapper_is_a_text_io_over_the_stream_with_the_options_given():
    stream = Stream([])
    f = opener(stream).open(
        "w", buffering=1, encoding="utf-16", errors="strict", newline="\r\n"
    )
    assert isinstance(f, io.TextIOWrapper) and isinstance(f, io.TextIOBase)
    assert f.buffer is stream
    assert f.encoding == "utf-16"
    assert f.line_buffering is True
    f.write("a\nb")
    f.close()
    assert stream.log == [("commit", "a\r\nb".encode("utf-16"))]


def test_a_stream_without_discard_is_handed_out_as_before():
    plain = io.BytesIO()
    assert opener(plain).open("wb") is plain
    text = opener(io.BytesIO()).open("w")
    assert type(text) is io.TextIOWrapper


def test_a_wrapper_that_fails_to_build_discards_and_closes_the_stream():
    log = []
    stream = Stream(log)
    with pytest.raises(LookupError):
        opener(stream).open("w", encoding="no-such-codec")
    assert log == ["discard", "dropped"]
    assert stream.closed


def test_a_discard_that_fails_still_closes_the_stream_and_its_error_surfaces():
    class Failing(Stream):
        def discard(self):
            super().discard()
            raise RuntimeError("discard failed")

    for mode in ("wb", "w"):
        log = []
        stream = Failing(log)
        with pytest.raises(RuntimeError, match="discard failed") as caught:
            with opener(stream).open(mode):
                raise Boom
        assert isinstance(caught.value.__context__, Boom)
        assert log == ["discard", "dropped"]


def test_a_binary_stream_with_discard_but_without_the_mixin_is_not_discarded():
    log = []
    with pytest.raises(Boom):
        with opener(Recording(log)).open("wb") as f:
            f.write(b"part")
            raise Boom
    assert log == [("commit", b"part")]


def test_a_binary_stream_without_the_mixin_is_discarded_when_its_own_exit_does_it():
    class Own(Recording):
        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                self.discard()
            return super().__exit__(exc_type, exc_value, traceback)

    log = []
    with pytest.raises(Boom):
        with opener(Own(log)).open("wb") as f:
            f.write(b"part")
            raise Boom
    assert log == ["discard", "dropped"]


def test_a_text_file_over_a_stream_with_discard_is_discarded_without_the_mixin():
    log = []
    with pytest.raises(Boom):
        with opener(Recording(log)).open("w") as f:
            f.write("part")
            raise Boom
    assert log == ["discard", "dropped"]


def test_the_mixin_closes_the_stream_when_the_subclass_has_no_discard():
    class Bare(DiscardOnError, io.BytesIO):
        pass

    with Bare() as fine:
        fine.write(b"x")
    assert fine.closed
    with pytest.raises(NotImplementedError):
        with Bare() as stream:
            raise Boom
    assert stream.closed


def test_a_text_wrapper_does_not_pickle_with_or_without_discard():
    with pytest.raises(TypeError):
        pickle.dumps(io.TextIOWrapper(io.BytesIO()))
    with opener(Stream([])).open("w") as wrapped:
        with pytest.raises(TypeError):
            pickle.dumps(wrapped)


def test_write_bytes_and_write_text_whose_write_fails_commit_nothing():
    class Breaking(Stream):
        def write(self, data):
            raise OSError(errno.ENOSPC, "no space left")

    for call in (
        lambda path: path.write_bytes(b"data"),
        lambda path: path.write_text("data"),
    ):
        log = []
        with pytest.raises(OSError, match="no space"):
            call(opener(Breaking(log)))
        assert log == ["discard", "dropped"]


# --- CommitOnClose -------------------------------------------------------------------------


def _recording():
    seen = []
    return seen, lambda buffer: seen.append(buffer.getvalue())


def test_commit_on_close_drops_the_buffer_and_commits_nothing_after_a_discard():
    seen, commit = _recording()
    stream = CommitOnClose(commit)
    stream.write(b"abc")
    stream.discard()
    assert stream.getvalue() == b"" and stream.tell() == 0
    stream.write(b"later")
    stream.close()
    assert seen == [] and stream.closed


def test_commit_on_close_discard_is_harmless_twice_and_after_close():
    seen, commit = _recording()
    stream = CommitOnClose(commit)
    stream.write(b"abc")
    stream.close()
    assert seen == [b"abc"]
    stream.discard()
    stream.discard()
    other = CommitOnClose(commit)
    other.discard()
    other.discard()
    other.close()
    assert seen == [b"abc"]


@pytest.mark.parametrize("error", [Boom, KeyboardInterrupt, SystemExit])
def test_commit_on_close_leaving_a_block_by_an_exception_commits_nothing(error):
    seen, commit = _recording()
    with pytest.raises(error):
        with CommitOnClose(commit) as stream:
            stream.write(b"abc")
            raise error
    assert seen == [] and stream.closed


def test_commit_on_close_discard_on_a_file_opened_with_content_leaves_it_alone():
    seen, commit = _recording()
    with pytest.raises(Boom):
        with CommitOnClose(commit, initial=b"keep") as stream:
            stream.write(b"change")
            raise Boom
    assert seen == []


def test_a_commit_that_fails_after_a_normal_block_raises_and_is_not_retried():
    calls = []

    def refuse(buffer):
        calls.append(1)
        raise OSError(errno.EACCES, "refused")

    with pytest.raises(OSError, match="refused"):
        with opener(CommitOnClose(refuse)).open("wb") as f:
            f.write(b"x")
    gc.collect()
    assert calls == [1]


# --- FTPPath -------------------------------------------------------------------------------

DIRS = {"/", "/pub"}
FILE = "type=file;size=5;modify=20240101000000;"


@pytest.fixture
def server():
    with LoopbackFtp(
        files={"/pub/x": b"hello"},
        dirs=DIRS,
        entries={"/pub/x": FILE},
        listings={"/pub": f"{FILE} x\r\n".encode()},
    ) as running:
        yield running


def _ftp(server, name):
    return FTPPath(name, host="127.0.0.1", port=server.port, timeout=5)


def _sent(server, verb):
    return [arg for _, command, arg in server.log if command == verb]


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_ftp_a_failed_block_does_not_create_the_file(server, mode, data):
    path = _ftp(server, "/pub/new")
    with pytest.raises(Boom):
        with path.open(mode) as f:
            f.write(data)
            raise Boom
    assert "/pub/new" not in server.files
    assert _sent(server, "STOR") == []


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_ftp_a_failed_block_leaves_the_old_bytes(server, mode, data):
    path = _ftp(server, "/pub/x")
    with pytest.raises(Boom):
        with path.open(mode) as f:
            f.write(data)
            raise Boom
    assert server.files["/pub/x"] == b"hello"
    assert _sent(server, "STOR") == []


@pytest.mark.parametrize("mode, data", [("r+b", b"J"), ("r+", "J")])
def test_ftp_a_failed_read_modify_write_block_leaves_the_old_bytes(server, mode, data):
    path = _ftp(server, "/pub/x")
    with pytest.raises(Boom):
        with path.open(mode) as f:
            f.write(data)
            raise Boom
    assert server.files["/pub/x"] == b"hello"
    assert _sent(server, "STOR") == []


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_ftp_a_block_that_ended_normally_uploads_what_was_written(server, mode, data):
    with _ftp(server, "/pub/x").open(mode) as f:
        f.write(data)
    assert server.files["/pub/x"] == b"whole"
    assert _sent(server, "STOR") == ["/pub/x"]


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_ftp_a_stream_closed_without_a_block_uploads_what_was_written(
    server, mode, data
):
    f = _ftp(server, "/pub/closed").open(mode)
    f.write(data)
    assert _sent(server, "STOR") == []
    f.close()
    assert server.files["/pub/closed"] == b"whole"
    assert _sent(server, "STOR") == ["/pub/closed"]


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_ftp_an_upload_the_server_refuses_raises_once_and_is_not_retried(mode, data):
    def refuse(session, arg):
        if session.pasv:
            session.pasv.close()
            session.pasv = None
        session.send("553 name not allowed")
        return True

    with LoopbackFtp(dirs=DIRS, overrides={"STOR": refuse}) as refusing:
        path = _ftp(refusing, "/pub/new")
        with pytest.raises(OSError):
            with path.open(mode) as f:
                f.write(data)
        gc.collect()
        assert _sent(refusing, "STOR") == ["/pub/new"]
        assert "/pub/new" not in refusing.files


# --- copy() and PathSyncer onto a store that commits on close -----------------------------


class Failing(io.RawIOBase):
    """A source file that reads three bytes and then fails."""

    def __init__(self):
        super().__init__()
        self.sent = False

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.sent:
            raise OSError(errno.EIO, "the source failed")
        self.sent = True
        buffer[:3] = b"abc"
        return 3


class Breaking(MemPath):
    """A file whose reads fail after the first three bytes."""

    def _open(self, mode="r", buffering=-1):
        stream = super()._open(mode, buffering)
        if mode == "r":
            stream.close()
            return Failing()
        return stream


def test_a_copy_whose_source_fails_midway_uploads_nothing(server):
    source = Breaking("/src.bin")
    source.write_bytes(b"abcdef")
    target = _ftp(server, "/pub/copied")
    with pytest.raises(OSError, match="the source failed"):
        source.copy(target)
    assert "/pub/copied" not in server.files
    assert _sent(server, "STOR") == []


def test_a_copy_that_succeeds_uploads_the_file_once(server):
    source = MemPath("/src.bin")
    source.write_bytes(b"abcdef")
    source.copy(_ftp(server, "/pub/copied"))
    assert server.files["/pub/copied"] == b"abcdef"
    assert _sent(server, "STOR") == ["/pub/copied"]


def test_a_sync_whose_source_fails_midway_keeps_the_old_file_and_stores_no_temporary_one(
    server,
):
    source = Breaking("/")
    (source / "x").write_bytes(b"abcdefgh")
    target = _ftp(server, "/pub")
    errors = []
    PathSyncer(
        lambda entry: entry.stat.st_size,
        ignore_error=lambda error, *rest: errors.append(error) or True,
    ).sync(source, target)
    assert errors and "the source failed" in str(errors[0])
    assert server.files == {"/pub/x": b"hello"}
    assert _sent(server, "STOR") == []


# --- ZipPath -------------------------------------------------------------------------------


def _zip(tmp_path, **members):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for name, data in members.items():
            out.writestr(name, data)
    return archive


def _members(archive):
    with zipfile.ZipFile(archive) as out:
        return {name: out.read(name) for name in out.namelist()}


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_zip_a_failed_block_does_not_add_the_member(tmp_path, mode, data):
    archive = _zip(tmp_path, old=b"OLD")
    before = archive.read_bytes()
    with pytest.raises(Boom):
        with ZipPath("new", archive=archive).open(mode) as f:
            f.write(data)
            raise Boom
    assert archive.read_bytes() == before


@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_zip_a_failed_block_leaves_the_member_it_would_have_replaced(
    tmp_path, mode, data
):
    archive = _zip(tmp_path, old=b"OLD")
    before = archive.read_bytes()
    with pytest.raises(Boom):
        with ZipPath("old", archive=archive).open(mode) as f:
            f.write(data)
            raise Boom
    assert archive.read_bytes() == before
    assert _members(archive) == {"old": b"OLD"}


@pytest.mark.parametrize("mode, data", [("r+b", b"J"), ("r+", "J")])
def test_zip_a_failed_read_modify_write_block_leaves_the_member(tmp_path, mode, data):
    archive = _zip(tmp_path, old=b"OLD")
    before = archive.read_bytes()
    with pytest.raises(Boom):
        with ZipPath("old", archive=archive).open(mode) as f:
            f.write(data)
            raise Boom
    assert archive.read_bytes() == before


def test_zip_a_failed_block_does_not_create_the_archive(tmp_path):
    archive = tmp_path / "missing.zip"
    with pytest.raises(Boom):
        with ZipPath("new", archive=archive).open("wb") as f:
            f.write(b"part")
            raise Boom
    assert not archive.exists()


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_zip_a_block_that_ended_normally_writes_the_member(tmp_path, mode, data):
    archive = _zip(tmp_path, old=b"OLD")
    with ZipPath("new", archive=archive).open(mode) as f:
        f.write(data)
    assert _members(archive) == {"old": b"OLD", "new": b"whole"}


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_zip_a_stream_closed_without_a_block_writes_the_member(tmp_path, mode, data):
    archive = _zip(tmp_path, old=b"OLD")
    f = ZipPath("new", archive=archive).open(mode)
    f.write(data)
    assert _members(archive) == {"old": b"OLD"}
    f.close()
    assert _members(archive) == {"old": b"OLD", "new": b"whole"}


def test_zip_a_failed_block_inside_a_batch_is_not_staged_and_the_rest_commits(
    tmp_path,
):
    archive = _zip(tmp_path, old=b"OLD")
    root = ZipPath("", archive=archive)
    with root.batch():
        (root / "a").write_bytes(b"A")
        with pytest.raises(Boom):
            with (root / "b").open("wb") as f:
                f.write(b"part")
                raise Boom
        assert not (root / "b").exists()
        with pytest.raises(Boom):
            with (root / "old").open("w") as f:
                f.write("part")
                raise Boom
        assert (root / "old").read_bytes() == b"OLD"
        (root / "c").write_bytes(b"C")
    assert _members(archive) == {"old": b"OLD", "a": b"A", "c": b"C"}


def test_zip_a_member_write_the_archive_refuses_raises_once_and_is_not_retried(
    tmp_path, monkeypatch
):
    from pathapi.archive._zip import _ZipBackend

    archive = _zip(tmp_path, old=b"OLD")
    calls = []

    def refuse(self, name, data):
        calls.append(name)
        raise OSError(errno.EACCES, "refused")

    monkeypatch.setattr(_ZipBackend, "write_member", refuse)
    with pytest.raises(OSError, match="refused"):
        with ZipPath("new", archive=archive).open("wb") as f:
            f.write(b"x")
    gc.collect()
    assert calls == ["new"]


def test_a_local_file_keeps_what_a_failed_block_wrote(tmp_path):
    local = LocalPath(tmp_path / "f.bin")
    with pytest.raises(Boom):
        with local.open("wb") as f:
            f.write(b"part")
            raise Boom
    assert local.read_bytes() == b"part"
    memory = MemPath("/f.bin")
    with pytest.raises(Boom):
        with memory.open("wb") as f:
            f.write(b"part")
            raise Boom
    assert memory.read_bytes() == b"part"
