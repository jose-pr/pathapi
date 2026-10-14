"""The public surface: what each module exports, the version, and the
constructors as a type checker reads them."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import pathlib_next

PACKAGE = pathlib.Path(pathlib_next.__file__).resolve().parent
HEADER = PACKAGE / "AGENTS.md"

# Each module's exports, as the shipped header documents them.
EXPORTS = {
    "pathlib_next": {
        "ArchivePath",
        "BinaryOpen",
        "Chmod",
        "FTPPath",
        "FileStat",
        "FsPathLike",
        "LocalPath",
        "MemPath",
        "NonRelativePatternError",
        "Path",
        "PathError",
        "PathLike",
        "Pathname",
        "PosixPathname",
        "PurePathLike",
        "Stat",
        "TarPath",
        "WindowsPathname",
        "ZipPath",
        "__version__",
        "glob",
        "sync",
    },
    "pathlib_next.path": {
        "FsPathLike",
        "Path",
        "PathLike",
        "Pathname",
        "PurePathLike",
    },
    "pathlib_next.fspath": {"LocalPath", "PosixPathname", "WindowsPathname"},
    "pathlib_next.archive": {
        "ArchivePath",
        "LinkTarget",
        "MAX_LINKS",
        "MEMBER_SPOOL_BYTES",
        "TarPath",
        "ZipPath",
        "detect_format",
        "make_archive",
        "unpack_archive",
    },
    "pathlib_next.ftp": {
        "BaseFTPBackend",
        "DEFAULT_PORT",
        "DEFAULT_TIMEOUT",
        "FTPBackend",
        "FTPEndpoint",
        "FTPPath",
        "IDLE_PROBE_SECONDS",
    },
    "pathlib_next.protocols": {
        "BinaryOpen",
        "Chmod",
        "Chown",
        "Copy",
        "Create",
        "FileStatLike",
        "Hardlink",
        "Identity",
        "Listing",
        "Move",
        "Named",
        "NativeChecksum",
        "Owned",
        "Readlink",
        "Remove",
        "Resolve",
        "Stat",
        "Symlink",
        "Utime",
        "capabilities",
        "supports",
    },
    "pathlib_next.protocols.fs": {
        "Chmod",
        "Chown",
        "Create",
        "FileStatLike",
        "Owned",
        "Stat",
        "Utime",
    },
    "pathlib_next.protocols.io": {"BinaryOpen"},
    "pathlib_next.protocols.checksum": {"NativeChecksum"},
    "pathlib_next.protocols.pure": {"Named"},
    "pathlib_next.protocols.tree": {"Listing", "Remove"},
    "pathlib_next.protocols.links": {"Hardlink", "Readlink", "Symlink"},
    "pathlib_next.protocols.resolve": {"Resolve"},
    "pathlib_next.protocols.transfer": {"Copy", "Identity", "Move"},
    "pathlib_next.providers": {
        "CommitOnClose",
        "DiscardOnError",
        "LRU",
        "OfferedOnce",
        "TreePath",
        "UNCHANGED",
        "as_error_handler",
        "as_error_predicate",
        "parse_mode",
        "parse_owner",
        "primitive",
        "check_follow",
        "contains",
        "copy_error_predicate",
        "derived_from",
        "is_safe_child_name",
        "is_windows_flavoured",
        "name_stem",
        "name_suffix",
        "name_suffixes",
        "notimplemented",
    },
    "pathlib_next.checksum": {"md5", "native", "sha256", "stream"},
    "pathlib_next.exceptions": {"NonRelativePatternError", "PathError"},
    "pathlib_next.mempath": {"MemBytesIO", "MemFile", "MemPath", "MemPathBackend"},
    "pathlib_next.sync": {"PathAndStat", "PathSyncer", "SyncEvent"},
    "pathlib_next.testing": {
        "DIRECTORY_ERRORS",
        "FIXTURE_TREE",
        "NOT_EMPTY_ERRNOS",
        "PathContract",
        "PurePathContract",
        "ReadPathContract",
        "populate_fixture_tree",
    },
    "pathlib_next.http": {
        "DEFAULT_TIMEOUT",
        "HTTPClient",
        "HTTPResponse",
        "HTTPStatusError",
        "HTTPTransport",
        "Headers",
        "MAX_REPLY_BYTES",
        "PreparedRequest",
        "TRANSPORT_ENV",
        "TRANSPORT_GROUP",
        "default_transport",
        "drop_body",
        "get_transport",
        "is_redirect",
        "json_body",
        "os_error",
        "peer_text",
        "prepare_request",
        "raise_for_status",
        "read_limited",
        "server_size",
        "split_userinfo",
        "translate_errors",
        "transport_names",
    },
    "pathlib_next.glob": {
        "RECURSIVE",
        "full_match",
        "glob",
        "parse_pattern",
        "select",
    },
}

# Every module of the core that is public: it declares `__all__`. Every other
# module of the core starts with an underscore.
CORE_PUBLIC = {
    "pathlib_next",
    "pathlib_next.archive",
    "pathlib_next.checksum",
    "pathlib_next.exceptions",
    "pathlib_next.fspath",
    "pathlib_next.ftp",
    "pathlib_next.glob",
    "pathlib_next.http",
    "pathlib_next.mempath",
    "pathlib_next.path",
    "pathlib_next.protocols",
    "pathlib_next.protocols.checksum",
    "pathlib_next.protocols.fs",
    "pathlib_next.protocols.io",
    "pathlib_next.protocols.links",
    "pathlib_next.protocols.pure",
    "pathlib_next.protocols.resolve",
    "pathlib_next.protocols.transfer",
    "pathlib_next.protocols.tree",
    "pathlib_next.providers",
    "pathlib_next.sync",
    "pathlib_next.testing",
}


# Importable and documented, but testing support: outside the compatibility
# promise, never imported by the package it tests, and not exported by it.
TESTING_SUPPORT = {"pathlib_next.http.testing"}


def _core_modules():
    """Dotted names of the core's modules, private ones included."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        if "__pycache__" in parts or parts[:3] == ("http", "testing", "suite"):
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.add(".".join(("pathlib_next", *parts)))
    return found


def test_every_public_module_of_the_core_declares_all_and_each_name_exists():
    for module in sorted(CORE_PUBLIC | TESTING_SUPPORT):
        imported = importlib.import_module(module)
        names = getattr(imported, "__all__", None)
        assert names is not None, f"{module} has no __all__"
        assert len(names) == len(set(names)), f"{module} lists a name twice"
        missing = [name for name in names if not hasattr(imported, name)]
        assert missing == [], f"{module} lists {missing}, which it does not have"


def test_every_other_module_of_the_core_is_private():
    public = {
        module
        for module in _core_modules()
        if not any(part.startswith("_") for part in module.split("."))
    }
    assert public == CORE_PUBLIC | TESTING_SUPPORT


@pytest.mark.parametrize("module", sorted(EXPORTS))
def test_module_declares_exactly_the_documented_exports(module):
    imported = importlib.import_module(module)
    declared = set(imported.__all__)
    expected = set(EXPORTS[module])
    assert declared == expected
    assert len(imported.__all__) == len(declared)
    for name in declared:
        assert hasattr(imported, name), name


@pytest.mark.parametrize("module", sorted(EXPORTS))
def test_documented_names_stay_importable_from_where_they_are_documented(module):
    names = EXPORTS[module]
    imported = importlib.import_module(module)
    missing = sorted(name for name in names if not hasattr(imported, name))
    assert missing == []


def test_star_import_of_the_root_publishes_only_the_declared_names():
    namespace: dict = {}
    exec("from pathlib_next import *", namespace)
    published = {name for name in namespace if name != "__builtins__"}
    assert published == set(pathlib_next.__all__)
    for leaked in ("P", "PN", "annotations", "fspath", "path", "protocols"):
        assert leaked not in published


def test_names_that_were_never_documented_stay_importable_by_name():
    from pathlib_next.path import P, PN  # noqa: F401


def _header_import_lines():
    text = HEADER.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
    for block in blocks:
        for line in block.splitlines():
            if re.match(r"(from \S+ import |import )", line):
                yield line


def test_the_import_lines_of_the_header_run():
    lines = list(_header_import_lines())
    assert any("pathlib_next.testing" in line for line in lines)
    for line in lines:
        exec(line, {})


def test_version_is_the_installed_distribution_version():
    assert isinstance(pathlib_next.__version__, str)
    try:
        installed = importlib.metadata.version("pathlib_next")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("the package is not installed")
    assert pathlib_next.__version__ == installed


def test_version_of_a_source_tree_that_is_not_installed():
    code = (
        "import importlib.metadata as m\n"
        "def missing(name):\n"
        "    raise m.PackageNotFoundError(name)\n"
        "m.version = missing\n"
        "import pathlib_next\n"
        "print(pathlib_next.__version__)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(PACKAGE.parent))
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == "0+unknown"


def _is_trivial_body(function: ast.FunctionDef) -> bool:
    """mypy's rule for a body that makes a protocol member abstract: nothing
    but a docstring, `...`, `pass` or `raise NotImplementedError`."""
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return True
    if len(body) > 1:
        return False
    (statement,) = body
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr):
        return isinstance(statement.value, ast.Constant) and (
            statement.value.value is Ellipsis
        )
    if isinstance(statement, ast.Raise) and statement.exc is not None:
        exc = statement.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _protocol_stubs():
    for relative in (
        "path/_pure.py",
        "protocols/fs.py",
        "protocols/io.py",
        "protocols/checksum.py",
        "protocols/tree.py",
        "protocols/links.py",
        "protocols/resolve.py",
        "protocols/transfer.py",
    ):
        tree = ast.parse((PACKAGE / relative).read_text(encoding="utf-8"))
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            bases = {
                getattr(base, "attr", getattr(base, "id", None)) for base in cls.bases
            }
            if "Protocol" not in bases:
                continue
            for function in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                decorators = {
                    getattr(d, "attr", getattr(d, "id", None))
                    for d in function.decorator_list
                }
                if "abstractmethod" in decorators or "_abstract" in decorators:
                    continue
                yield f"{relative}:{cls.name}.{function.name}", function


def test_no_protocol_stub_has_a_body_a_type_checker_reads_as_abstract():
    stubs = list(_protocol_stubs())
    assert len(stubs) > 10
    trivial = [label for label, function in stubs if _is_trivial_body(function)]
    assert trivial == []


CONSUMER = """\
from pathlib_next import FileStat, LocalPath, Path, Pathname, checksum, glob, sync
from pathlib_next.mempath import MemPath
from pathlib_next.protocols import BinaryOpen, Listing, Stat

bare = Path("x")
local = LocalPath("x")
memory = MemPath("/a")
stat = FileStat(st_size=1, is_dir=False)
joined = bare / "y"


class Composed(Pathname, Listing, Stat, BinaryOpen):
    __slots__ = ()


composed = Composed()
found = list(glob.glob(composed))
digest: str = checksum.md5(composed)
sync.PathSyncer().sync(composed, memory)
"""


def _consumer(tmp_path):
    # Outside any dot-directory: pyright silently skips those.
    directory = tmp_path / "consumer"
    directory.mkdir()
    (directory / "consumer.py").write_text(CONSUMER, encoding="utf-8")
    return directory


def test_mypy_accepts_the_constructors(tmp_path, monkeypatch):
    api = pytest.importorskip("mypy.api")
    directory = _consumer(tmp_path)
    monkeypatch.setenv("MYPYPATH", str(PACKAGE.parent))
    out, err, status = api.run(
        [
            "--follow-imports=silent",
            "--no-incremental",
            "--cache-dir=" + str(tmp_path / "mypy-cache"),
            str(directory / "consumer.py"),
        ]
    )
    assert status == 0, out + err


@pytest.mark.allow_program("pyright", "basedpyright")
def test_pyright_accepts_the_constructors(tmp_path):
    checker = shutil.which("pyright") or shutil.which("basedpyright")
    if checker is None:
        pytest.skip("pyright is not installed")
    directory = _consumer(tmp_path)
    (directory / "pyrightconfig.json").write_text(
        json.dumps(
            {"typeCheckingMode": "standard", "extraPaths": [str(PACKAGE.parent)]}
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [checker, "--pythonpath", sys.executable, "consumer.py"],
        capture_output=True,
        text=True,
        cwd=directory,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_module_postpones_the_evaluation_of_annotations():
    # A bare `X | Y` in an annotation must not be evaluated on Python 3.9.
    without = sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        # The transport suite's own test files, copied into a wheel.
        if path.relative_to(PACKAGE).parts[:3] != ("http", "testing", "suite")
        if "from __future__ import annotations" not in path.read_text(encoding="utf-8")
    )
    assert without == []
