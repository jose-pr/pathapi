"""The seam between the core and the URI schemes.

Every underscore name that a scheme module (or the command line) imports from
the core or reads as an attribute of one of its objects is documented in the
two implementer sections of the shipped header, and each helper that was made
public is the same object as the underscore name it replaced.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

import pathlib_next

PACKAGE = pathlib.Path(pathlib_next.__file__).resolve().parent
HEADER = PACKAGE / "AGENTS.md"
IMPLEMENTER_HEADINGS = ("For path implementers", "For scheme implementers")

# Names a scheme module reads that are its own: an attribute of one of its
# helper classes, or of a stdlib named tuple. Each is a name the scan finds
# in a core module too; a pair that no longer matches anything fails.
NOT_THE_CORES = {
    ("uri/schemes/_objstore.py", "_dirty"): "a write stream's own flag",
    ("uri/schemes/s3.py", "_path"): "the path a stream or a client call is for",
    ("uri/schemes/_objstore.py", "_path"): "the path a stream is for",
    ("uri/schemes/dav.py", "_path"): "the path a stream is for",
    ("uri/schemes/http.py", "_path"): "the path a stream is for",
    ("uri/schemes/dav.py", "_replace"): "a stdlib named tuple's method",
    ("uri/schemes/http.py", "_replace"): "a stdlib named tuple's method",
    ("uri/schemes/_gitrepo.py", "_replace"): "a `Source` named tuple's method",
}

# (module, public name): documented helpers a provider calls, defined in a
# private module and listed in the public module's `__all__`.
PROVIDER_HELPERS = [
    ("pathlib_next.archive", "detect_format"),
    ("pathlib_next.providers", "CommitOnClose"),
    ("pathlib_next.providers", "check_follow"),
    ("pathlib_next.providers", "contains"),
    ("pathlib_next.providers", "copy_error_predicate"),
]

# (module, public name, the underscore name it replaced)
PUBLIC_HELPERS = [
    ("pathlib_next.uri", "same_authority", "_same_authority"),
    ("pathlib_next.uri", "DerivedBackend", "_DerivedBackend"),
    ("pathlib_next.uri.source", "compose_uri", "_compose_uri"),
    ("pathlib_next.uri.source", "compose_host", "_compose_host"),
    ("pathlib_next.uri.source", "percent_encode", "_encode"),
    ("pathlib_next.uri.source", "is_drive", "_is_drive"),
    ("pathlib_next.uri.source", "SAFE_PATH", "_SAFE_PATH"),
    ("pathlib_next.uri.source", "ERRORS", "_ERRORS"),
]


def is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _files(directory: pathlib.Path):
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def core_sources() -> dict[str, str]:
    """Every module that is not a scheme, a provider package (`ftp`, `archive`),
    the command line or the test helpers."""
    skipped = (
        PACKAGE / "uri" / "schemes",
        PACKAGE / "tools",
        PACKAGE / "ftp",
        PACKAGE / "archive",
    )
    return {
        p.relative_to(PACKAGE).as_posix(): p.read_text(encoding="utf-8")
        for p in _files(PACKAGE)
        if not any(s in p.parents for s in skipped) and p.name != "testing.py"
    }


def scheme_sources() -> dict[str, str]:
    return {
        p.relative_to(PACKAGE).as_posix(): p.read_text(encoding="utf-8")
        for directory in (PACKAGE / "uri" / "schemes", PACKAGE / "tools")
        for p in _files(directory)
    }


def core_names(sources: dict[str, str]) -> set[str]:
    """The underscore names the core defines: functions, classes, module and
    class attributes, `self.x = ...` and `__slots__` entries."""
    found: set[str] = set()
    for text in sources.values():
        for node in ast.walk(ast.parse(text)):
            names: list[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.append(target.id)
                        if target.id == "__slots__" and isinstance(
                            node.value, (ast.Tuple, ast.List)
                        ):
                            names += [
                                e.value
                                for e in node.value.elts
                                if isinstance(e, ast.Constant)
                            ]
                    elif isinstance(target, ast.Attribute) and isinstance(
                        target.value, ast.Name
                    ):
                        names.append(target.attr)
            found.update(n for n in names if is_private(n))
    return found


def _imported_module(relpath: str, node: ast.ImportFrom) -> str:
    """The dotted module a `from ... import` of the file at `relpath` names."""
    package = ["pathlib_next", *relpath.split("/")[:-1]]
    if node.level:
        package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, *(node.module.split(".") if node.module else [])])


def seam_names(core: set[str], sources: dict[str, str]) -> dict[str, set[str]]:
    """`{name: modules}` for each underscore name of `core` that a module of
    `sources` imports from outside the scheme layer or reads as an attribute."""
    used: dict[str, set[str]] = {}
    for relpath, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                module = _imported_module(relpath, node)
                if module.startswith("pathlib_next.uri.schemes"):
                    continue
                for alias in node.names:
                    if alias.name in core:
                        used.setdefault(alias.name, set()).add(relpath)
            elif isinstance(node, ast.Attribute) and node.attr in core:
                used.setdefault(node.attr, set()).add(relpath)
    return used


def documented_names(text: str) -> set[str]:
    """The names the python blocks of the two implementer sections declare."""
    found: set[str] = set()
    for title in IMPLEMENTER_HEADINGS:
        section, inside, fenced = [], False, False
        for line in text.splitlines():
            if line.startswith("```"):
                fenced = not fenced
            elif not fenced and re.match(r"#{1,3} ", line):
                inside = line.startswith(f"### {title}")
            if inside:
                section.append(line)
        assert section, f"the header has no '{title}' section"
        for block in re.findall(
            r"```python\n(.*?)```", "\n".join(section), flags=re.DOTALL
        ):
            for node in ast.walk(ast.parse(block)):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    found.add(node.name)
                elif isinstance(node, ast.Assign):
                    found.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Name):
                    found.add(node.value.id)
    return found


@pytest.fixture(scope="module")
def core():
    return core_names(core_sources())


def test_the_scan_finds_the_names_the_schemes_use(core):
    used = seam_names(core, scheme_sources())
    assert {
        "_init",
        "_from_parsed_parts",
        "_format_parsed_parts",
        "_make_child_relpath",
        "_rename_target",
        "_pop_stat_hint",
        "_backend_for",
        "_supplied_backend",
        "_scandir",
    } <= set(used)


def test_every_core_name_a_scheme_uses_is_documented_in_the_header(core):
    used = seam_names(core, scheme_sources())
    undocumented = {
        name: sorted(modules)
        for name, modules in used.items()
        if name not in documented_names(HEADER.read_text(encoding="utf-8"))
        and not all((module, name) in NOT_THE_CORES for module in modules)
    }
    assert undocumented == {}


def test_the_scheme_owned_names_are_all_still_read_by_their_module(core):
    used = seam_names(core, scheme_sources())
    stale = [pair for pair in NOT_THE_CORES if pair[0] not in used.get(pair[1], ())]
    assert stale == []


def test_a_scheme_that_takes_an_undocumented_core_name_is_reported(core):
    sources = scheme_sources()
    sources["uri/schemes/new.py"] = (
        "from ...path import _relation\n"
        "\n"
        "class New:\n"
        "    def go(self):\n"
        "        return self._family()\n"
    )
    used = seam_names(core, sources)
    documented = documented_names(HEADER.read_text(encoding="utf-8"))
    assert {"_relation", "_family"} <= set(used)
    assert {"_relation", "_family"}.isdisjoint(documented)


def test_no_scheme_imports_an_underscore_name_from_the_core(core):
    imported = []
    for relpath, text in scheme_sources().items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom):
                module = _imported_module(relpath, node)
                if module.startswith("pathlib_next.uri.schemes"):
                    continue
                imported += [
                    (relpath, alias.name) for alias in node.names if alias.name in core
                ]
    assert imported == []


@pytest.mark.parametrize(("module", "public", "private"), PUBLIC_HELPERS)
def test_a_public_helper_is_the_object_its_underscore_name_stays_as(
    module, public, private
):
    if module.startswith("pathlib_next.uri"):
        pytest.importorskip("uritools")
    imported = importlib.import_module(module)
    assert getattr(imported, public) is getattr(imported, private)
    if hasattr(imported, "__all__"):
        assert public in imported.__all__
        assert private not in imported.__all__


@pytest.mark.parametrize(("module", "public", "private"), PUBLIC_HELPERS)
def test_a_public_helper_is_documented_in_the_header(module, public, private):
    assert public in documented_names(HEADER.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("module", "public"), PROVIDER_HELPERS)
def test_a_provider_helper_is_exported_and_documented(module, public):
    imported = importlib.import_module(module)
    assert public in imported.__all__
    assert callable(getattr(imported, public))
    assert public in documented_names(HEADER.read_text(encoding="utf-8"))
