"""`FileStat`: the slotted stat for backends without an `os.stat_result`."""

from __future__ import annotations

from pathlib_next import FileStat


def test_repr_shows_a_small_size_as_a_plain_number():
    assert "size=500," in repr(FileStat(st_size=500))
    assert "size=5.0K," in repr(FileStat(st_size=5120))


def test_repr_shows_a_large_size_in_binary_units():
    assert "size=1.0K," in repr(FileStat(st_size=1024))
    assert "size=1.0M," in repr(FileStat(st_size=1024 * 1024))
    assert "size=1.0G," in repr(FileStat(st_size=1024 * 1024 * 1024))
    assert "size=1.0Y," in repr(FileStat(st_size=1024**8))


def test_filestat_methods():
    # test __init__, settime, setmode
    st = FileStat(is_dir=False, st_size=100, st_mtime=50)
    assert st.st_size == 100
    assert st.st_mtime == 50
    assert st.is_file()
    assert not st.is_dir()

    st.settime(123)
    assert st.st_atime == 123
    assert st.st_mtime == 123
    assert st.st_ctime == 123

    st.setmode(0o755, isdir=False)
    import stat

    assert stat.S_ISREG(st.st_mode)
    assert (st.st_mode & 0o777) == 0o755

    st.setmode(0o700, isdir=True)
    assert stat.S_ISDIR(st.st_mode)
    assert (st.st_mode & 0o777) == 0o700

    # __getitem__, items, __repr__, __str__
    assert st["st_size"] == 100
    items_dict = dict(st.items())
    assert items_dict["st_size"] == 100

    rep = repr(st)
    assert "FileStat" in rep

    s = str(st)
    assert "st_size=100" in s

    # from_path with FileStat returned from path.stat()
    class FakePathWithFileStat:
        def stat(self, follow_symlinks=True):
            return st

    assert FileStat.from_path(FakePathWithFileStat()) is st

    # from_path with missing raises None
    class FakePath:
        def stat(self, follow_symlinks=True):
            raise FileNotFoundError()

    assert FileStat.from_path(FakePath()) is None

    # from_path with normal stat object
    class FakeStat:
        def __init__(self):
            self.st_mode = 0o100644
            self.st_size = 50
            self.st_mtime = 10

    class FakePathWithStat:
        def stat(self, follow_symlinks=True):
            return FakeStat()

    st_from = FileStat.from_path(FakePathWithStat())
    assert st_from.st_size == 50
    assert st_from.st_mtime == 10


def test_stat_protocol_helpers():
    from pathlib_next.protocols.fs import Stat
    import stat

    class StubStatPath(Stat):
        def __init__(self, mode):
            self.mode = mode

        def stat(self, *, follow_symlinks=True):
            class FakeStat:
                st_mode = self.mode
                st_size = 0
                st_mtime = 0

            return FakeStat()

    # Block device
    p_blk = StubStatPath(stat.S_IFBLK)
    assert p_blk.is_block_device()
    assert not p_blk.is_char_device()
    assert not p_blk.is_fifo()
    assert not p_blk.is_socket()

    # Character device
    p_chr = StubStatPath(stat.S_IFCHR)
    assert not p_chr.is_block_device()
    assert p_chr.is_char_device()
    assert not p_chr.is_fifo()
    assert not p_chr.is_socket()

    # FIFO
    p_fifo = StubStatPath(stat.S_IFIFO)
    assert not p_fifo.is_block_device()
    assert not p_fifo.is_char_device()
    assert p_fifo.is_fifo()
    assert not p_fifo.is_socket()

    # Socket
    p_sock = StubStatPath(stat.S_IFSOCK)
    assert not p_sock.is_block_device()
    assert not p_sock.is_char_device()
    assert not p_sock.is_fifo()
    assert p_sock.is_socket()

    # Test error fallback to None st_mode returning False
    class ErrStatPath(Stat):
        def stat(self, *, follow_symlinks=True):
            raise PermissionError("Access denied")

    p_err = ErrStatPath()
    assert not p_err.exists()
    assert not p_err.is_dir()
    assert not p_err.is_file()
    assert not p_err.is_symlink()
