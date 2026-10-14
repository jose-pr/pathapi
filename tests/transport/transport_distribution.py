"""A throwaway installed distribution: a directory with a `dist-info` that
declares entry points, and the modules they name, run in a child interpreter
that has the directory on its import path."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pathapi

_SRC = pathlib.Path(pathapi.__file__).resolve().parent.parent


def install(directory, name, entry_points, modules):
    """Writes `<name>-1.0.dist-info` with `entry_points` (`{group: {name:
    "module:attribute"}}`) and one `<module>.py` per item of `modules` under
    `directory`."""
    directory = pathlib.Path(directory)
    info = directory / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_bytes(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n".encode()
    )
    text = "".join(
        f"[{group}]\n" + "".join(f"{key} = {value}\n" for key, value in items.items())
        for group, items in entry_points.items()
    )
    (info / "entry_points.txt").write_bytes(text.encode())
    for module, source in modules.items():
        (directory / f"{module}.py").write_bytes(textwrap.dedent(source).encode())
    return directory


def run(code, *directories, env=None):
    """Runs `code` in a child interpreter whose import path starts with this
    checkout of `pathapi` and `directories`, with `env` added to its
    environment, and returns what it printed as JSON (the last line)."""
    child = {**os.environ, **(env or {})}
    child["PYTHONPATH"] = os.pathsep.join(
        [str(_SRC), *map(str, directories), *filter(None, [child.get("PYTHONPATH")])]
    )
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=120,
        env=child,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])
