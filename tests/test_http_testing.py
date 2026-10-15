"""`pathapi.http.testing`: the transport suite that a distribution
providing a transport runs, and the files that the wheel carries for it."""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import zipfile

import pytest

import pathapi
from pathapi.http import testing as http_testing

REPO = pathlib.Path(__file__).resolve().parent.parent
SUITE = REPO / "tests" / "transport"
PER_TRANSPORT = re.compile(
    r"\b(http_transport_kind|http_transport|on_current_transport|make_transport|"
    r"current_kind)\b"
)
# Test files of the suite that check one implementation (the built-in
# transport's pool and proxy handling, the registry, the helper that a new
# transport starts from) and so are not run over another transport.
NOT_PER_TRANSPORT = {
    "test_client_deadline.py",
    "test_client_urls.py",
    "test_transport_pool.py",
    "test_transport_prepare.py",
    "test_transport_proxy.py",
    "test_transport_registry.py",
    "test_transport_tls.py",
}

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = pytest.importorskip("tomli")


def _project():
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _local_imports(path):
    names = {p.stem for p in SUITE.glob("*.py")}
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return {name + ".py" for name in found & names}


def test_the_modules_are_those_that_use_the_transport_fixtures():
    used = sorted(
        path.name
        for path in SUITE.glob("test_*.py")
        if PER_TRANSPORT.search(path.read_text(encoding="utf-8"))
    )
    assert list(http_testing.MODULES) == used


def test_the_modules_left_out_are_the_ones_that_check_one_implementation():
    left_out = {p.name for p in SUITE.glob("test_*.py")} - set(http_testing.MODULES)
    assert left_out == NOT_PER_TRANSPORT


def test_the_helpers_are_the_files_the_modules_import():
    needed = set()
    pending = [*http_testing.MODULES, "conftest.py"]
    while pending:
        name = pending.pop()
        for imported in _local_imports(SUITE / name) - needed:
            needed.add(imported)
            pending.append(imported)
    assert needed | {"conftest.py"} == set(http_testing.HELPERS)
    assert not needed & set(http_testing.MODULES)


def test_the_wheel_is_told_to_carry_exactly_the_suite():
    wanted = {
        f"tests/transport/{name}": f"pathapi/http/testing/suite/{name}"
        for name in (*http_testing.MODULES, *http_testing.HELPERS)
    }
    force = _project()["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    shipped = {k: v for k, v in force.items() if v.startswith("pathapi/http/testing/")}
    assert shipped == wanted
    assert all((REPO / source).is_file() for source in wanted)


@pytest.mark.skipif(
    importlib.util.find_spec("hatchling") is None, reason="hatchling is not installed"
)
def test_the_built_wheel_carries_the_suite_and_the_package_around_it(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hatchling",
            "build",
            "-t",
            "wheel",
            "-d",
            str(tmp_path),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    (wheel,) = tmp_path.glob("pathapi-*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    for name in (*http_testing.MODULES, *http_testing.HELPERS):
        assert f"pathapi/http/testing/suite/{name}" in names
    assert {
        "pathapi/http/testing/__init__.py",
        "pathapi/http/testing/__main__.py",
        "pathapi/http/__init__.py",
        "pathapi/AGENTS.md",
        "pathapi/py.typed",
    } <= names
    assert not any(
        n.startswith("pathapi/http/testing/suite/test_transport_p") for n in names
    )


def test_a_checkout_runs_the_suite_from_its_transport_directory():
    if REPO / "src" not in pathlib.Path(http_testing.__file__).resolve().parents:
        pytest.skip("the package is installed, not a checkout")
    assert http_testing.suite_directory() == SUITE


def _child(code):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [
                str(pathlib.Path(pathapi.__file__).resolve().parent.parent),
                env.get("PYTHONPATH"),
            ],
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_importing_the_suite_module_does_not_import_pytest():
    assert (
        _child(
            "import sys; import pathapi.http.testing; "
            "print(sorted({'pytest'} & set(sys.modules)))"
        )
        == "[]"
    )


def test_importing_the_client_package_imports_neither_the_suite_nor_pytest():
    assert (
        _child(
            "import sys; import pathapi.http; "
            "print(sorted(m for m in sys.modules "
            "if m == 'pytest' or m.startswith('pathapi.http.testing')))"
        )
        == "[]"
    )


def test_the_path_contract_suite_still_loads_neither_ssl_nor_http_client():
    assert (
        _child(
            "import sys; import pathapi.testing; "
            "print(sorted({'ssl', 'http.client'} & set(sys.modules)))"
        )
        == "[]"
    )


def test_the_suite_module_is_not_exported_by_the_package_or_the_root():
    import pathapi.http

    assert "testing" not in pathapi.http.__all__
    assert "testing" not in pathapi.__all__
    assert "http" not in pathapi.__all__


def test_nothing_outside_the_suite_module_imports_it():
    package = pathlib.Path(pathapi.__file__).resolve().parent
    own = package / "http" / "testing"
    importers = []
    for path in package.rglob("*.py"):
        if own in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [base] + [f"{base}.{alias.name}" for alias in node.names]
                if node.level and path.parent == package / "http":
                    names += [f"pathapi.http.{base}"]
                    names += [f"pathapi.http.{alias.name}" for alias in node.names]
            if any(
                n == "pathapi.http.testing" or n.startswith("pathapi.http.testing.")
                for n in names
            ):
                importers.append(path.name)
    assert importers == []


def test_the_module_says_it_is_not_stable_api():
    assert "not part of the stable API" in http_testing.__doc__
    header = (pathlib.Path(pathapi.__file__).resolve().parent / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "### Testing support: the HTTP transport suite" in header
    assert "may change in any release" in header


def _collect(directory):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [
                str(pathlib.Path(pathapi.__file__).resolve().parent.parent),
                env.get("PYTHONPATH"),
            ],
        )
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pathapi.http.testing",
            "--http-transports",
            "builtin",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=directory,
    )


def test_an_option_and_its_value_before_the_files_reach_the_suite_from_any_directory(
    tmp_path,
):
    result = _collect(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_transport_send.py::" in result.stdout


def test_the_suite_is_listed_by_the_command_and_runs_once_per_transport():
    result = _collect(REPO)
    assert result.returncode == 0, result.stdout + result.stderr
    listed = result.stdout.splitlines()
    assert any(
        "test_client_redirects.py::" in line and line.endswith("[builtin]")
        for line in listed
    )
    assert not any("test_transport_pool.py" in line for line in listed)


def _arguments_given_to_pytest(monkeypatch, tmp_path, arguments):
    seen = []
    monkeypatch.setattr(pytest, "main", lambda args: seen.append(list(args)) or 0)
    monkeypatch.setattr(http_testing, "suite_directory", lambda: tmp_path)
    assert http_testing.run(arguments) == 0
    return seen[0]


def test_run_roots_pytest_at_the_suite_directory(monkeypatch, tmp_path):
    given = _arguments_given_to_pytest(monkeypatch, tmp_path, ["-q"])
    assert f"--rootdir={tmp_path}" in given
    assert given[-1] == "-q"


@pytest.mark.parametrize(
    "option", [["--rootdir=/elsewhere"], ["--rootdir", "/elsewhere"]]
)
def test_run_keeps_a_rootdir_the_caller_gave(monkeypatch, tmp_path, option):
    given = _arguments_given_to_pytest(monkeypatch, tmp_path, option)
    assert [a for a in given if a.startswith("--rootdir")] == option[:1]
    assert given[-len(option) :] == option
