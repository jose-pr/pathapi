"""Regression lock-in for currently-correct behavior (safety net).

These are the snippets from README.md's Quick start that don't touch the
network, plus the no-dependency contract: `pip install pathapi` installs
nothing else, so the package and everything it ships must import and work with
the standard library alone.
"""

import ast
import importlib.util
import os
import pathlib
import subprocess
import sys
import sysconfig
import textwrap

import pytest

import pathapi
from pathapi import Path, glob
from pathapi.mempath import MemPath

PACKAGE = pathlib.Path(pathapi.__file__).resolve().parent

#: Third-party packages a Python environment commonly holds. Blocking them in a
#: child interpreter shows that nothing the package imports needs one.
BLOCKED = (
    "uritools",
    "netimps",
    "requests",
    "paramiko",
    "asyncssh",
    "boto3",
    "botocore",
    "duho",
)


def _run_python(code: str, *, block_third_party: bool = False) -> str:
    """Run `code` in a fresh interpreter (clean `sys.modules`), return stdout.
    `block_third_party` hides the packages of `BLOCKED` first, as an install
    without them would."""
    code = textwrap.dedent(code)
    if block_third_party:
        blocked = "".join(f"sys.modules[{name!r}] = None\n" for name in BLOCKED)
        code = "import sys\n" + blocked + code
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_import_bare():
    import pathapi  # noqa: F401


def test_the_package_and_its_providers_import_and_work_with_third_party_packages_blocked():
    out = _run_python(
        """
        import pathapi
        from pathapi import LocalPath, Path, glob, sync
        from pathapi.mempath import MemPath
        import pathapi.archive, pathapi.ftp, pathapi.http

        assert not hasattr(pathapi, "UriPath")
        path = MemPath("a") / "b.txt"
        path.parent.mkdir(parents=True)
        path.write_text("x")
        print(path.read_text(), isinstance(Path("."), LocalPath))
        """,
        block_third_party=True,
    )
    assert out.split() == ["x", "True"]


def test_importing_the_root_loads_no_third_party_package():
    out = _run_python("""
        import sys
        import pathapi
        print(sorted(m for m in %r if m in sys.modules))
        """ % (BLOCKED,))
    assert out.split("\n")[0] == "[]"


def _is_standard_library(name: str) -> bool:
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return name in names
    spec = importlib.util.find_spec(name)
    if spec is None:
        return False
    if spec.origin in ("built-in", "frozen") or spec.origin is None:
        return True
    origin = os.path.normcase(spec.origin)
    paths = sysconfig.get_paths()
    roots = [
        paths["stdlib"],
        paths["platstdlib"],
        os.path.join(sys.base_prefix, "DLLs"),
    ]
    return any(origin.startswith(os.path.normcase(root)) for root in roots)


def _guarded_imports(tree):
    """The nodes inside a `try` whose `except` catches `ImportError`: an
    import the package falls back from, not one it needs."""
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = {
            name.id
            for handler in node.handlers
            for name in ast.walk(handler.type or ast.Constant(None))
            if isinstance(name, ast.Name)
        }
        if caught & {"ImportError", "ModuleNotFoundError"}:
            for statement in node.body:
                guarded.update(map(id, ast.walk(statement)))
    return guarded


def test_no_module_of_the_package_needs_anything_outside_the_standard_library():
    # The testing helpers import pytest, which they require and document.
    skipped = (PACKAGE / "testing", PACKAGE / "http" / "testing")
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if any(directory in path.parents for directory in skipped):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = _guarded_imports(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                tops = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                tops = [(node.module or "").split(".")[0]]
            else:
                continue
            for top in tops:
                if top != "pathapi" and not _is_standard_library(top):
                    found.setdefault(top, set()).add(
                        path.relative_to(PACKAGE).as_posix()
                    )
    assert found == {}


def test_readme_local_path():
    local_path = Path("./my_folder")
    assert isinstance(local_path, pathapi.LocalPath)


def test_example_mempath_roundtrip(tmp_path):
    mempath = MemPath("test/test3") / "subpath"
    mempath.parent.mkdir(parents=True, exist_ok=True)
    mempath.write_text("test")
    check = mempath.read_text()
    assert check == "test"
    mempath.parent.rm(recursive=True)


def test_example_glob_local(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("")
    pattern = Path(f"{tmp_path.as_posix()}/**/*.py")
    found = list(glob.glob(pattern, recursive=True))
    assert len(found) == 2
