"""`import pathlib_next` warns once and is `pathapi`: names and submodules are the
same objects. Each case runs in a fresh interpreter, since the warning and the
module aliases come from the import itself."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

SHIM_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
SUBMODULES = (
    "path protocols providers fspath mempath glob sync checksum exceptions "
    "ftp archive http testing"
).split()


def _run(code: str, **env: str) -> dict:
    """Run `code` (it prints one JSON object) with the shim on the path; `env`
    sets variables and an empty value removes one."""
    environ = dict(os.environ)
    paths = [os.path.abspath(SHIM_SRC)]
    if environ.get("PATHAPI_SRC"):
        paths.append(os.path.abspath(environ["PATHAPI_SRC"]))
    if environ.get("PYTHONPATH"):
        paths.append(environ["PYTHONPATH"])
    environ["PYTHONPATH"] = os.pathsep.join(paths)
    for name in ("PATHLIB_NEXT_HTTP_TRANSPORT", "PATHAPI_HTTP_TRANSPORT"):
        environ.pop(name, None)
    for name, value in env.items():
        if value:
            environ[name] = value
    done = subprocess.run(
        [sys.executable, "-W", "always", "-c", textwrap.dedent(code)],
        env=environ,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_importing_warns_once_and_names_pathapi():
    out = _run("""
        import json, warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import pathlib_next
            import pathlib_next.path
            import pathlib_next
        print(json.dumps([[w.category.__name__, str(w.message)] for w in caught]))
        """)
    assert len(out) == 1
    assert out[0][0] == "DeprecationWarning"
    assert "pathapi" in out[0][1]


def test_the_names_are_the_pathapi_objects():
    out = _run("""
        import json, warnings
        warnings.simplefilter("ignore")
        import pathapi, pathlib_next
        from pathlib_next import LocalPath, Path
        from pathlib_next.protocols import Stat
        import pathapi.protocols
        print(json.dumps([
            Path is pathapi.Path,
            LocalPath is pathapi.LocalPath,
            Stat is pathapi.protocols.Stat,
            isinstance(pathapi.LocalPath("."), pathlib_next.Path),
            all(getattr(pathlib_next, n) is getattr(pathapi, n) for n in pathapi.__all__ if n != "__version__"),
        ]))
        """)
    assert out == [True] * 5


@pytest.mark.parametrize("name", SUBMODULES)
def test_a_submodule_is_the_pathapi_module(name):
    out = _run(f"""
        import json, warnings, importlib
        warnings.simplefilter("ignore")
        old = importlib.import_module("pathlib_next.{name}")
        new = importlib.import_module("pathapi.{name}")
        print(json.dumps([old is new, old.__name__]))
        """)
    assert out == [True, f"pathapi.{name}"]


def test_a_nested_submodule_and_an_import_from_resolve():
    out = _run("""
        import json, warnings
        warnings.simplefilter("ignore")
        from pathlib_next.http.testing import MODULES
        import pathapi.http.testing as new
        import pathlib_next.http
        print(json.dumps([MODULES is new.MODULES, pathlib_next.http.testing is new]))
        """)
    assert out == [True, True]


def test_an_unknown_submodule_is_not_found():
    out = _run("""
        import json, warnings
        warnings.simplefilter("ignore")
        try:
            import pathlib_next.uri
        except ModuleNotFoundError as error:
            print(json.dumps(error.name))
        """)
    assert out == "pathlib_next.uri"


def test_importing_loads_no_more_than_pathapi_does():
    code = """
        import json, sys, warnings
        warnings.simplefilter("ignore")
        import {name}
        print(json.dumps(sorted(m for m in ("ftplib", "ssl") if m in sys.modules)))
        """
    assert _run(code.format(name="pathlib_next")) == _run(code.format(name="pathapi"))
    assert _run(code.format(name="pathapi")) == []


def test_the_version_is_the_shims():
    out = _run("""
        import json, warnings
        warnings.simplefilter("ignore")
        import pathlib_next
        print(json.dumps(pathlib_next.__version__))
        """)
    assert out == "0.10.1"


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"PATHLIB_NEXT_HTTP_TRANSPORT": "old"}, "old"),
        (
            {"PATHLIB_NEXT_HTTP_TRANSPORT": "old", "PATHAPI_HTTP_TRANSPORT": "new"},
            "new",
        ),
        ({"PATHAPI_HTTP_TRANSPORT": "new"}, "new"),
        ({}, None),
    ],
)
def test_the_old_transport_variable_is_copied_only_when_the_new_is_unset(env, expected):
    out = _run(
        """
        import json, os, warnings
        warnings.simplefilter("ignore")
        import pathlib_next
        print(json.dumps(os.environ.get("PATHAPI_HTTP_TRANSPORT")))
        """,
        **env,
    )
    assert out == expected
