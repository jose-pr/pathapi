import pytest

import objstore_fakes

pytest.importorskip("google.cloud.storage")

from pathlib_next.uri.schemes.gs import GsPath, GsBackend
from pathlib_next.uri import UriPath


@pytest.fixture
def gs_server(gcs_api_server, monkeypatch):
    """The fake GCS server through the real SDK. `GsBackend` no longer turns
    `api_endpoint` into a process-wide `STORAGE_EMULATOR_HOST`, so a custom
    endpoint authenticates unless told not to:
    `use_auth_w_custom_endpoint=False` is the SDK's own no-auth switch."""
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    base_url, bucket_name = gcs_api_server
    backend = GsBackend(
        client_options={"api_endpoint": base_url},
        use_auth_w_custom_endpoint=False,
    )
    yield GsPath(f"gs://{bucket_name}", backend=backend), backend
    # The backend must not have redirected the rest of the process.
    import os

    assert "STORAGE_EMULATOR_HOST" not in os.environ


def test_gs_path_registration():
    """Test that gs: scheme resolves to GsPath."""
    path = UriPath("gs://bucket/key/path", findclass=True)
    assert isinstance(path, GsPath)


def test_gs_path_components(gs_server):
    """Test that GsPath components are parsed correctly."""
    path, _ = gs_server
    assert path.bucket_name == "test-bucket"
    assert path.key == ""


def test_gs_stat_root(gs_server):
    """Root always exists and is a directory."""
    path, _ = gs_server
    stat = path.stat()
    assert stat.is_dir()


def test_gs_list_bucket_root(gs_server):
    """List the bucket root, which holds fixture_tree."""
    path, _ = gs_server
    children = {child.name for child in path.iterdir()}
    assert children == {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir"}


def test_gs_read_existing_file(gs_server, fixture_tree):
    """Read an existing file from the bucket."""
    path, backend = gs_server
    file_content = (fixture_tree / "a.txt").read_bytes()
    file_path = path / "a.txt"
    content = file_path.read_bytes()
    assert content == file_content


def test_gs_write_new_file(gs_server):
    """Write a new file to the bucket."""
    path, backend = gs_server
    test_path = path / "new_file.txt"
    test_path.write_bytes(b"test content")
    content = test_path.read_bytes()
    assert content == b"test content"


def test_gs_mkdir(gs_server):
    """Create a directory marker."""
    path, backend = gs_server
    dir_path = path / "testdir"
    dir_path.mkdir()
    assert dir_path.exists()
    assert dir_path.is_dir()


def test_gs_delete_file(gs_server):
    """Delete a file."""
    path, backend = gs_server
    test_path = path / "to_delete.txt"
    test_path.write_bytes(b"delete me")
    assert test_path.exists()
    test_path.unlink()
    assert not test_path.exists()


def test_gs_open_modes(gs_server):
    """Test open() with different modes."""
    path, backend = gs_server
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


def _object_that_is_also_a_prefix(path):
    base = path / "col"
    (base / "logs").write_bytes(b"FILE-CONTENT")
    (base / "logs" / "2026.txt").write_bytes(b"child")
    (base / "d" / "x").write_bytes(b"x")
    return base


def test_gs_object_that_is_also_a_prefix_lists_as_the_object(gs_server):
    path, _ = gs_server
    base = _object_that_is_also_a_prefix(path)
    listing = dict(base._scandir())
    assert sorted(listing) == ["d", "logs"]
    assert not listing["logs"].is_dir()
    assert not (base / "logs").stat().is_dir()


def test_gs_prefix_with_a_hidden_subtree_is_not_copied_moved_or_removed(gs_server):
    from pathlib_next.mempath import MemPath

    path, _ = gs_server
    base = _object_that_is_also_a_prefix(path)
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


def test_gs_write_onto_a_prefix_directory_is_refused(gs_server):
    path, _ = gs_server
    with pytest.raises(IsADirectoryError):
        (path / "sub").write_bytes(b"clobber")
    assert (path / "sub").is_dir()


def test_gs_rmdir_of_the_bucket_root_is_refused(gs_server):
    path, _ = gs_server
    with pytest.raises(PermissionError):
        path.rmdir()
    assert (path / "a.txt").exists()


def _closed_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _unreachable(monkeypatch, **options):
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    backend = GsBackend(
        client_options={"api_endpoint": f"http://127.0.0.1:{_closed_port()}"},
        use_auth_w_custom_endpoint=False,
        project="p",
        **options,
    )
    return GsPath("gs://bucket/blob.txt", backend=backend)


def test_gs_nothing_listening_is_a_connection_error_without_retries(monkeypatch):
    # A refused loopback connection takes about two seconds to be reported on
    # Windows, so the timeout must be longer than that.
    path = _unreachable(monkeypatch, timeout=10, retry=None)
    with pytest.raises(ConnectionError) as info:
        path.stat()
    assert not isinstance(info.value, TimeoutError)
    assert info.value.filename == "gs://bucket/blob.txt"
    assert info.value.__cause__ is None
    assert path.exists() is False


def test_gs_a_retry_that_runs_out_of_time_is_still_a_connection_error(monkeypatch):
    from google.cloud.storage.retry import DEFAULT_RETRY

    path = _unreachable(monkeypatch, timeout=10, retry=DEFAULT_RETRY.with_deadline(1))
    with pytest.raises(ConnectionError) as info:
        path.stat()
    assert info.value.__cause__ is None


# --- listing pages and batched deletes --------------------------------------


def _list_requests(state):
    return [
        target
        for method, target in state.requests
        if method == "GET" and target.startswith("/storage/v1/b/test-bucket/o?")
    ]


def test_gs_a_listing_that_spans_pages_shows_every_entry_once(
    gs_server, gcs_api_server
):
    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(3):
        state.objects[f"paged/d{index}/x"] = b"x"
        state.objects[f"paged/f{index}"] = b"x"
    state.page_size = 2
    del state.requests[:]
    names = sorted(child.name for child in (path / "paged").iterdir())
    assert names == ["d0", "d1", "d2", "f0", "f1", "f2"]
    assert len(_list_requests(state)) >= 3
    assert len({target for target in _list_requests(state)}) >= 3


def test_gs_removing_a_prefix_lists_every_page(gs_server, gcs_api_server):
    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(7):
        state.objects[f"paged/f{index}"] = b"x"
    state.page_size = 2
    (path / "paged").rm(recursive=True)
    assert not [name for name in state.objects if name.startswith("paged/")]


def test_gs_rm_recursive_removes_a_prefix_in_batch_requests(gs_server, gcs_api_server):
    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(250):
        state.objects[f"bulk/f{index:03d}"] = b"x"
    del state.requests[:]
    (path / "bulk").rm(recursive=True)
    assert [method for method, _ in state.requests].count("DELETE") == 0
    posts = [
        target
        for method, target in state.requests
        if method == "POST" and target.split("?")[0] == "/batch/storage/v1"
    ]
    assert len(posts) == 3
    assert [len(names) for names in state.batches] == [100, 100, 50]
    assert not [name for name in state.objects if name.startswith("bulk/")]
    assert "a.txt" in state.objects


def test_gs_a_refused_key_of_a_batch_is_offered_once_with_its_own_path(
    gs_server, gcs_api_server
):
    path, _ = gs_server
    state = gcs_api_server.state
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


def test_gs_a_blob_the_batch_finds_gone_is_not_an_error(gs_server, gcs_api_server):
    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(3):
        state.objects[f"bulk/f{index}"] = b"x"
    state.refuse["bulk/f1"] = 404
    (path / "bulk").rm(recursive=True)
    assert [name for name in state.objects if name.startswith("bulk/")] == ["bulk/f1"]


def test_gs_a_store_without_the_batch_endpoint_is_deleted_key_by_key(
    gs_server, gcs_api_server
):
    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(4):
        state.objects[f"bulk/f{index}"] = b"x"
    state.batch_status = 501
    del state.requests[:]
    (path / "bulk").rm(recursive=True)
    assert not [name for name in state.objects if name.startswith("bulk/")]
    methods = [method for method, _ in state.requests]
    assert methods.count("DELETE") == 4


# --- checksum() and syncing -------------------------------------------------


def _content_reads(state):
    """The requests that download an object's bytes."""
    return [
        target
        for method, target in state.requests
        if method == "GET" and "alt=media" in target
    ]


def test_gs_checksum_is_the_md5_or_crc32c_of_the_object_and_downloads_nothing(
    gs_server, gcs_api_server, fixture_tree
):
    import hashlib

    path, _ = gs_server
    state = gcs_api_server.state
    content = (fixture_tree / "a.txt").read_bytes()
    del state.requests[:]
    assert (path / "a.txt").checksum() == hashlib.md5(content).hexdigest()
    assert (path / "a.txt").checksum("crc32c") == "%08x" % objstore_fakes.crc32c(
        content
    )
    assert _content_reads(state) == []


def test_gs_checksum_of_an_object_without_an_md5_is_refused(gs_server, gcs_api_server):
    path, _ = gs_server
    gcs_api_server.state.metadata["a.txt"] = {"md5Hash": None}
    with pytest.raises(NotImplementedError):
        (path / "a.txt").checksum("md5")
    assert (path / "a.txt").checksum("crc32c")
    with pytest.raises(FileNotFoundError):
        (path / "nothing").checksum()
    with pytest.raises(IsADirectoryError):
        (path / "sub").checksum()


def test_gs_syncing_an_unchanged_tree_downloads_no_object(gs_server, gcs_api_server):
    from pathlib_next.utils.sync import PathSyncer

    path, _ = gs_server
    state = gcs_api_server.state
    for index in range(6):
        state.objects[f"mirror/src/d{index % 2}/f{index}"] = bytes([index]) * 100
    syncer = PathSyncer(quick_check=False)
    syncer.sync(path / "mirror" / "src", path / "mirror" / "dst")
    assert state.objects["mirror/dst/d1/f5"] == bytes([5]) * 100
    del state.requests[:]
    syncer.sync(path / "mirror" / "src", path / "mirror" / "dst")
    assert _content_reads(state) == []
    assert [method for method, _ in state.requests if method != "GET"] == []


# --- copy(): one copy request in the store ----------------------------------


def test_gs_copy_of_a_file_is_one_copy_request_and_reads_nothing(
    gs_server, gcs_api_server, fixture_tree
):
    path, _ = gs_server
    state = gcs_api_server.state
    del state.requests[:]
    (path / "a.txt").copy(path / "dup" / "a.txt")
    assert state.objects["dup/a.txt"] == (fixture_tree / "a.txt").read_bytes()
    assert state.copies == [("a.txt", "dup/a.txt")]
    assert _content_reads(state) == []
    assert not [target for method, target in state.requests if "/upload/" in target]


def test_gs_copy_onto_an_existing_object_needs_overwrite(gs_server, gcs_api_server):
    path, _ = gs_server
    state = gcs_api_server.state
    with pytest.raises(FileExistsError):
        (path / "a.txt").copy(path / "b.py")
    with pytest.raises(IsADirectoryError):
        (path / "a.txt").copy(path / "sub", overwrite=True)
    with pytest.raises(OSError, match="same file"):
        (path / "a.txt").copy(path / "a.txt", overwrite=True)
    assert state.copies == []
    (path / "a.txt").copy(path / "b.py", overwrite=True)
    assert state.copies == [("a.txt", "b.py")]
    assert state.objects["b.py"] == state.objects["a.txt"]


def test_gs_a_refused_copy_leaves_an_existing_target_as_it_was(
    gs_server, gcs_api_server
):
    path, _ = gs_server
    state = gcs_api_server.state
    before = state.objects["b.py"]
    state.copy_status = 403
    with pytest.raises(PermissionError):
        (path / "a.txt").copy(path / "b.py", overwrite=True)
    assert state.objects["b.py"] == before
    assert [method for method, _ in state.requests].count("DELETE") == 0


def test_gs_a_store_that_does_not_implement_the_copy_is_read_and_written_instead(
    gs_server, gcs_api_server, fixture_tree
):
    path, _ = gs_server
    state = gcs_api_server.state
    state.copy_status = 501
    del state.requests[:]
    (path / "a.txt").copy(path / "dup.txt")
    assert state.objects["dup.txt"] == (fixture_tree / "a.txt").read_bytes()
    assert len(_content_reads(state)) == 1


def test_gs_moving_a_prefix_copies_each_object_in_the_store(gs_server, gcs_api_server):
    path, _ = gs_server
    state = gcs_api_server.state
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
