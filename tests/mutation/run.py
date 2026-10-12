"""Run the mutation list in `mutants.py` against the test suite.

    python tests/mutation/run.py [--list] [--jobs N] [--no-control] [ID ...]

Each mutant is one textual replacement in a guard of the library. It is
applied to a temporary copy of `src/pathlib_next` (nothing under `src/` is
ever written), the tests it names run against that copy with the copy first
on the import path, and a pytest plugin refuses the run if the checkout was
imported instead. A mutant is

- KILLED   when a named test fails (the safety test can fail);
- SURVIVED when every named test passes against the mutated copy;
- STALE    when its `old` text no longer occurs exactly once in its file;
- ERROR    when pytest could not run the named tests (or they were all
           skipped here), or timed out;
- N/A      when the mutant names a platform other than this one.

Before any mutant runs, the named tests run once against an unmutated copy:
a test that already fails there would "kill" everything.

The exit status is 0 only when every mutant that applies here was KILLED, so
a list that stopped matching the code, or a test that stopped catching its
mutant, cannot go unnoticed. Nothing here is collected by pytest.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PACKAGE = REPO / "src" / "pathlib_next"

sys.path.insert(0, str(HERE))
from mutants import MUTANTS, Mutant  # noqa: E402


def copy_package(destination: Path) -> Path:
    """A copy of the package under `destination`; returns the directory that
    holds `pathlib_next`."""
    shutil.copytree(
        PACKAGE,
        destination / "pathlib_next",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return destination


def apply(mutant: Mutant, root: Path) -> str | None:
    """Apply `mutant` to the copy under `root`; the reason it cannot be, or
    None."""
    target = root / "pathlib_next" / mutant.file
    if not target.is_file():
        return f"{mutant.file} does not exist"
    text = target.read_bytes().decode("utf-8")
    count = text.count(mutant.old)
    if count != 1:
        return f"the line occurs {count} times in {mutant.file}"
    if mutant.old == mutant.new:
        return "the replacement is the original text"
    target.write_bytes(text.replace(mutant.old, mutant.new).encode("utf-8"))
    return None


def run_tests(root: Path, tests, work: Path, timeout: float):
    """Run `tests` against the copy under `root`; `(returncode, output)`."""
    env = dict(os.environ)
    env["MUTATION_ROOT"] = str(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(HERE)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "--no-header",
        "-rfE",
        "-p",
        "no:cacheprovider",
        "-p",
        "mutation_check",
        "-o",
        f"pythonpath={root}",
        f"--basetemp={work / 'pytest'}",
        *tests,
    ]
    try:
        done = subprocess.run(
            command,
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return None, output
    return done.returncode, done.stdout


def first_failure(output: str) -> str:
    for line in output.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            return line[:160]
    return ""


def judge(mutant: Mutant, scratch: Path, timeout: float):
    """`(verdict, detail)` for one mutant."""
    if mutant.platform and mutant.platform != os.name:
        return "N/A", f"its tests need os.name == {mutant.platform!r}"
    work = Path(tempfile.mkdtemp(prefix=f"{mutant.id}-", dir=scratch))
    root = copy_package(work)
    reason = apply(mutant, root)
    if reason is not None:
        return "STALE", reason
    code, output = run_tests(root, mutant.tests, work, timeout)
    if code is None:
        return "ERROR", f"no verdict within {timeout:.0f} s"
    if code == 0:
        if not re.search(r"\d+ passed", output):
            return (
                "ERROR",
                "no named test ran: " + (output.strip().splitlines() or [""])[-1],
            )
        return "SURVIVED", "every named test passed against the mutant"
    if code == 1:
        return "KILLED", first_failure(output)
    return "ERROR", f"pytest exited {code}: " + (
        first_failure(output) or "see --verbose"
    )


def control(selected, scratch: Path, timeout: float) -> bool:
    """Run every named test once against an unmutated copy."""
    tests = list(
        dict.fromkeys(
            test
            for mutant in selected
            if not mutant.platform or mutant.platform == os.name
            for test in mutant.tests
        )
    )
    work = Path(tempfile.mkdtemp(prefix="control-", dir=scratch))
    root = copy_package(work)
    code, output = run_tests(root, tests, work, timeout * 4)
    if code == 0:
        print(f"control: {len(tests)} named tests pass on an unmutated copy")
        return True
    print(f"control FAILED (exit {code}): the named tests do not pass unmutated")
    print("\n".join(output.splitlines()[-25:]))
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ids", nargs="*", help="run only these mutants")
    parser.add_argument("--list", action="store_true", help="list the mutants")
    parser.add_argument("--jobs", type=int, default=1, help="mutants run at once")
    parser.add_argument("--timeout", type=float, default=600, help="seconds per mutant")
    parser.add_argument(
        "--no-control", action="store_true", help="skip the unmutated run"
    )
    args = parser.parse_args(argv)

    ids = [mutant.id for mutant in MUTANTS]
    if len(set(ids)) != len(ids):
        parser.error(
            "duplicate mutant ids: "
            + ", ".join(sorted({i for i in ids if ids.count(i) > 1}))
        )
    unknown = [name for name in args.ids if name not in ids]
    if unknown:
        parser.error("unknown mutant: " + ", ".join(unknown))
    selected = [m for m in MUTANTS if not args.ids or m.id in args.ids]

    if args.list:
        for mutant in selected:
            print(f"{mutant.id:44} {mutant.file}: {mutant.note}")
        return 0

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pathlib-next-mutation-") as scratch_name:
        scratch = Path(scratch_name)
        if not args.no_control and not control(selected, scratch, args.timeout):
            return 2
        tally: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max(1, args.jobs)) as pool:
            futures = {
                pool.submit(judge, mutant, scratch, args.timeout): mutant
                for mutant in selected
            }
            for future in concurrent.futures.as_completed(futures):
                mutant = futures[future]
                verdict, detail = future.result()
                tally[verdict] = tally.get(verdict, 0) + 1
                print(f"{mutant.id:44} {verdict:9} {detail}", flush=True)
    summary = ", ".join(
        f"{count} {name.lower()}" for name, count in sorted(tally.items())
    )
    print(f"{len(selected)} mutants: {summary} in {time.monotonic() - started:.0f} s")
    applicable = len(selected) - tally.get("N/A", 0)
    return 0 if tally.get("KILLED", 0) == applicable else 1


if __name__ == "__main__":
    sys.exit(main())
