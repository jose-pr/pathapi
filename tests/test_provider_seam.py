"""The seam between the core and the classes other distributions build on it.

Every helper a provider calls is a public name of `pathapi.providers` or
`pathapi.archive`, listed in that module's `__all__` and documented in the
implementer sections of the shipped header.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

import pathapi

PACKAGE = pathlib.Path(pathapi.__file__).resolve().parent
HEADER = PACKAGE / "AGENTS.md"
IMPLEMENTER_HEADINGS = ("For path implementers",)

# (module, public name): documented helpers a provider calls, defined in a
# private module and listed in the public module's `__all__`.
PROVIDER_HELPERS = [
    ("pathapi.archive", "detect_format"),
    ("pathapi.providers", "CommitOnClose"),
    ("pathapi.providers", "DiscardOnError"),
    ("pathapi.providers", "check_follow"),
    ("pathapi.providers", "contains"),
    ("pathapi.providers", "copy_error_predicate"),
]


def documented_names(text: str) -> set[str]:
    """The names the python blocks of the implementer sections declare."""
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


@pytest.mark.parametrize(("module", "public"), PROVIDER_HELPERS)
def test_a_provider_helper_is_exported_and_documented(module, public):
    imported = importlib.import_module(module)
    assert public in imported.__all__
    assert callable(getattr(imported, public))
    assert public in documented_names(HEADER.read_text(encoding="utf-8"))
