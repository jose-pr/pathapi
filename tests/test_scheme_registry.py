"""The scheme registry: defining a `UriPath` subclass that lists a scheme is the
one way to register it, a name is held by one class, and the registry can be
listed and queried.

Cases that need a process in which no scheme module has been imported yet, or
a third-party distribution on the path, run in a fresh interpreter.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("uritools")
pytest.importorskip("netimps")

import pathlib_next
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.data import DataUri
from pathlib_next.uri.schemes.file import FileUri

# The tree this process imports, so a child tests the same code.
_SRC = pathlib.Path(pathlib_next.__file__).resolve().parent.parent

_PRELUDE = """
import socket, sys

def _refuse(*args, **kwargs):
    raise RuntimeError("network used")

socket.socket.connect = _refuse
socket.getaddrinfo = _refuse
"""


def _run(code: str, *extra_paths, blocked=()) -> subprocess.CompletedProcess:
    script = _PRELUDE + "".join(f"sys.modules[{n!r}] = None\n" for n in blocked)
    script += textwrap.dedent(code)
    env = dict(os.environ)
    # The code under test first, then any plugin distribution.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_SRC), *map(str, extra_paths), *filter(None, [env.get("PYTHONPATH")])]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def plugins(tmp_path):
    """A distribution on the path whose entry points name the built-in `sftp:`
    and `file:` (module `regplug_shadow`) and a scheme of its own, `regextra:`
    (module `regplug_extra`)."""
    dist = tmp_path / "regplug-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_bytes(
        b"Metadata-Version: 2.1\nName: regplug\nVersion: 1.0\n"
    )
    (dist / "entry_points.txt").write_bytes(
        b"[pathlib_next.schemes]\n"
        b"sftp = regplug_shadow:Shadow\n"
        b"file = regplug_shadow:Shadow\n"
        b"regextra = regplug_extra:Extra\n"
    )
    (tmp_path / "regplug_shadow.py").write_bytes(
        b"from pathlib_next.uri import UriPath\n"
        b"class Shadow(UriPath):\n"
        b"    __SCHEMES = ('sftp', 'file')\n"
    )
    (tmp_path / "regplug_extra.py").write_bytes(
        b"from pathlib_next.uri import UriPath\n"
        b"class Extra(UriPath):\n"
        b"    __SCHEMES = ('regextra',)\n"
    )
    return tmp_path


# --- a name is held by one class -----------------------------------------------------


def test_a_second_class_listing_a_scheme_raises_at_definition():
    class First(UriPath):
        __SCHEMES = ("regdup1",)
        __slots__ = ()

    with pytest.raises(ValueError) as refused:

        class Second(UriPath):
            __SCHEMES = ("regdup1",)
            __slots__ = ()

    message = str(refused.value)
    assert "'regdup1'" in message and "already registered" in message
    assert "First" in message and "replace=True" in message
    assert type(UriPath("regdup1://h/x")) is First
    assert UriPath.scheme_class("regdup1") is First


def test_a_class_refused_for_one_name_registers_none_of_its_names():
    class Holder(UriPath):
        __SCHEMES = ("regheld1",)
        __slots__ = ()

    with pytest.raises(ValueError):

        class Greedy(UriPath):
            __SCHEMES = ("regfree1", "regheld1")
            __slots__ = ()

    assert type(UriPath("regfree1://h/x")) is UriPath
    assert "regfree1" not in UriPath.schemes()
    assert UriPath.scheme_class("regheld1") is Holder


def test_replace_true_takes_the_scheme_from_the_class_that_held_it():
    class Original(UriPath):
        __SCHEMES = ("regrep1",)
        __slots__ = ()

    class Replacement(UriPath, replace=True):
        __SCHEMES = ("regrep1",)
        __slots__ = ()

    assert UriPath.scheme_class("regrep1") is Replacement
    assert type(UriPath("regrep1://h/x")) is Replacement
    assert Original is not Replacement


def test_replace_true_on_a_free_name_just_registers_it():
    class Fresh(UriPath, replace=True):
        __SCHEMES = ("regfresh1",)
        __slots__ = ()

    assert type(UriPath("regfresh1://h/x")) is Fresh


def test_a_subclass_sharing_its_parents_name_and_listing_nothing_takes_nothing():
    class Named(UriPath):
        __SCHEMES = ("regsame1",)
        __slots__ = ()

    parent = Named

    class Named(parent):  # noqa: F811 - the same name is the point
        __slots__ = ()

    assert type(UriPath("regsame1://h/x")) is parent
    assert UriPath.scheme_class("regsame1") is parent


def test_reloading_a_module_takes_its_scheme_over_with_the_new_class(
    tmp_path, monkeypatch
):
    source = tmp_path / "regreload_module.py"
    source.write_bytes(textwrap.dedent("""\
            from pathlib_next.uri import UriPath
            class Reloaded(UriPath):
                __SCHEMES = ('regreload1',)
                __slots__ = ()
            """).encode())
    monkeypatch.syspath_prepend(str(tmp_path))
    import regreload_module

    try:
        first = regreload_module.Reloaded
        assert UriPath.scheme_class("regreload1") is first
        importlib.reload(regreload_module)
        second = regreload_module.Reloaded
        assert second is not first
        assert UriPath.scheme_class("regreload1") is second
        assert type(UriPath("regreload1://h/x")) is second
    finally:
        sys.modules.pop("regreload_module", None)


def test_reloading_a_built_in_scheme_module_keeps_the_scheme_dispatching():
    result = _run("""
        import importlib
        from pathlib_next.uri import UriPath
        import pathlib_next.uri.schemes.data as data

        first = data.DataUri
        importlib.reload(data)
        second = data.DataUri
        print(second is not first)
        print(UriPath.scheme_class("data") is second)
        print(type(UriPath("data:,x")) is second)
        """)
    assert result.stdout.split() == ["True", "True", "True"]


def test_a_class_statement_run_twice_registers_the_second_class():
    def define():
        class Twice(UriPath):
            __SCHEMES = ("regtwice1",)
            __slots__ = ()

        return Twice

    first, second = define(), define()

    assert first is not second
    assert UriPath.scheme_class("regtwice1") is second
    assert type(UriPath("regtwice1://h/x")) is second


def test_a_class_of_the_same_qualified_name_in_another_module_is_still_refused():
    source = (
        "from pathlib_next.uri import UriPath\n"
        "class Twin(UriPath):\n"
        "    __SCHEMES = ('regtwin1',)\n"
        "    __slots__ = ()\n"
    )

    def define(module):
        namespace = {"__name__": module}
        exec(source, namespace)
        return namespace["Twin"]

    first = define("regtwin_module_a")
    assert first.__qualname__ == "Twin" and first.__module__ == "regtwin_module_a"
    with pytest.raises(ValueError, match="regtwin_module_a.Twin"):
        define("regtwin_module_b")
    assert UriPath.scheme_class("regtwin1") is first
    assert define("regtwin_module_a") is not first
    assert UriPath.scheme_class("regtwin1").__module__ == "regtwin_module_a"


@pytest.mark.parametrize("scheme", ["file", "data", "https"])
def test_a_class_cannot_take_a_built_in_scheme_by_definition(scheme):
    before = UriPath.scheme_class(scheme)

    with pytest.raises(ValueError, match=repr(scheme)):

        class Hijack(UriPath):
            __SCHEMES = (scheme, "reghijack1")
            __slots__ = ()

    assert UriPath.scheme_class(scheme) is before
    assert type(UriPath(f"{scheme}://h/x")) is before
    assert "reghijack1" not in UriPath.schemes()


def test_a_built_in_scheme_is_refused_before_its_module_is_imported():
    result = _run("""
        from pathlib_next.uri import UriPath
        try:
            class Hijack(UriPath):
                __SCHEMES = ("sftp",)
        except ValueError as error:
            print("refused:", error)
        print("imported:", "pathlib_next.uri.schemes.sftp" in sys.modules)
        print("class:", UriPath.scheme_class("sftp").__module__)
        """)
    refused, imported, cls = result.stdout.splitlines()
    assert refused.startswith("refused: 'sftp' is a built-in URI scheme")
    assert "replace=True" in refused
    assert imported == "imported: False"
    assert cls == "class: pathlib_next.uri.schemes.sftp"


def test_a_replaced_built_in_scheme_stays_replaced_when_its_module_is_imported():
    result = _run("""
        from pathlib_next.uri import UriPath

        class Mine(UriPath, replace=True):
            __SCHEMES = ("sftp",)
            __slots__ = ()

        import pathlib_next.uri.schemes.sftp as builtin
        print(UriPath.scheme_class("sftp") is Mine)
        print(type(UriPath("sftp://h/x")) is Mine)
        print(builtin.SftpPath is not Mine)
        """)
    assert result.stdout.split() == ["True", "True", "True"]


# --- an entry point never takes a built-in name ---------------------------------------


def test_an_entry_point_named_like_a_built_in_scheme_does_not_displace_it(plugins):
    result = _run(
        """
        from pathlib_next.uri import UriPath
        print(type(UriPath("sftp://u@h/x")).__module__)
        print(type(UriPath("file:///x")).__module__)
        print("regplug_shadow" in sys.modules)
        try:
            import regplug_shadow
        except ValueError as error:
            print("refused:", error)
        """,
        plugins,
    )
    sftp, file, imported, refused = result.stdout.splitlines()
    assert sftp == "pathlib_next.uri.schemes.sftp"
    assert file == "pathlib_next.uri.schemes.file"
    assert imported == "False"
    assert refused.startswith(
        "refused: URI scheme 'sftp' is already registered by "
        "pathlib_next.uri.schemes.sftp.SftpPath"
    )


def test_an_entry_point_for_a_scheme_of_its_own_is_loaded_on_demand(plugins):
    result = _run(
        """
        from pathlib_next.uri import UriPath
        print("regextra" in UriPath.schemes(), "regplug_extra" in sys.modules)
        print(type(UriPath("regextra://h/x")).__name__)
        print(UriPath.scheme_class("regextra").__module__)
        """,
        plugins,
    )
    assert result.stdout.splitlines() == ["True False", "Extra", "regplug_extra"]


# --- listing and querying -------------------------------------------------------------


def test_schemes_lists_every_built_in_name_without_importing_its_module():
    result = _run("""
        from pathlib_next.uri import UriPath
        names = UriPath.schemes()
        print(isinstance(names, tuple), list(names) == sorted(set(names)))
        print(" ".join(sorted(set(names) & {
            "file", "data", "zip", "tar", "archive", "archive+zip", "archive+tar",
            "ftp", "ftps", "http", "https", "dav", "davs", "sftp", "s3", "gs",
            "az", "github", "gitlab", "git", "git+github", "git+gitlab",
        })))
        print([m for m in sys.modules if m.startswith("pathlib_next.uri.schemes.")
               and m.split(".")[3] in {"sftp", "s3", "gs", "az", "ftp", "http"}])
        """)
    shape, names, imported = result.stdout.splitlines()
    assert shape == "True True"
    assert names.split() == sorted(
        "file data zip tar archive archive+zip archive+tar ftp ftps http https "
        "dav davs sftp s3 gs az github gitlab git git+github git+gitlab".split()
    )
    assert imported == "[]"


def test_schemes_lists_an_entry_point_whose_module_is_not_imported(plugins):
    result = _run(
        """
        from pathlib_next.uri import UriPath
        print("regextra" in UriPath.schemes(), "regplug_extra" in sys.modules)
        """,
        plugins,
    )
    assert result.stdout.strip() == "True False"


def test_schemes_lists_a_class_defined_after_the_first_call():
    before = UriPath.schemes()
    assert "reglisted1" not in before

    class Listed(UriPath):
        __SCHEMES = ("reglisted1", "reglisted2")
        __slots__ = ()

    after = UriPath.schemes()
    assert {"reglisted1", "reglisted2"} <= set(after)
    assert set(before) <= set(after)
    assert len(after) == len(set(after)) and list(after) == sorted(after)


def test_scheme_class_returns_the_class_registered_for_a_name():
    assert UriPath.scheme_class("data") is DataUri
    assert UriPath.scheme_class("DATA") is DataUri
    assert UriPath.scheme_class("file") is FileUri
    assert FileUri.scheme_class("data") is DataUri


def test_scheme_class_of_an_unknown_name_names_what_is_registered():
    with pytest.raises(ValueError) as unknown:
        UriPath.scheme_class("regnosuchscheme")
    message = str(unknown.value)
    assert "'regnosuchscheme'" in message
    assert message.endswith(", ".join(UriPath.schemes()))
    assert "sftp" in message and "file" in message


def test_a_scheme_whose_client_library_is_missing_stays_listed_and_fails_on_use():
    result = _run(
        """
        from pathlib_next.uri import UriPath
        names = set(UriPath.schemes())
        print(sorted(names & {"http", "https", "dav", "davs", "github", "gitlab"}))
        print(UriPath.scheme_class("http").__name__)
        try:
            UriPath("http://h/x").stat()
        except ImportError as error:
            print(error)
        """,
        blocked=("requests",),
    )
    names, cls, error = result.stdout.splitlines()
    assert names == str(["dav", "davs", "github", "gitlab", "http", "https"])
    assert cls == "HttpPath"
    assert "pathlib-next[http]" in error


def test_a_built_in_module_that_cannot_be_imported_stays_listed_and_raises_on_use():
    result = _run("""
        from unittest import mock
        from pathlib_next.uri import UriPath
        with mock.patch("importlib.import_module", side_effect=ImportError("no dep")):
            try:
                UriPath.scheme_class("ftp")
            except ImportError as error:
                print("raised:", error)
            print("ftp" in UriPath.schemes())
        """)
    assert result.stdout.splitlines() == ["raised: no dep", "True"]
