import datetime
import hashlib

import pytest

import objstore_fakes
from pathlib_next.uri.schemes import _objstore as store
from pathlib_next.uri.schemes.gs import BaseGsBackend, GsPath


class _ApiError(Exception):
    """Mirrors `google.api_core.exceptions.GoogleAPICallError`: the HTTP
    status is the `code` attribute (`NotFound.code == 404`), which is what
    `GsPath` classifies by. The real types cannot be raised here -- the SDK
    is not installed in the test venvs."""

    code = None


class _Missing(_ApiError):
    code = 404


class _Forbidden(_ApiError):
    code = 403


class _PreconditionFailed(_ApiError):
    code = 412


class _ServiceUnavailable(_ApiError):
    code = 503


class _NotImplemented(_ApiError):
    code = 501


class _RangeNotSatisfiable(_ApiError):
    code = 416


class _FakeBlob:
    def __init__(self, bucket, name):
        self._bucket = bucket
        self.name = name
        self._generation = None

    @property
    def generation(self):
        # Known once a download or a reload has reported it, as the SDK's.
        return self._generation

    def _require(self, if_generation_match):
        """The object's current generation, after the preconditions: an object
        that is not the generation asked for is 412, and so is a missing one
        when the bucket answers a precondition before it looks for the object."""
        bucket = self._bucket
        if self.name not in bucket.objects:
            if if_generation_match is not None and bucket.missing_is_412:
                raise _PreconditionFailed(self.name)
            raise _Missing(self.name)
        current = bucket.objects.version(self.name)
        if if_generation_match is not None and if_generation_match != current:
            raise _PreconditionFailed(self.name)
        return current

    @property
    def size(self):
        return len(self._bucket.objects[self.name])

    @property
    def updated(self):
        return datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def _stored(self, field, default):
        return self._bucket.stored.get(self.name, {}).get(field, default)

    @property
    def md5_hash(self):
        return self._stored("md5_hash", objstore_fakes.md5_base64(self._content()))

    @property
    def crc32c(self):
        return self._stored("crc32c", objstore_fakes.crc32c_base64(self._content()))

    @property
    def content_encoding(self):
        return self._stored("content_encoding", None)

    def _content(self):
        return self._bucket.objects[self.name]

    def reload(self, if_generation_match=None, **_kwargs):
        if self.name in self._bucket.reload_errors:
            raise self._bucket.reload_errors[self.name]
        self._generation = self._require(if_generation_match)

    def delete(self):
        if self._bucket.batch is not None:
            self._bucket.batch.queued.append(self.name)
            return
        if self.name in self._bucket.delete_errors:
            raise OSError(self.name)
        if self.name in self._bucket.delete_raises:
            raise self._bucket.delete_raises[self.name]
        if self.name not in self._bucket.objects:
            raise _Missing(self.name)
        del self._bucket.objects[self.name]
        self._bucket.deleted.append(self.name)

    def download_as_bytes(
        self, start=None, end=None, checksum=None, if_generation_match=None, **_kwargs
    ):
        if self._bucket.before_read is not None:
            self._bucket.before_read(self.name)
        self._generation = self._require(if_generation_match)
        self._bucket.reads.append((self.name, start, if_generation_match))
        content = self._bucket.objects[self.name]
        if start is not None:
            # A range at or past the end is answered 416, an empty object's
            # included.
            if start >= len(content):
                raise _RangeNotSatisfiable(self.name)
            content = content[start : None if end is None else end + 1]
            self._bucket.ranges.append((self.name, start, end))
        self._bucket.downloads.append(self.name)
        return content

    def upload_from_file(
        self, file_obj, size=None, if_generation_match=None, **_kwargs
    ):
        self._bucket.file_uploads.append((self.name, size))
        self.upload_from_string(file_obj.read(), if_generation_match)

    def upload_from_string(self, data, if_generation_match=None):
        if self._bucket.upload_errors:
            raise self._bucket.upload_errors.pop(0)
        if if_generation_match == 0 and self.name in self._bucket.objects:
            raise _PreconditionFailed(self.name)
        self._bucket.objects[self.name] = bytes(data)
        self._bucket.uploads.append(self.name)


class _FakeIterator:
    """`list_blobs()`'s HTTPIterator: blobs when iterated, and with a
    delimiter the common `prefixes` seen while paging."""

    def __init__(self, bucket, prefix, delimiter):
        self._bucket = bucket
        self._prefix = prefix
        self._delimiter = delimiter
        self.prefixes = set()

    def __iter__(self):
        for name in sorted(self._bucket.objects):
            if not name.startswith(self._prefix):
                continue
            rest = name[len(self._prefix) :]
            if self._delimiter and self._delimiter in rest:
                self.prefixes.add(
                    self._prefix + rest.split(self._delimiter, 1)[0] + self._delimiter
                )
                continue
            yield _FakeBlob(self._bucket, name)


class _Reply:
    """One part of a batch reply: what `requests.Response` gives the SDK's
    `Batch` for it."""

    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


class _FakeBatch:
    """The SDK's `Batch`: inside the block `blob.delete()` only queues; on
    exit one request answers every queued delete, and `_responses` holds a
    reply per delete in order."""

    def __init__(self, bucket, raise_exception=True):
        self._bucket = bucket
        self.queued = []
        self._responses = []

    def __enter__(self):
        self._bucket.batch = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._bucket.batch = None
        if exc_type is None:
            self._finish()

    def _finish(self):
        bucket = self._bucket
        bucket.batch_calls.append(list(self.queued))
        if bucket.batch_rejected:
            raise _ServiceUnavailable("503 batch")
        for name in self.queued:
            if name in bucket.batch_statuses:
                status = bucket.batch_statuses[name]
            elif name in bucket.objects:
                del bucket.objects[name]
                bucket.deleted.append(name)
                status = 204
            else:
                status = 404
            self._responses.append(_Reply(status, b'{"error": {"message": "refused"}}'))
        if bucket.batch_short:
            del self._responses[-1]


class _FakeBucket:
    def __init__(self):
        self.objects = objstore_fakes.VersionedObjects()
        self.before_read = None
        self.missing_is_412 = False
        self.reads = []
        self.downloads = []
        self.ranges = []
        self.file_uploads = []
        self.copies = []
        self.copy_errors = {}
        self.stored = {}
        self.batch = None
        self.batch_calls = []
        self.batch_statuses = {}
        self.batch_rejected = False
        self.batch_short = False
        self.deleted = []
        self.uploads = []
        self.delete_errors = set()
        self.delete_raises = {}
        self.reload_errors = {}
        self.upload_errors = []
        self.list_calls = []

    def blob(self, name, chunk_size=None):
        return _FakeBlob(self, name)

    def list_blobs(self, prefix="", delimiter=None, **_kwargs):
        self.list_calls.append(prefix)
        return _FakeIterator(self, prefix, delimiter)

    def copy_blob(self, blob, destination_bucket, new_name):
        if blob.name in self.copy_errors:
            raise self.copy_errors[blob.name]
        if blob.name not in self.objects:
            raise _Missing(blob.name)
        destination_bucket.objects[new_name] = self.objects[blob.name]
        self.copies.append((blob.name, new_name))


class _FakeClient:
    def __init__(self, batching=False):
        self.bucket_obj = _FakeBucket()
        if batching:
            # A client that cannot batch has no such method.
            self.batch = self._batch

    def bucket(self, _name):
        return self.bucket_obj

    def _batch(self, raise_exception=True):
        return _FakeBatch(self.bucket_obj, raise_exception)


class _FakeBackend(BaseGsBackend):
    def __init__(self, batching=False):
        self.client_obj = _FakeClient(batching)

    def client(self):
        return self.client_obj


def _gs(uri, backend=None):
    return GsPath(uri, backend=backend or _FakeBackend())


def test_stat_and_listing_report_the_updated_time_as_an_epoch():
    # `Blob.updated` is a timezone-aware UTC datetime.
    backend = _FakeBackend()
    backend.client_obj.bucket_obj.objects["dir/a.txt"] = b"abc"
    assert _gs("gs://bucket/dir/a.txt", backend).stat().st_mtime == 1767268800
    entries = dict(_gs("gs://bucket/dir", backend)._scandir())
    assert entries["a.txt"].st_mtime == 1767268800
    assert entries["a.txt"].st_size == 3


def test_rm_recursive_deletes_prefix_tree():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {
            "dir/": b"",
            "dir/a.txt": b"a",
            "dir/sub/b.txt": b"b",
            "other.txt": b"keep",
        }
    )
    _gs("gs://bucket/dir", backend).rm(recursive=True)
    assert bucket.objects == {"other.txt": b"keep"}
    assert bucket.list_calls == ["dir/"]


def test_rm_recursive_deletes_exact_object_before_prefix():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {
            "file.txt": b"x",
            "file.txt/nested.txt": b"keep",
        }
    )
    _gs("gs://bucket/file.txt", backend).rm(recursive=True)
    assert bucket.objects == {"file.txt/nested.txt": b"keep"}
    assert bucket.list_calls == []


def _batching_bucket(count=0, **objects):
    backend = _FakeBackend(batching=True)
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update({f"dir/f{i:03d}": b"x" for i in range(count)})
    bucket.objects.update(objects)
    return backend, bucket


def test_rm_recursive_deletes_a_prefix_in_batches_of_one_hundred():
    backend, bucket = _batching_bucket(250, **{"other.txt": b"keep"})
    _gs("gs://bucket/dir", backend).rm(recursive=True)
    assert bucket.objects == {"other.txt": b"keep"}
    assert [len(names) for names in bucket.batch_calls] == [100, 100, 50]
    assert bucket.batch_calls[0][0] == "dir/f000"
    assert bucket.batch_calls[2][-1] == "dir/f249"


def test_rm_recursive_offers_each_refused_key_once_with_its_own_path():
    backend, bucket = _batching_bucket(5)
    bucket.batch_statuses["dir/f002"] = 403
    offered = []
    _gs("gs://bucket/dir", backend).rm(
        recursive=True,
        ignore_error=lambda error, path: offered.append((type(error), path.key))
        or True,
    )
    assert offered == [(PermissionError, "dir/f002")]
    assert set(bucket.objects) == {"dir/f002"}
    assert len(bucket.batch_calls) == 1


def test_rm_recursive_raises_the_first_refused_key_when_it_is_not_ignored():
    backend, bucket = _batching_bucket(3)
    bucket.batch_statuses["dir/f001"] = 500
    with pytest.raises(OSError) as info:
        _gs("gs://bucket/dir", backend).rm(recursive=True)
    assert type(info.value) is OSError
    assert info.value.filename == "gs://bucket/dir/f001"
    assert "refused" in str(info.value)


def test_rm_recursive_does_not_offer_a_blob_that_is_already_gone():
    backend, bucket = _batching_bucket(3)
    bucket.batch_statuses["dir/f001"] = 404
    offered = []
    _gs("gs://bucket/dir", backend).rm(
        recursive=True, ignore_error=lambda error, path: offered.append(path) or True
    )
    assert offered == []


def test_rm_recursive_deletes_each_key_when_the_batch_request_fails():
    backend, bucket = _batching_bucket(4)
    bucket.batch_rejected = True
    _gs("gs://bucket/dir", backend).rm(recursive=True)
    assert bucket.objects == {}
    assert len(bucket.batch_calls) == 1


def test_rm_recursive_deletes_each_key_when_the_replies_do_not_match_the_keys():
    backend, bucket = _batching_bucket(4)
    bucket.batch_short = True
    _gs("gs://bucket/dir", backend).rm(recursive=True)
    assert bucket.objects == {}


def test_rm_recursive_missing_ok():
    _gs("gs://bucket/missing", _FakeBackend()).rm(recursive=True, missing_ok=True)


def test_rm_recursive_missing_without_missing_ok_raises():
    with pytest.raises(FileNotFoundError):
        _gs("gs://bucket/missing", _FakeBackend()).rm(recursive=True)


def test_rm_recursive_ignore_error_swallows_missing():
    calls = []
    _gs("gs://bucket/missing", _FakeBackend()).rm(
        recursive=True,
        ignore_error=lambda err, path: calls.append((type(err), path.key)) or True,
    )
    assert calls == [(FileNotFoundError, "missing")]


def test_rm_recursive_root_guard():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects["a.txt"] = b"x"
    with pytest.raises(PermissionError):
        _gs("gs://bucket/", backend).rm(recursive=True)
    assert bucket.objects == {"a.txt": b"x"}


def test_rm_recursive_delete_error_reroutes_to_ignore_error():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects["dir/a.txt"] = b"x"
    bucket.delete_errors.add("dir/a.txt")
    calls = []
    _gs("gs://bucket/dir", backend).rm(
        recursive=True,
        ignore_error=lambda err, path: calls.append((type(err), path.key)) or True,
    )
    assert calls == [(OSError, "dir/a.txt")]


# --- a trailing "/" names the directory, not the "dir/" marker object -------


@pytest.mark.parametrize(
    "uri, key",
    [
        ("gs://bucket/dir/", "dir"),
        ("gs://bucket/dir", "dir"),
        ("gs://bucket/", ""),
        ("gs://bucket/a//b", "a//b"),
        ("gs://bucket/dir//", "dir/"),
    ],
)
def test_key_drops_one_trailing_slash(uri, key):
    assert _gs(uri).key == key


def test_trailing_slash_marker_dir_is_a_directory_and_rm_deletes_tree():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {"dir/": b"", "dir/a.txt": b"a", "dir/sub/b.txt": b"b", "other.txt": b"k"}
    )
    p = _gs("gs://bucket/dir/", backend)
    assert p.is_dir()
    assert not p.is_file()
    p.rm(recursive=True)
    assert bucket.objects == {"other.txt": b"k"}


# --- GsBackend passes client options through, never touching os.environ ----


@pytest.fixture
def fake_storage_module(monkeypatch):
    """A stand-in `google.cloud.storage` that records the Client kwargs, so
    the backend is checked without the SDK installed."""
    import importlib
    import sys
    import types

    received = []

    class Client:
        def __init__(self, **kwargs):
            received.append(kwargs)

    storage = types.ModuleType("google.cloud.storage")
    storage.Client = Client
    for name in ("google", "google.cloud"):
        try:
            importlib.import_module(name)
        except ImportError:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)
    monkeypatch.setattr(sys.modules["google.cloud"], "storage", storage, raising=False)
    return received


def test_gs_backend_does_not_mutate_environment(fake_storage_module, monkeypatch):
    import os

    from pathlib_next.uri.schemes.gs import GsBackend

    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    options = {
        "api_endpoint": "https://storage-acme.p.googleapis.com",
        "quota_project_id": "billing-proj",
    }
    GsBackend(client_options=options, project="prod").client()
    assert "STORAGE_EMULATOR_HOST" not in os.environ
    # Every option reaches the client, api_endpoint included.
    assert fake_storage_module == [
        {
            "client_options": {
                "api_endpoint": "https://storage-acme.p.googleapis.com",
                "quota_project_id": "billing-proj",
            },
            "project": "prod",
        }
    ]

    # A later, unrelated backend is not redirected.
    GsBackend(project="unrelated").client()
    assert "STORAGE_EMULATOR_HOST" not in os.environ
    assert fake_storage_module[-1] == {"project": "unrelated"}


def test_gs_backend_caches_its_client(fake_storage_module):
    from pathlib_next.uri.schemes.gs import GsBackend

    backend = GsBackend(project="p")
    assert backend.client() is backend.client()
    assert len(fake_storage_module) == 1


# --- only a not-found reply means "missing" ---------------------------------


def _bucket_with(**objects):
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(objects)
    return backend, bucket


def test_transient_reload_error_is_not_file_not_found():
    backend, bucket = _bucket_with(**{"report.csv": b"PRECIOUS"})
    bucket.reload_errors["report.csv"] = _ServiceUnavailable("503 backend error")
    p = _gs("gs://bucket/report.csv", backend)
    with pytest.raises(OSError) as info:
        p.stat()
    assert not isinstance(info.value, FileNotFoundError)
    assert info.value.__cause__ is None
    assert bucket.objects == {"report.csv": b"PRECIOUS"}


def test_forbidden_reload_is_permission_error():
    backend, bucket = _bucket_with(**{"a.txt": b"x"})
    bucket.reload_errors["a.txt"] = _Forbidden("403")
    p = _gs("gs://bucket/a.txt", backend)
    with pytest.raises(PermissionError):
        p.stat()
    assert not p.exists()  # pathlib parity: exists() swallows OSError


def test_rm_recursive_transient_reload_error_does_not_delete_prefix_tree():
    backend, bucket = _bucket_with(**{"x": b"object", "x/child": b"child"})
    bucket.reload_errors["x"] = _ServiceUnavailable("503")
    with pytest.raises(OSError):
        _gs("gs://bucket/x", backend).rm(recursive=True)
    assert bucket.objects == {"x": b"object", "x/child": b"child"}


def test_unlink_missing_ok_does_not_hide_a_failed_delete():
    backend, bucket = _bucket_with(**{"held.txt": b"x"})
    bucket.delete_raises["held.txt"] = _Forbidden("403 object under hold")
    p = _gs("gs://bucket/held.txt", backend)
    with pytest.raises(PermissionError):
        p.unlink(missing_ok=True)
    with pytest.raises(PermissionError):
        p.unlink()
    assert "held.txt" in bucket.objects


def test_missing_sdk_is_import_error_not_file_not_found():
    try:
        import google.cloud.storage  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("google-cloud-storage is installed")
    p = GsPath("gs://bucket/a.txt")
    with pytest.raises(ImportError):
        p.read_bytes()
    with pytest.raises(ImportError):
        p.exists()


# --- missing or wrong-type targets raise like pathlib ------------------------


def test_iterdir_missing_and_file_raise():
    backend, _bucket = _bucket_with(**{"file.txt": b"x", "d/": b""})
    with pytest.raises(FileNotFoundError):
        list(_gs("gs://bucket/typo", backend).iterdir())
    with pytest.raises(NotADirectoryError):
        list(_gs("gs://bucket/file.txt", backend).iterdir())
    assert list(_gs("gs://bucket/d", backend).iterdir()) == []


def test_unlink_directory_and_rmdir_wrong_targets():
    import errno

    backend, bucket = _bucket_with(**{"file.txt": b"x", "d/": b"", "d/f": b"y"})
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket/d", backend).unlink()
    with pytest.raises(FileNotFoundError):
        _gs("gs://bucket/nope", backend).rmdir()
    with pytest.raises(NotADirectoryError):
        _gs("gs://bucket/file.txt", backend).rmdir()
    with pytest.raises(OSError) as info:
        _gs("gs://bucket/d", backend).rmdir()
    assert info.value.errno == errno.ENOTEMPTY
    assert set(bucket.objects) == {"file.txt", "d/", "d/f"}


def test_read_directory_is_a_directory_error():
    backend, _bucket = _bucket_with(**{"d/f": b"y"})
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket/d", backend).read_bytes()


# --- write streams ----------------------------------------------------------


def test_failed_close_upload_is_not_retried_at_gc():
    import gc

    backend, bucket = _bucket_with()
    bucket.upload_errors.append(_ServiceUnavailable("503"))
    p = _gs("gs://bucket/state.json", backend)
    f = p.open("wb")
    f.write(b'{"version": 1}')
    with pytest.raises(OSError):
        f.close()
    assert f.closed
    p.write_bytes(b'{"version": 2}')
    del f
    gc.collect()
    assert bucket.objects["state.json"] == b'{"version": 2}'


def test_exclusive_create_is_atomic():
    backend, bucket = _bucket_with()
    first = _gs("gs://bucket/job.lock", backend).open("xb")
    second = _gs("gs://bucket/job.lock", backend).open("xb")
    first.write(b"worker1")
    second.write(b"worker2")
    first.close()
    with pytest.raises(FileExistsError):
        second.close()
    assert bucket.objects["job.lock"] == b"worker1"


def test_mkdir_race_is_file_exists():
    backend, bucket = _bucket_with()
    bucket.upload_errors.append(_PreconditionFailed("412"))
    with pytest.raises(FileExistsError):
        _gs("gs://bucket/d", backend).mkdir()


def test_rplus_writes_land_and_read_stream_is_read_only():
    import io

    backend, bucket = _bucket_with(**{"a.txt": b"hello"})
    p = _gs("gs://bucket/a.txt", backend)
    with p.open("rb") as f:
        with pytest.raises(io.UnsupportedOperation):
            f.write(b"x")
    with p.open("r+b") as f:
        assert f.read() == b"hello"
    assert bucket.uploads == []  # unmodified: nothing uploaded
    with p.open("r+b") as f:
        f.write(b"J")
    assert bucket.objects["a.txt"] == b"Jello"


class _Abort(Exception):
    pass


@pytest.mark.parametrize("spooled", [False, True], ids=["memory", "temp-file"])
@pytest.mark.parametrize("mode, data", [("wb", b"part"), ("w", "part")])
def test_a_write_block_that_raised_uploads_nothing(monkeypatch, mode, data, spooled):
    if spooled:
        monkeypatch.setattr(store.SpoolUploadStream, "spool_size", 2)
    backend, bucket = _bucket_with(**{"old": b"OLD"})
    for key in ("old", "new"):
        with pytest.raises(_Abort):
            with _gs(f"gs://bucket/{key}", backend).open(mode) as f:
                f.write(data)
                raise _Abort
    assert bucket.objects == {"old": b"OLD"}
    assert bucket.uploads == []


@pytest.mark.parametrize("mode, data", [("r+b", b"J"), ("r+", "J")])
def test_a_read_modify_write_block_that_raised_uploads_nothing(mode, data):
    backend, bucket = _bucket_with(**{"a.txt": b"hello"})
    with pytest.raises(_Abort):
        with _gs("gs://bucket/a.txt", backend).open(mode) as f:
            f.write(data)
            raise _Abort
    assert bucket.objects == {"a.txt": b"hello"}
    assert bucket.uploads == []


def test_an_exclusive_create_block_that_raised_uploads_nothing():
    backend, bucket = _bucket_with()
    with pytest.raises(_Abort):
        with _gs("gs://bucket/job.lock", backend).open("xb") as f:
            f.write(b"worker1")
            raise _Abort
    assert bucket.objects == {} and bucket.uploads == []


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_a_write_block_that_ended_normally_or_was_closed_uploads_once(mode, data):
    backend, bucket = _bucket_with()
    with _gs("gs://bucket/a", backend).open(mode) as f:
        f.write(data)
    f = _gs("gs://bucket/b", backend).open(mode)
    f.write(data)
    f.close()
    f.close()
    assert bucket.objects == {"a": b"whole", "b": b"whole"}
    assert len(bucket.uploads) == 2


@pytest.mark.parametrize("mode, data", [("wb", b"whole"), ("w", "whole")])
def test_an_upload_that_fails_after_a_normal_block_raises_once_and_is_not_retried(
    mode, data
):
    import gc

    backend, bucket = _bucket_with()
    bucket.upload_errors.append(_ServiceUnavailable("503"))
    with pytest.raises(OSError):
        with _gs("gs://bucket/state.json", backend).open(mode) as f:
            f.write(data)
    gc.collect()
    assert bucket.objects == {} and bucket.uploads == []


# --- rename/move of a prefix directory ---------------------------------------


def test_rename_prefix_directory_falls_back_in_move():
    backend, bucket = _bucket_with(**{"dir/a": b"a", "dir/sub/b": b"b", "k": b"k"})
    src = _gs("gs://bucket/dir", backend)
    with pytest.raises(NotImplementedError):
        src.rename("dir2")
    with pytest.raises(FileNotFoundError):
        _gs("gs://bucket/nope", backend).rename("nope2")
    src.move(_gs("gs://bucket/dir3", backend))
    # copy(recursive=True) creates the directories as markers.
    assert bucket.objects == {
        "dir3/": b"",
        "dir3/a": b"a",
        "dir3/sub/": b"",
        "dir3/sub/b": b"b",
        "k": b"k",
    }


# --- objstore-listing-hides-object-prefix-collision -----------------------------


def test_key_that_is_object_and_prefix_lists_as_the_object():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {"src/logs": b"FILE-CONTENT", "src/logs/2026.txt": b"child", "src/d/x": b"x"}
    )
    listing = dict(_gs("gs://bucket/src", backend)._scandir())
    assert sorted(listing) == ["d", "logs"]
    assert not listing["logs"].is_dir()
    assert listing["logs"].st_size == len(b"FILE-CONTENT")
    assert listing["d"].is_dir()
    assert not _gs("gs://bucket/src/logs", backend).stat().is_dir()


# --- objstore-gs-az-root-always-exists ------------------------------------------


class _MissingBucket(_FakeBucket):
    def list_blobs(self, prefix="", delimiter=None, **_kwargs):
        raise _Missing("bucket not found")


def test_missing_bucket_root_does_not_exist():
    backend = _FakeBackend()
    backend.client_obj.bucket_obj = _MissingBucket()
    root = _gs("gs://typo-bucket/", backend)
    with pytest.raises(FileNotFoundError):
        root.stat()
    assert not root.exists()
    assert not root.is_dir()


def test_existing_bucket_root_is_a_directory_even_when_empty():
    backend = _FakeBackend()
    root = _gs("gs://bucket", backend)
    assert root.stat().is_dir()
    assert backend.client_obj.bucket_obj.list_calls == [""]


def test_bucket_root_permission_error_is_not_a_directory():
    class _Forbidding(_FakeBucket):
        def list_blobs(self, prefix="", delimiter=None, **_kwargs):
            raise _Forbidden("no list permission")

    backend = _FakeBackend()
    backend.client_obj.bucket_obj = _Forbidding()
    with pytest.raises(PermissionError):
        _gs("gs://bucket/", backend).stat()


# --- a listed name is one component inside the directory that listed it ------


def test_listing_skips_names_that_are_not_one_component():
    backend = _FakeBackend()
    backend.client_obj.bucket_obj.objects.update(
        {
            "dir/ok.txt": b"x",
            "dir/..": b"x",
            "dir/.": b"x",
            "dir/../up/f": b"x",
            "dir/./down/f": b"x",
            "dir/sub/f": b"x",
        }
    )
    listing = dict(_gs("gs://bucket/dir", backend)._scandir())
    assert sorted(listing) == ["ok.txt", "sub"]
    assert listing["sub"].is_dir()


class _RawListing(list):
    """What `list_blobs(delimiter="/")` returns: the blobs, and `prefixes`."""

    def __init__(self, blobs, prefixes):
        super().__init__(blobs)
        self.prefixes = prefixes


def test_listing_skips_names_a_server_reports_with_a_separator():
    class _Raw(_FakeBucket):
        def list_blobs(self, prefix="", delimiter=None, **_kwargs):
            blobs = [_FakeBlob(self, name) for name in ("dir/ok.txt", "dir/a/b")]
            self.objects.update({blob.name: b"x" for blob in blobs})
            return _RawListing(blobs, ["dir/sub/", "dir/p/q/", "dir//"])

    backend = _FakeBackend()
    backend.client_obj.bucket_obj = _Raw()
    listing = dict(_gs("gs://bucket/dir", backend)._scandir())
    assert sorted(listing) == ["ok.txt", "sub"]


# --- a prefix is moved, copied and removed as the same set of objects ---------

_ODD_PREFIXES = {
    "an-empty-segment": {"d/x.txt": b"x", "d//y.txt": b"y", "d/sub//z.txt": b"z"},
    "an-object-that-is-also-a-prefix": {
        "d/x.txt": b"x",
        "d/logs": b"log",
        "d/logs/2026.txt": b"c",
        "d/logs/deep/z.txt": b"z",
    },
    "a-slash-name-that-holds-data": {"d/x.txt": b"x", "d/blob/": b"data"},
    "a-dot-segment": {"d/x.txt": b"x", "d/sub/../z.txt": b"z"},
}


@pytest.fixture(params=list(_ODD_PREFIXES))
def odd_prefix(request):
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(_ODD_PREFIXES[request.param])
    bucket.objects["unrelated.txt"] = b"keep"
    return backend, bucket, dict(bucket.objects)


def test_rm_recursive_of_a_prefix_with_hidden_objects_removes_none(odd_prefix):
    backend, bucket, before = odd_prefix
    with pytest.raises(OSError, match="nothing was changed"):
        _gs("gs://bucket/d", backend).rm(recursive=True)
    assert bucket.objects == before
    assert bucket.deleted == []


def test_copy_recursive_of_a_prefix_with_hidden_objects_is_refused(odd_prefix):
    from pathlib_next.mempath import MemPath

    backend, bucket, before = odd_prefix
    target = MemPath("/copied")
    with pytest.raises(OSError, match="nothing was changed"):
        _gs("gs://bucket/d", backend).copy(target, recursive=True)
    assert not target.exists()
    assert bucket.objects == before


def test_move_of_a_prefix_with_hidden_objects_keeps_every_object(odd_prefix):
    backend, bucket, before = odd_prefix
    with pytest.raises(OSError, match="nothing was changed"):
        _gs("gs://bucket/d", backend).move(_gs("gs://bucket/moved", backend))
    assert bucket.objects == before


def test_the_refusal_names_the_objects_a_listing_does_not_show():
    backend = _FakeBackend()
    backend.client_obj.bucket_obj.objects.update(
        {"d/x.txt": b"x", "d//y.txt": b"y", "d/logs": b"l", "d/logs/2026.txt": b"c"}
    )
    with pytest.raises(OSError) as raised:
        _gs("gs://bucket/d", backend).rm(recursive=True)
    assert "'d//y.txt'" in str(raised.value)
    assert "'d/logs/2026.txt'" in str(raised.value)
    assert "'d/x.txt'" not in str(raised.value)


def test_rm_recursive_with_ignore_error_removes_only_the_objects_a_listing_shows():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {"d/": b"", "d/x.txt": b"x", "d//y.txt": b"y", "d/logs": b"l", "d/logs/z": b"z"}
    )
    seen = []
    _gs("gs://bucket/d", backend).rm(
        recursive=True, ignore_error=lambda err, path: seen.append(err) or True
    )
    assert len(seen) == 1 and "'d//y.txt'" in str(seen[0])
    assert sorted(bucket.objects) == ["d//y.txt", "d/logs/z"]


def test_move_of_a_prefix_a_listing_shows_moves_every_object():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update(
        {"d/": b"", "d/x.txt": b"x", "d/sub/a.txt": b"a", "d/empty/": b""}
    )
    _gs("gs://bucket/d", backend).move(_gs("gs://bucket/moved", backend))
    assert sorted(bucket.objects) == [
        "moved/",
        "moved/empty/",
        "moved/sub/",
        "moved/sub/a.txt",
        "moved/x.txt",
    ]


# --- a write or rename never turns a prefix directory into an object ----------


def test_write_onto_a_prefix_directory_is_refused():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update({"d/": b"", "d/x.txt": b"x"})
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket/d", backend).write_bytes(b"clobber")
    assert bucket.objects == {"d/": b"", "d/x.txt": b"x"}
    assert bucket.uploads == []


def test_rename_onto_a_prefix_directory_is_refused():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update({"b.txt": b"b", "dst/keep.txt": b"k"})
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket/b.txt", backend).rename("dst")
    assert bucket.objects == {"b.txt": b"b", "dst/keep.txt": b"k"}


def test_write_and_rename_onto_an_object_still_replace_it():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update({"a.txt": b"a", "b.txt": b"b"})
    _gs("gs://bucket/a.txt", backend).write_bytes(b"new")
    assert bucket.objects["a.txt"] == b"new"
    _gs("gs://bucket/b.txt", backend).rename("a.txt")
    assert bucket.objects == {"a.txt": b"b"}


def test_write_where_the_prefix_cannot_be_listed_still_writes():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj

    def denied(prefix="", delimiter=None, **_kwargs):
        raise _Forbidden("403 list denied")

    bucket.list_blobs = denied
    _gs("gs://bucket/w.txt", backend).write_bytes(b"written")
    assert bucket.objects == {"w.txt": b"written"}


# --- mkdir treats only "not found" as absent ------------------------------------


def test_mkdir_does_not_create_a_marker_when_the_probe_fails():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects["existing.txt"] = b"keep"
    bucket.reload_errors["existing.txt"] = _ServiceUnavailable("503")
    with pytest.raises(OSError) as raised:
        _gs("gs://bucket/existing.txt", backend).mkdir()
    assert not isinstance(raised.value, FileExistsError)
    assert bucket.objects == {"existing.txt": b"keep"}
    assert bucket.uploads == []


def test_mkdir_on_an_object_is_file_exists_and_on_a_missing_path_writes_the_marker():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects["existing.txt"] = b"keep"
    with pytest.raises(FileExistsError):
        _gs("gs://bucket/existing.txt", backend).mkdir()
    _gs("gs://bucket/newdir", backend).mkdir()
    assert sorted(bucket.objects) == ["existing.txt", "newdir/"]


# --- removing a bucket is not supported ----------------------------------------


def test_rmdir_of_the_bucket_root_is_refused():
    backend = _FakeBackend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects["a.txt"] = b"x"
    root = _gs("gs://bucket/", backend)
    with pytest.raises(PermissionError):
        root.rmdir()
    with pytest.raises(PermissionError):
        root.rm()
    assert bucket.objects == {"a.txt": b"x"}
    assert bucket.deleted == []


# --- a failed request is an OSError of the right type, with nothing chained --


class _GatewayTimeout(_ApiError):  # DeadlineExceeded
    code = 504


def _get_failure(error):
    backend, bucket = _bucket_with(**{"a.txt": b"x"})
    bucket.reload_errors["a.txt"] = error
    return _gs("gs://bucket/a.txt", backend)


def test_a_connection_failure_is_a_connection_error_naming_the_path():
    requests_exc = pytest.importorskip("requests.exceptions")
    error = requests_exc.ConnectionError(
        "Max retries: /o/a.txt?X-Goog-Signature=SECRET"
    )
    path = _get_failure(error)
    with pytest.raises(ConnectionError) as info:
        path.stat()
    assert not isinstance(info.value, TimeoutError)
    assert info.value.filename == "gs://bucket/a.txt"
    assert info.value.__cause__ is None
    assert "SECRET" not in str(info.value)
    assert path.exists() is False


@pytest.mark.parametrize("name", ["ReadTimeout", "ConnectTimeout"])
def test_a_timeout_of_the_http_client_is_a_timeout_error(name):
    requests_exc = pytest.importorskip("requests.exceptions")
    path = _get_failure(getattr(requests_exc, name)("slow"))
    with pytest.raises(TimeoutError) as info:
        path.stat()
    assert info.value.filename == "gs://bucket/a.txt"
    assert info.value.__cause__ is None


def test_a_gateway_timeout_reply_is_a_timeout_error():
    path = _get_failure(_GatewayTimeout("504"))
    with pytest.raises(TimeoutError):
        path.stat()


@pytest.mark.parametrize(
    "cause, expected",
    [
        ("connection", ConnectionError),
        ("read timeout", TimeoutError),
        (None, TimeoutError),
        ("unavailable", OSError),
    ],
)
def test_a_retry_that_ran_out_of_time_reports_what_the_last_attempt_failed_with(
    cause, expected
):
    requests_exc = pytest.importorskip("requests.exceptions")
    api_exc = pytest.importorskip("google.api_core.exceptions")
    last = {
        "connection": requests_exc.ConnectionError("refused"),
        "read timeout": requests_exc.ReadTimeout("slow"),
        "unavailable": _ServiceUnavailable("503"),
        None: None,
    }[cause]
    path = _get_failure(api_exc.RetryError("Timeout of 120.0s exceeded", last))
    with pytest.raises(expected) as info:
        path.stat()
    if expected is OSError:
        assert not isinstance(info.value, (ConnectionError, TimeoutError))
    assert info.value.__cause__ is None
    assert path.exists() is False


def test_a_body_cut_short_is_a_connection_reset_naming_the_path(monkeypatch):
    requests_exc = pytest.importorskip("requests.exceptions")
    backend, bucket = _bucket_with(**{"a.txt": b"x"})

    def cut(self, **_kwargs):
        raise requests_exc.ChunkedEncodingError("Connection broken")

    monkeypatch.setattr(_FakeBlob, "download_as_bytes", cut)
    with pytest.raises(ConnectionResetError) as info:
        _gs("gs://bucket/a.txt", backend).read_bytes()
    assert info.value.filename == "gs://bucket/a.txt"
    assert info.value.__cause__ is None


def test_other_exceptions_still_propagate_as_themselves():
    path = _get_failure(KeyError("a bug"))
    with pytest.raises(KeyError):
        path.stat()


# --- timeout and retry reach every call -------------------------------------


def _record_sdk_calls(monkeypatch):
    seen = []

    def wrap(cls, name):
        original = getattr(cls, name)

        def method(self, *args, **kwargs):
            options = {k: kwargs.pop(k) for k in ("timeout", "retry") if k in kwargs}
            seen.append((name, options))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, method)

    for name in ("reload", "delete", "download_as_bytes", "upload_from_string"):
        wrap(_FakeBlob, name)
    for name in ("list_blobs", "copy_blob"):
        wrap(_FakeBucket, name)
    return seen


def test_the_backend_timeout_and_retry_reach_every_sdk_call(monkeypatch):
    class Backend(_FakeBackend):
        def call_options(self):
            return {"timeout": 7, "retry": None}

    seen = _record_sdk_calls(monkeypatch)
    backend = Backend()
    bucket = backend.client_obj.bucket_obj
    bucket.objects.update({"d/a.txt": b"a", "d/b.txt": b"b", "f.txt": b"f"})
    root = _gs("gs://bucket/", backend)
    (root / "f.txt").stat()
    (root / "d").stat()
    list((root / "d").iterdir())
    (root / "f.txt").read_bytes()
    (root / "w.txt").write_bytes(b"w")
    (root / "w.txt").rename("v.txt")
    (root / "v.txt").unlink()
    (root / "m").mkdir()
    (root / "m").rmdir()
    (root / "d").rm(recursive=True)
    assert {name for name, _ in seen} == {
        "reload",
        "delete",
        "download_as_bytes",
        "upload_from_string",
        "list_blobs",
        "copy_blob",
    }
    assert all(options == {"timeout": 7, "retry": None} for _, options in seen)


def test_a_backend_without_options_adds_no_keyword_to_a_call(monkeypatch):
    seen = _record_sdk_calls(monkeypatch)
    backend, _bucket = _bucket_with(**{"f.txt": b"f"})
    _gs("gs://bucket/f.txt", backend).stat()
    assert seen and all(options == {} for _, options in seen)


def test_gs_backend_carries_timeout_and_retry_apart_from_the_client_kwargs(
    fake_storage_module,
):
    from pathlib_next.uri.schemes.gs import GsBackend

    backend = GsBackend(project="p", timeout=(3, 9), retry=None)
    assert backend.call_options() == {"timeout": (3, 9), "retry": None}
    backend.client()
    assert fake_storage_module == [{"project": "p"}]
    assert GsBackend(timeout=5).call_options() == {"timeout": 5}
    assert GsBackend().call_options() == {}


# --- paths that name no object --------------------------------------------------


def test_a_bucket_root_is_a_directory_to_read_or_write():
    backend, bucket = _bucket_with(**{"a.txt": b"x"})
    root = _gs("gs://bucket/", backend)
    with pytest.raises(IsADirectoryError):
        root.read_bytes()
    with pytest.raises(IsADirectoryError):
        root.write_bytes(b"clobber")
    with pytest.raises(IsADirectoryError):
        root.open("r+b")
    assert bucket.list_calls == [] and bucket.uploads == []
    assert bucket.objects == {"a.txt": b"x"}


@pytest.mark.parametrize("uri", ["gs:x", "gs:///x", "gs:///"])
def test_a_path_without_a_bucket_does_not_exist_and_asks_nothing(uri):
    backend, bucket = _bucket_with(**{"x": b"x"})
    path = _gs(uri, backend)
    assert path.exists() is False
    with pytest.raises(FileNotFoundError):
        path.stat()
    with pytest.raises(FileNotFoundError):
        path.read_bytes()
    with pytest.raises(FileNotFoundError):
        path.write_bytes(b"x")
    assert bucket.list_calls == [] and bucket.uploads == []


def test_removing_a_missing_bucket_with_missing_ok_is_not_an_error():
    backend, bucket = _bucket_with()

    def missing(prefix="", **_kwargs):
        raise _Missing("The specified bucket does not exist")

    bucket.list_blobs = missing
    path = _gs("gs://nobucket/x", backend)
    assert path.rm(recursive=True, missing_ok=True) is None
    with pytest.raises(FileNotFoundError):
        path.rm(recursive=True)
    offered = []
    path.rm(
        recursive=True,
        ignore_error=lambda error, where: offered.append(type(error)) or True,
    )
    assert offered == [FileNotFoundError]


def test_renaming_a_missing_path_onto_its_own_name_is_file_not_found():
    backend, bucket = _bucket_with(**{"there.txt": b"x", "d/f": b"x"})
    with pytest.raises(FileNotFoundError):
        _gs("gs://bucket/nope", backend).rename("nope")
    there = _gs("gs://bucket/there.txt", backend)
    assert there.rename("there.txt") == there
    assert _gs("gs://bucket/d", backend).rename("d").key == "d"
    assert bucket.objects == {"there.txt": b"x", "d/f": b"x"}


def test_renaming_a_prefix_directory_reloads_the_key_once(monkeypatch):
    reloads = []
    original = _FakeBlob.reload

    def counted(self, **kwargs):
        reloads.append(self.name)
        return original(self, **kwargs)

    monkeypatch.setattr(_FakeBlob, "reload", counted)
    backend, bucket = _bucket_with(**{"dir/f": b"x"})
    with pytest.raises(NotImplementedError):
        _gs("gs://bucket/dir", backend).rename("other")
    assert reloads == ["dir"]
    assert bucket.list_calls == ["dir/"]


# --- checksum(): the digests the store keeps --------------------------------


def test_the_crc32c_the_fakes_report_is_the_castagnoli_check_value():
    assert objstore_fakes.crc32c(b"123456789") == 0xE3069283


def test_checksum_is_the_md5_or_crc32c_the_store_keeps_and_reads_no_byte():
    backend, bucket = _bucket_with(**{"a.bin": b"content"})
    path = _gs("gs://bucket/a.bin", backend)
    assert path.checksum() == hashlib.md5(b"content").hexdigest()
    assert path.checksum("MD5") == hashlib.md5(b"content").hexdigest()
    assert path.checksum("crc32c") == "%08x" % objstore_fakes.crc32c(b"content")
    assert path.supported_checksums() == frozenset({"md5", "crc32c"})
    assert bucket.downloads == []


def test_checksum_of_a_composite_object_has_no_md5_but_has_its_crc32c():
    backend, bucket = _bucket_with(**{"composed": b"abcdef"})
    bucket.stored["composed"] = {"md5_hash": None}
    path = _gs("gs://bucket/composed", backend)
    with pytest.raises(NotImplementedError):
        path.checksum("md5")
    assert path.checksum("crc32c") == "%08x" % objstore_fakes.crc32c(b"abcdef")


def test_checksum_of_an_object_with_a_content_encoding_is_refused():
    # The service reads such an object back decoded: the stored bytes' digests
    # are not those of the content.
    backend, bucket = _bucket_with(**{"z.gz": b"\x1f\x8b-compressed"})
    bucket.stored["z.gz"] = {"content_encoding": "gzip"}
    path = _gs("gs://bucket/z.gz", backend)
    for algorithm in ("md5", "crc32c"):
        with pytest.raises(NotImplementedError):
            path.checksum(algorithm)


def test_checksum_refuses_an_algorithm_the_store_does_not_keep():
    backend, _bucket = _bucket_with(**{"a.bin": b"content"})
    with pytest.raises(NotImplementedError):
        _gs("gs://bucket/a.bin", backend).checksum("sha256")


def test_checksum_of_a_missing_path_a_directory_and_the_bucket():
    backend, _bucket = _bucket_with(**{"d/a.txt": b"x"})
    with pytest.raises(FileNotFoundError):
        _gs("gs://bucket/nothing", backend).checksum()
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket/d", backend).checksum()
    with pytest.raises(IsADirectoryError):
        _gs("gs://bucket", backend).checksum()


# --- copy(): one copy call in the store -------------------------------------


def test_copy_of_a_file_is_one_copy_call_and_reads_nothing():
    backend, bucket = _bucket_with(**{"a.txt": b"content"})
    _gs("gs://bucket/a.txt", backend).copy(_gs("gs://bucket/b.txt", backend))
    assert bucket.objects["b.txt"] == b"content"
    assert bucket.copies == [("a.txt", "b.txt")]
    assert bucket.downloads == [] and bucket.uploads == []


def test_copy_reports_progress_once_with_the_total():
    backend, _bucket = _bucket_with(**{"a.txt": b"content", "empty": b""})
    calls = []
    for name, size in (("a.txt", 7), ("empty", 0)):
        _gs(f"gs://bucket/{name}", backend).copy(
            _gs(f"gs://bucket/copy-{name}", backend),
            progress=lambda path, copied, total: calls.append(
                (path.key, copied, total)
            ),
        )
        assert calls == [(name, size, size)]
        del calls[:]


def test_copy_onto_an_existing_object_needs_overwrite_and_deletes_nothing_first():
    backend, bucket = _bucket_with(**{"a.txt": b"new", "b.txt": b"old"})
    source, target = _gs("gs://bucket/a.txt", backend), _gs(
        "gs://bucket/b.txt", backend
    )
    with pytest.raises(FileExistsError):
        source.copy(target)
    assert bucket.copies == [] and bucket.objects["b.txt"] == b"old"
    source.copy(target, overwrite=True)
    assert bucket.objects["b.txt"] == b"new"
    assert bucket.copies == [("a.txt", "b.txt")] and bucket.deleted == []


def test_copy_onto_itself_or_a_prefix_directory_is_refused_before_the_store_is_asked():
    import errno

    backend, bucket = _bucket_with(**{"a.txt": b"keep", "d/inner": b"inner"})
    source = _gs("gs://bucket/a.txt", backend)
    for overwrite in (False, True):
        with pytest.raises(OSError, match="same file") as info:
            source.copy(_gs("gs://bucket/a.txt", backend), overwrite=overwrite)
        assert info.value.errno == errno.EINVAL
        with pytest.raises(IsADirectoryError):
            source.copy(_gs("gs://bucket/d", backend), overwrite=overwrite)
    assert bucket.copies == []
    assert set(bucket.objects) == {"a.txt", "d/inner"}


def test_a_refused_copy_leaves_an_existing_target_as_it_was():
    backend, bucket = _bucket_with(**{"a.txt": b"new", "b.txt": b"old"})
    bucket.copy_errors["a.txt"] = _Forbidden("403")
    with pytest.raises(PermissionError) as info:
        _gs("gs://bucket/a.txt", backend).copy(
            _gs("gs://bucket/b.txt", backend), overwrite=True
        )
    assert info.value.filename == "gs://bucket/a.txt"
    assert bucket.objects["b.txt"] == b"old" and bucket.deleted == []


def test_a_store_that_does_not_implement_the_copy_is_read_and_written_instead():
    backend, bucket = _bucket_with(**{"a.txt": b"content"})
    bucket.copy_errors["a.txt"] = _NotImplemented("501")
    _gs("gs://bucket/a.txt", backend).copy(_gs("gs://bucket/b.txt", backend))
    assert bucket.objects["b.txt"] == b"content"
    assert bucket.downloads == ["a.txt"] and bucket.uploads == ["b.txt"]


def test_copy_between_two_supplied_backends_streams():
    first, bucket = _bucket_with(**{"a.txt": b"content"})
    second = _FakeBackend()
    _gs("gs://bucket/a.txt", first).copy(_gs("gs://bucket/b.txt", second))
    assert second.client_obj.bucket_obj.objects == {"b.txt": b"content"}
    assert bucket.copies == [] and bucket.downloads == ["a.txt"]


def test_moving_a_prefix_copies_each_object_in_the_store():
    backend, bucket = _bucket_with(**{"d/a": b"a", "d/sub/b": b"b"})
    _gs("gs://bucket/d", backend).move(_gs("gs://bucket/e", backend))
    assert sorted(name for name in bucket.objects if not name.endswith("/")) == [
        "e/a",
        "e/sub/b",
    ]
    assert sorted(bucket.copies) == [("d/a", "e/a"), ("d/sub/b", "e/sub/b")]
    assert bucket.downloads == []


# --- reads and writes stream ------------------------------------------------


def test_open_reads_the_object_in_ranges_of_one_chunk(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": bytes(range(10))})
    with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
        assert stream.read() == bytes(range(10))
    assert bucket.ranges == [("big.bin", 0, 3), ("big.bin", 4, 7), ("big.bin", 8, 11)]


def test_a_stream_of_a_large_object_seeks_by_range(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": bytes(range(10))})
    with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
        assert stream.seekable()
        stream.seek(6)
        assert stream.read(2) == bytes([6, 7])
        stream.seek(-2, 2)
        assert stream.read() == bytes([8, 9])
        stream.seek(1)
        assert stream.read(1) == bytes([1])


def test_open_reads_an_empty_object_and_text(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, _bucket = _bucket_with(
        **{"empty": b"", "t.txt": "héllo\nworld\n".encode()}
    )
    assert _gs("gs://bucket/empty", backend).read_bytes() == b""
    text = _gs("gs://bucket/t.txt", backend).read_text(encoding="utf-8")
    assert text == "héllo\nworld\n"


def test_a_range_that_fails_midway_is_an_oserror(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, _bucket = _bucket_with(**{"big.bin": bytes(range(10))})
    original = _FakeBlob.download_as_bytes

    def failing(self, start=None, end=None, **kwargs):
        if start:
            raise _ServiceUnavailable("503")
        return original(self, start, end, **kwargs)

    monkeypatch.setattr(_FakeBlob, "download_as_bytes", failing)
    with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
        assert stream.read(4) == bytes(range(4))
        with pytest.raises(OSError) as info:
            stream.read(1)
    assert info.value.filename == "gs://bucket/big.bin"
    assert info.value.__cause__ is None


def test_r_plus_changes_an_object_that_spans_several_ranges(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": bytes(range(10))})
    with _gs("gs://bucket/big.bin", backend).open("r+b") as stream:
        stream.seek(5)
        stream.write(b"\xff")
    assert bucket.objects["big.bin"] == bytes([0, 1, 2, 3, 4, 255, 6, 7, 8, 9])


def test_a_write_that_fits_the_spool_is_one_request_and_a_larger_one_is_a_file(
    monkeypatch,
):
    monkeypatch.setattr(store, "SPOOL_SIZE", 8)
    backend, bucket = _bucket_with()
    _gs("gs://bucket/small", backend).write_bytes(b"12345678")
    _gs("gs://bucket/large", backend).write_bytes(b"123456789")
    assert bucket.objects == {"small": b"12345678", "large": b"123456789"}
    assert bucket.uploads == ["small", "large"]
    assert bucket.file_uploads == [("large", 9)]


def test_a_large_exclusive_upload_does_not_replace_an_object(monkeypatch):
    import io

    monkeypatch.setattr(store, "SPOOL_SIZE", 8)
    backend, bucket = _bucket_with(**{"there": b"keep"})
    with pytest.raises(FileExistsError):
        _gs("gs://bucket/there", backend)._upload_file(
            io.BytesIO(b"123456789"), 9, exclusive=True
        )
    assert bucket.objects == {"there": b"keep"}
    with _gs("gs://bucket/new", backend).open("xb") as stream:
        stream.write(b"123456789")
    assert bucket.objects["new"] == b"123456789"


# --- one default backend per process ----------------------------------------


def test_separately_built_paths_share_one_default_client(fake_storage_module):
    first, second = GsPath("gs://one/a.txt"), GsPath("gs://two/b.txt")
    assert first.backend is second.backend
    first.backend.client()
    second.backend.client()
    assert len(fake_storage_module) == 1


def test_a_supplied_backend_is_not_the_shared_one():
    from pathlib_next.uri.schemes.gs import GsBackend

    own = GsBackend()
    path = GsPath("gs://one/a.txt", backend=own)
    assert path.backend is own
    assert path.backend is not GsPath("gs://one/b.txt").backend
    assert (path / "child").backend is own


def test_threads_racing_for_the_default_backend_get_one():
    import threading

    found = []
    barrier = threading.Barrier(8)

    def build():
        barrier.wait()
        found.append(GsPath("gs://one/x").backend)

    threads = [threading.Thread(target=build) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(found) == 8 and len({id(backend) for backend in found}) == 1


def test_a_test_may_build_the_default_backend():
    from pathlib_next.uri.schemes import gs

    assert GsPath("gs://one/a.txt").backend is gs._DEFAULT_BACKEND


def test_the_next_test_starts_without_the_default_backend_of_the_one_before():
    # Follows the test above: the suite forgets the shared backend between tests.
    from pathlib_next.uri.schemes import gs

    assert gs._DEFAULT_BACKEND is None


# --- a read is one version of the object ------------------------------------

OLD = b"0123456789ab"
NEW = b"ZZZZZZZZZZZZ"


def _change_before_read(bucket, name, change, *, nth=2):
    """Run `change()` just before the `nth` request for the content of `name`."""
    seen = []

    def hook(read):
        if read == name:
            seen.append(read)
            if len(seen) == nth:
                change()

    bucket.before_read = hook


def _stale_errno():
    import errno

    return getattr(errno, "ESTALE", errno.EIO)


def test_a_read_across_an_overwrite_raises_and_returns_no_mixed_bytes(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": OLD})
    _change_before_read(
        bucket, "big.bin", lambda: bucket.objects.update(**{"big.bin": NEW})
    )
    path = _gs("gs://bucket/big.bin", backend)
    got = []
    with path.open("rb") as stream:
        got.append(stream.read(4))
        with pytest.raises(OSError) as info:
            stream.read(4)
    assert got == [OLD[:4]]
    error = info.value
    assert not isinstance(error, FileNotFoundError)
    assert error.errno == _stale_errno()
    assert "changed while it was being read" in error.strerror
    assert error.filename == "gs://bucket/big.bin"
    assert error.__cause__ is None


def test_a_read_of_an_object_deleted_midway_is_file_not_found(monkeypatch):
    for answers_412 in (False, True):
        monkeypatch.setattr(store, "READ_CHUNK", 4)
        backend, bucket = _bucket_with(**{"big.bin": OLD})
        bucket.missing_is_412 = answers_412
        _change_before_read(bucket, "big.bin", lambda: bucket.objects.pop("big.bin"))
        with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
            assert stream.read(4) == OLD[:4]
            with pytest.raises(FileNotFoundError):
                stream.read(4)


def test_a_read_pins_the_generation_of_its_first_request(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": OLD})
    generation = bucket.objects.version("big.bin")
    path = _gs("gs://bucket/big.bin", backend)
    with path.open("rb") as stream:
        assert stream.read() == OLD
        stream.seek(5)
        assert stream.read(2) == OLD[5:7]
        stream.seek(-2, 2)
        assert stream.read() == OLD[-2:]
    assert bucket.reads[0] == ("big.bin", 0, None)
    assert {read[2] for read in bucket.reads[1:]} == {generation}
    assert ("big.bin", 4, generation) in bucket.reads


def test_two_opens_are_two_snapshots(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": OLD})
    path = _gs("gs://bucket/big.bin", backend)
    with path.open("rb") as first:
        assert first.read(4) == OLD[:4]
        bucket.objects["big.bin"] = NEW
        with path.open("rb") as second:
            assert second.read() == NEW
        with pytest.raises(OSError) as info:
            first.read()
    assert info.value.errno == _stale_errno()


def test_seeking_to_the_end_asks_the_size_of_the_version_being_read(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": OLD})
    asked = []
    original = _FakeBlob.reload

    def recorded(self, if_generation_match=None, **kwargs):
        asked.append(if_generation_match)
        return original(self, if_generation_match, **kwargs)

    monkeypatch.setattr(_FakeBlob, "reload", recorded)
    generation = bucket.objects.version("big.bin")
    with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
        stream.seek(-3, 2)
        assert stream.read() == OLD[-3:]
    assert asked == [generation]
    # An object replaced by a longer one is not measured as the one being read.
    with _gs("gs://bucket/big.bin", backend).open("rb") as stream:
        bucket.objects["big.bin"] = NEW + NEW
        with pytest.raises(OSError) as info:
            stream.seek(-3, 2)
    assert info.value.errno == _stale_errno()


def test_r_plus_reads_its_initial_content_as_one_version(monkeypatch):
    monkeypatch.setattr(store, "READ_CHUNK", 4)
    backend, bucket = _bucket_with(**{"big.bin": OLD})
    _change_before_read(
        bucket, "big.bin", lambda: bucket.objects.update(**{"big.bin": NEW})
    )
    with pytest.raises(OSError) as info:
        _gs("gs://bucket/big.bin", backend).open("r+b")
    assert info.value.errno == _stale_errno()
    assert bucket.objects["big.bin"] == NEW and bucket.uploads == []
