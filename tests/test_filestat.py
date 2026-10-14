"""`FileStat`: the slotted stat for backends without an `os.stat_result`."""

from __future__ import annotations

import stat

from pathapi import FileStat


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
    from pathapi.protocols.fs import Stat

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


def test_a_bare_permission_mode_gets_the_type_of_the_file():
    assert FileStat(st_mode=0o644).is_file()
    assert not FileStat(st_mode=0o644).is_dir()
    assert FileStat(st_mode=0o755, is_dir=True).is_dir()
    assert not FileStat(st_mode=0o755, is_dir=True).is_file()
    assert stat.S_IMODE(FileStat(st_mode=0o755, is_dir=True).st_mode) == 0o755


def test_a_mode_that_has_a_type_keeps_it():
    assert FileStat(st_mode=stat.S_IFLNK | 0o777).is_symlink()
    assert FileStat(st_mode=stat.S_IFREG | 0o600, is_dir=True).is_file()
    assert FileStat(st_mode=stat.S_IFDIR | 0o700).is_dir()


def test_a_permission_mode_counts_as_reported():
    assert FileStat(st_mode=0o644).mode_known is True
    assert FileStat().mode_known is False
    assert FileStat(is_dir=True).is_dir()


def test_setmode_keeps_a_device_or_socket_from_becoming_a_directory():
    for kind in (stat.S_IFBLK, stat.S_IFSOCK, stat.S_IFCHR):
        listed = FileStat(st_mode=kind | 0o600)
        listed.setmode(0o644)
        assert listed.is_file()
        assert not listed.is_dir()
    directory = FileStat(is_dir=True)
    directory.setmode(0o700)
    assert directory.is_dir()
    assert stat.S_IMODE(directory.st_mode) == 0o700
