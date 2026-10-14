"""Member names an archive holds that are not valid UTF-8: they list, compare,
hash and read like any other path, as a surrogate-escaped name."""

import io
import tarfile

import pytest

from pathapi.archive import TarPath


@pytest.fixture
def latin1_tar(tmp_path):
    """`d/café.txt` stored as the single byte 0xE9, the way a tar written in
    a Latin-1 locale holds it."""
    archive = tmp_path / "latin1.tar"
    with tarfile.open(
        archive, "w", format=tarfile.GNU_FORMAT, encoding="latin-1"
    ) as tf:
        for name, data in (("d/café.txt", b"x"), ("d/plain.txt", b"p")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return archive


def _child(root):
    (child,) = [c for c in (root / "d").iterdir() if c.name != "plain.txt"]
    return child


def test_a_member_name_that_is_not_utf8_is_one_component(latin1_tar):
    root = TarPath(archive=latin1_tar)
    child = _child(root)
    assert child.name == "caf\udce9.txt"
    assert child.parent == root / "d"
    assert str(child) == "d/caf\udce9.txt"


def test_such_a_name_compares_hashes_and_deduplicates(latin1_tar):
    root = TarPath(archive=latin1_tar)
    first, second = _child(root), _child(root)
    assert first == second and hash(first) == hash(second)
    assert len({first, second}) == 1
    assert first != root / "d" / "plain.txt"


def test_such_a_name_reads_through_every_spelling_of_it(latin1_tar):
    root = TarPath(archive=latin1_tar)
    child = _child(root)
    assert child.read_bytes() == b"x"
    assert child.stat().st_size == 1
    assert child.exists() and child.is_file()
    typed = TarPath("d/caf\udce9.txt", archive=latin1_tar)
    assert typed == child
    assert typed.read_bytes() == b"x"
    assert TarPath(str(child), archive=latin1_tar).read_bytes() == b"x"


def test_such_a_name_is_listed_by_walk_and_glob(latin1_tar):
    root = TarPath(archive=latin1_tar)
    walked = sorted(name for _, _, files in root.walk() for name in files)
    assert walked == ["caf\udce9.txt", "plain.txt"]
    globbed = sorted(str(p) for p in root.glob("d/*.txt"))
    assert [g.rsplit("/", 1)[-1] for g in globbed] == ["caf\udce9.txt", "plain.txt"]
