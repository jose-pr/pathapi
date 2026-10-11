import pytest

pytest.importorskip("azure.storage.blob")

from pathlib_next.uri.schemes.az import AzPath, AzBackend
from pathlib_next.uri import UriPath


def test_az_path_registration():
    """Test that az: scheme resolves to AzPath."""
    path = UriPath("az://account/container/key/path", findclass=True)
    assert isinstance(path, AzPath)


def test_az_path_components(az_server):
    """Test that AzPath components are parsed correctly."""
    path, _ = az_server
    assert path.account == "testaccount"
    assert path.container == "testcontainer"
    assert path.key == ""


def test_az_stat_root(az_server):
    """Root always exists and is a directory."""
    path, _ = az_server
    stat = path.stat()
    assert stat.is_dir()


def test_az_list_container_root(az_server):
    """List the container root, which holds fixture_tree."""
    path, _ = az_server
    children = {child.name for child in path.iterdir()}
    assert children == {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir"}


def test_az_read_existing_file(az_server, fixture_tree):
    """Read an existing file from the container."""
    path, backend = az_server
    file_content = (fixture_tree / "a.txt").read_bytes()
    file_path = path / "a.txt"
    content = file_path.read_bytes()
    assert content == file_content


def test_az_write_new_file(az_server):
    """Write a new file to the container."""
    path, backend = az_server
    test_path = path / "new_file.txt"
    test_path.write_bytes(b"test content")
    content = test_path.read_bytes()
    assert content == b"test content"


def test_az_mkdir(az_server):
    """Create a directory marker."""
    path, backend = az_server
    dir_path = path / "testdir"
    dir_path.mkdir()
    assert dir_path.exists()
    assert dir_path.is_dir()


def test_az_delete_file(az_server):
    """Delete a file."""
    path, backend = az_server
    test_path = path / "to_delete.txt"
    test_path.write_bytes(b"delete me")
    assert test_path.exists()
    test_path.unlink()
    assert not test_path.exists()


def test_az_open_modes(az_server):
    """Test open() with different modes."""
    path, backend = az_server
    test_path = path / "mode_test.txt"

    # Write mode
    with test_path.open("wb") as f:
        f.write(b"hello")

    # Read mode
    with test_path.open("rb") as f:
        content = f.read()
    assert content == b"hello"

    # Exclusive mode (should fail if exists)
    with pytest.raises(FileExistsError):
        test_path.open("x")


def _blob_that_is_also_a_prefix(path):
    base = path / "col"
    (base / "logs").write_bytes(b"FILE-CONTENT")
    (base / "logs" / "2026.txt").write_bytes(b"child")
    (base / "d" / "x").write_bytes(b"x")
    return base


def test_az_blob_that_is_also_a_prefix_lists_as_the_blob(az_server):
    """The SDK lists a page's prefixes before its blobs; the blob still wins."""
    path, _ = az_server
    base = _blob_that_is_also_a_prefix(path)
    listing = dict(base._scandir())
    assert sorted(listing) == ["d", "logs"]
    assert not listing["logs"].is_dir()
    assert listing["logs"].st_size == len(b"FILE-CONTENT")
    assert not (base / "logs").stat().is_dir()


def test_az_prefix_with_a_hidden_subtree_is_not_copied_moved_or_removed(az_server):
    from pathlib_next.mempath import MemPath

    path, _ = az_server
    base = _blob_that_is_also_a_prefix(path)
    target = MemPath("/copied")
    with pytest.raises(OSError, match="nothing was changed"):
        base.copy(target, recursive=True)
    assert not target.exists()
    with pytest.raises(OSError, match="nothing was changed"):
        base.move(path / "moved")
    with pytest.raises(OSError, match="nothing was changed"):
        base.rm(recursive=True)
    assert (base / "logs" / "2026.txt").read_bytes() == b"child"
    assert (base / "d" / "x").read_bytes() == b"x"
    assert not (path / "moved").exists()


def test_az_write_onto_a_prefix_directory_is_refused(az_server):
    path, _ = az_server
    with pytest.raises(IsADirectoryError):
        (path / "sub").write_bytes(b"clobber")
    assert (path / "sub").is_dir()


def test_az_rmdir_of_the_container_root_is_refused(az_server):
    path, _ = az_server
    with pytest.raises(PermissionError):
        path.rmdir()
    assert (path / "a.txt").exists()


def _closed_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_az_nothing_listening_is_a_connection_error_with_the_sas_token_unseen():
    # The SDK's own error is not an OSError, and its text names the URL.
    sas = "sv=2024-01-01&sp=r&sig=SECRETSIGNATUREVALUE"
    backend = AzBackend(
        account_url=f"http://127.0.0.1:{_closed_port()}/acct?{sas}",
        retry_total=0,
        # A refused loopback connection takes about two seconds to be
        # reported on Windows; a shorter timeout would be the error instead.
        connection_timeout=10,
    )
    path = AzPath("az://acct/container/blob.txt", backend=backend)
    with pytest.raises(ConnectionError) as info:
        path.stat()
    assert not isinstance(info.value, TimeoutError)
    assert info.value.filename == "az://acct/container/blob.txt"
    assert info.value.__cause__ is None
    assert "SECRETSIGNATUREVALUE" not in str(info.value)
    assert path.exists() is False


@pytest.fixture
def listing_requests(monkeypatch):
    """The request lines the loopback server receives."""
    import http.server

    seen = []
    original = http.server.BaseHTTPRequestHandler.parse_request

    def parse_request(self):
        ok = original(self)
        if ok:
            seen.append(f"{self.command} {self.path}")
        return ok

    monkeypatch.setattr(
        http.server.BaseHTTPRequestHandler, "parse_request", parse_request
    )
    return seen


def test_az_probes_for_a_directory_ask_the_service_for_one_item(
    az_server, listing_requests
):
    path, _ = az_server
    assert (path / "sub").is_dir()
    assert not (path / "missing").exists()
    probes = [line for line in listing_requests if "comp=list" in line]
    assert len(probes) == 2
    assert all(
        "prefix=sub%2F" in line or "prefix=missing%2F" in line for line in probes
    )
    assert all("maxresults=1" in line for line in probes)


# --- listing pages and batched deletes --------------------------------------


def _blob_list_requests(state):
    return [
        target
        for method, target in state.requests
        if method == "GET" and "comp=list" in target
    ]


def test_az_a_listing_that_spans_pages_shows_every_entry_once(az_server, az_api_server):
    path, _ = az_server
    state = az_api_server.state
    for index in range(3):
        state.objects[f"paged/d{index}/x"] = b"x"
        state.objects[f"paged/f{index}"] = b"x"
    state.page_size = 2
    del state.requests[:]
    names = sorted(child.name for child in (path / "paged").iterdir())
    assert names == ["d0", "d1", "d2", "f0", "f1", "f2"]
    assert len(_blob_list_requests(state)) >= 3
    assert any("marker=" in target for target in _blob_list_requests(state))


def test_az_removing_a_prefix_lists_every_page(az_server, az_api_server):
    path, _ = az_server
    state = az_api_server.state
    for index in range(7):
        state.objects[f"paged/f{index}"] = b"x"
    state.page_size = 2
    (path / "paged").rm(recursive=True)
    assert not [name for name in state.objects if name.startswith("paged/")]


def test_az_rm_recursive_removes_a_prefix_in_one_batch_request(
    az_server, az_api_server
):
    path, _ = az_server
    state = az_api_server.state
    for index in range(250):
        state.objects[f"bulk/f{index:03d}"] = b"x"
    del state.requests[:]
    (path / "bulk").rm(recursive=True)
    assert [method for method, _ in state.requests].count("DELETE") == 0
    posts = [target for method, target in state.requests if method == "POST"]
    assert len(posts) == 1 and "comp=batch" in posts[0]
    assert [len(names) for names in state.batches] == [250]
    assert not [name for name in state.objects if name.startswith("bulk/")]
    assert "a.txt" in state.objects


def test_az_a_refused_key_of_a_batch_is_offered_once_with_its_own_path(
    az_server, az_api_server
):
    path, _ = az_server
    state = az_api_server.state
    for index in range(5):
        state.objects[f"bulk/f{index}"] = b"x"
    state.refuse["bulk/f3"] = 403
    offered = []
    (path / "bulk").rm(
        recursive=True,
        ignore_error=lambda error, where: offered.append((type(error), str(where)))
        or True,
    )
    assert offered == [(PermissionError, f"{path}/bulk/f3")]
    assert [name for name in state.objects if name.startswith("bulk/")] == ["bulk/f3"]


# --- checksum() and syncing -------------------------------------------------


def _content_reads(state):
    """The requests that download a blob's bytes."""
    return [
        target
        for method, target in state.requests
        if method == "GET" and "comp=list" not in target
    ]


def test_az_checksum_is_the_content_md5_and_downloads_nothing(
    az_server, az_api_server, fixture_tree
):
    import hashlib

    path, _ = az_server
    state = az_api_server.state
    content = (fixture_tree / "a.txt").read_bytes()
    del state.requests[:]
    assert (path / "a.txt").checksum() == hashlib.md5(content).hexdigest()
    assert _content_reads(state) == []


def test_az_checksum_of_a_blob_without_a_content_md5_is_refused(
    az_server, az_api_server
):
    path, _ = az_server
    az_api_server.state.metadata["a.txt"] = {"Content-MD5": None}
    with pytest.raises(NotImplementedError):
        (path / "a.txt").checksum("md5")
    with pytest.raises(FileNotFoundError):
        (path / "nothing").checksum()
    with pytest.raises(IsADirectoryError):
        (path / "sub").checksum()


def test_az_syncing_an_unchanged_tree_downloads_no_blob(az_server, az_api_server):
    from pathlib_next.utils.sync import PathSyncer

    path, _ = az_server
    state = az_api_server.state
    for index in range(6):
        state.objects[f"mirror/src/d{index % 2}/f{index}"] = bytes([index]) * 100
    syncer = PathSyncer(quick_check=False)
    syncer.sync(path / "mirror" / "src", path / "mirror" / "dst")
    assert state.objects["mirror/dst/d1/f5"] == bytes([5]) * 100
    del state.requests[:]
    syncer.sync(path / "mirror" / "src", path / "mirror" / "dst")
    assert _content_reads(state) == []
    assert [m for m, _ in state.requests if m not in ("GET", "HEAD")] == []


# --- copy(): one copy request in the service --------------------------------


def test_az_copy_of_a_blob_is_one_service_copy_and_reads_nothing(
    az_server, az_api_server, fixture_tree
):
    path, _ = az_server
    state = az_api_server.state
    del state.requests[:]
    (path / "a.txt").copy(path / "dup" / "a.txt")
    assert state.objects["dup/a.txt"] == (fixture_tree / "a.txt").read_bytes()
    assert state.copies == [("a.txt", "dup/a.txt")]
    assert _content_reads(state) == []


def test_az_copy_onto_an_existing_blob_needs_overwrite(az_server, az_api_server):
    path, _ = az_server
    state = az_api_server.state
    with pytest.raises(FileExistsError):
        (path / "a.txt").copy(path / "b.py")
    with pytest.raises(IsADirectoryError):
        (path / "a.txt").copy(path / "sub", overwrite=True)
    with pytest.raises(OSError, match="same file"):
        (path / "a.txt").copy(path / "a.txt", overwrite=True)
    assert state.copies == []
    (path / "a.txt").copy(path / "b.py", overwrite=True)
    assert state.copies == [("a.txt", "b.py")]


def test_az_a_refused_copy_leaves_an_existing_target_as_it_was(
    az_server, az_api_server
):
    path, _ = az_server
    state = az_api_server.state
    before = state.objects["b.py"]
    state.copy_status = 403
    with pytest.raises(PermissionError):
        (path / "a.txt").copy(path / "b.py", overwrite=True)
    assert state.objects["b.py"] == before
    assert [method for method, _ in state.requests].count("DELETE") == 0


def test_az_a_service_that_does_not_implement_the_copy_is_read_and_written_instead(
    az_server, az_api_server, fixture_tree
):
    path, _ = az_server
    state = az_api_server.state
    state.copy_status = 501
    (path / "a.txt").copy(path / "dup.txt")
    assert state.objects["dup.txt"] == (fixture_tree / "a.txt").read_bytes()


def test_az_moving_a_prefix_copies_each_blob_in_the_service(az_server, az_api_server):
    path, _ = az_server
    state = az_api_server.state
    for name in ("one", "sub/two"):
        state.objects[f"tree/{name}"] = name.encode()
    del state.requests[:]
    (path / "tree").move(path / "moved")
    assert state.objects["moved/one"] == b"one"
    assert state.objects["moved/sub/two"] == b"sub/two"
    assert not [name for name in state.objects if name.startswith("tree/")]
    assert sorted(state.copies) == [
        ("tree/one", "moved/one"),
        ("tree/sub/two", "moved/sub/two"),
    ]
    assert _content_reads(state) == []


# --- reads and writes stream: memory is bounded by a few chunks -------------

MIB = 1024 * 1024
BIG = 32 * MIB


def _traced(operation):
    """The peak memory, in bytes, that Python allocates while `operation()`
    runs (the loopback server's included), and its result."""
    import tracemalloc

    tracemalloc.start()
    try:
        result = operation()
        return tracemalloc.get_traced_memory()[1], result
    finally:
        tracemalloc.stop()


def _read_in_pieces(path):
    import hashlib

    digest = hashlib.md5()
    with path.open("rb") as stream:
        while piece := stream.read(MIB):
            digest.update(piece)
    return digest.hexdigest()


def _write_in_pieces(path, piece):
    with path.open("wb") as stream:
        for _ in range(BIG // len(piece)):
            stream.write(piece)


def test_az_reading_a_large_blob_holds_a_few_chunks(az_server, az_api_server):
    import hashlib

    path, _ = az_server
    content = bytes(range(256)) * (BIG // 256)
    az_api_server.state.objects["big.bin"] = content
    peak, digest = _traced(lambda: _read_in_pieces(path / "big.bin"))
    assert digest == hashlib.md5(content).hexdigest()
    assert peak < 12 * MIB, peak / MIB


def test_az_writing_a_large_blob_holds_a_few_chunks(az_server, az_api_server):
    import hashlib

    path, _ = az_server
    state = az_api_server.state
    state.discard_uploads = True
    piece = bytes(range(256)) * (MIB // 256)
    peak, _ = _traced(lambda: _write_in_pieces(path / "up.bin", piece))
    assert state.discarded["up.bin"] == (BIG, hashlib.md5(piece * 32).hexdigest())
    assert peak < 12 * MIB, peak / MIB
    blocks = [
        target
        for method, target in state.requests
        if method == "PUT" and "comp=block" in target and "blocklist" not in target
    ]
    assert len(blocks) > 1


def test_az_a_stream_seeks_without_reading_what_it_skips(az_server, az_api_server):
    path, _ = az_server
    state = az_api_server.state
    state.objects["big.bin"] = bytes(range(256)) * (BIG // 256)
    del state.requests[:]
    with (path / "big.bin").open("rb") as stream:
        stream.seek(-4, 2)
        assert stream.read() == bytes([252, 253, 254, 255])
        stream.seek(20 * MIB + 1)
        assert stream.read(3) == bytes([1, 2, 3])
    assert len(_content_reads(state)) == 3


def test_az_an_exclusive_upload_does_not_replace_an_existing_blob(
    az_server, az_api_server, monkeypatch
):
    import io

    from pathlib_next.uri.schemes import _objstore as store

    path, _ = az_server
    state = az_api_server.state
    before = state.objects["a.txt"]
    with pytest.raises(FileExistsError):
        (path / "a.txt")._upload(b"new", exclusive=True)
    # Above the single-request size the upload is made of blocks.
    monkeypatch.setattr(store, "SPOOL_SIZE", 8)
    with pytest.raises(FileExistsError):
        (path / "a.txt")._upload_file(io.BytesIO(b"123456789"), 9, exclusive=True)
    assert state.objects["a.txt"] == before
    (path / "fresh.txt")._upload_file(io.BytesIO(b"123456789"), 9, exclusive=True)
    assert state.objects["fresh.txt"] == b"123456789"


# --- a read is one version of the blob --------------------------------------

OLD = bytes(range(64))
NEW = b"Z" * 64


def _stale_errno():
    import errno

    return getattr(errno, "ESTALE", errno.EIO)


def _change_on_second_read(state, name, change):
    """Run `change()` just before the server reads `name` for its second request."""
    seen = []

    def hook(read):
        if read == name:
            seen.append(read)
            if len(seen) == 2:
                change()

    state.before_content_read = hook


@pytest.fixture
def small_chunks(monkeypatch):
    from pathlib_next.uri.schemes import az

    monkeypatch.setattr(az, "READ_CHUNK", 16)


def test_az_a_read_across_an_overwrite_raises_and_returns_no_mixed_bytes(
    az_server, az_api_server, small_chunks
):
    path, _ = az_server
    state = az_api_server.state
    state.objects["pinned.bin"] = OLD
    _change_on_second_read(
        state, "pinned.bin", lambda: state.objects.update({"pinned.bin": NEW})
    )
    got = []
    with (path / "pinned.bin").open("rb") as stream:
        got.append(stream.read(16))
        with pytest.raises(OSError) as info:
            stream.read(16)
    assert got == [OLD[:16]]
    error = info.value
    assert not isinstance(error, FileNotFoundError)
    assert error.errno == _stale_errno()
    assert "changed while it was being read" in error.strerror
    assert error.filename == f"{path}/pinned.bin"
    assert error.__cause__ is None
    assert state.objects["pinned.bin"] == NEW


def test_az_a_read_of_a_blob_deleted_midway_is_file_not_found(
    az_server, az_api_server, small_chunks
):
    path, _ = az_server
    state = az_api_server.state
    state.objects["pinned.bin"] = OLD
    _change_on_second_read(state, "pinned.bin", lambda: state.objects.pop("pinned.bin"))
    with (path / "pinned.bin").open("rb") as stream:
        assert stream.read(16) == OLD[:16]
        with pytest.raises(FileNotFoundError):
            stream.read(16)


def test_az_an_unchanged_blob_reads_whole_across_chunks_and_after_a_seek(
    az_server, az_api_server, small_chunks
):
    path, _ = az_server
    state = az_api_server.state
    state.objects["pinned.bin"] = OLD
    with (path / "pinned.bin").open("rb") as stream:
        assert stream.read() == OLD
        stream.seek(20)
        assert stream.read(30) == OLD[20:50]
        stream.seek(-5, 2)
        assert stream.read() == OLD[-5:]
        stream.seek(0)
        assert stream.read(16) == OLD[:16]


def test_az_every_request_after_the_first_names_the_etag(
    az_server, az_api_server, small_chunks
):
    path, _ = az_server
    state = az_api_server.state
    state.objects["pinned.bin"] = OLD
    etag = f'"{state.objects.version("pinned.bin")}"'
    del state.reads[:]
    with (path / "pinned.bin").open("rb") as stream:
        stream.read()
        stream.seek(0)
        stream.read(1)
    reads = [read for read in state.reads if read[0] == "pinned.bin"]
    assert reads[0] == ("pinned.bin", 0, None)
    assert {read[2] for read in reads[1:]} == {etag}
    assert [read[1] for read in reads[:5]] == [0, 16, 32, 48, 64]
    assert reads[-1] == ("pinned.bin", 0, etag)
    # Two opens are two snapshots: the second one pins the newer ETag.
    state.objects["pinned.bin"] = NEW
    del state.reads[:]
    with (path / "pinned.bin").open("rb") as stream:
        assert stream.read() == NEW
    assert state.reads[-1][2] == f'"{state.objects.version("pinned.bin")}"' != etag
