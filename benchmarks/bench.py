"""pathapi benchmark runner.

Run `python benchmarks/bench.py --help` for the suites. `--save` writes the
run as structured JSON into `benchmarks/results/`; the schema is documented in
`benchmarks/README.md`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import statistics
import subprocess
import sysconfig
import timeit
import tempfile
import shutil
import time
import datetime
from pathlib import Path as StdlibPath

# Add src to sys.path so we can import pathapi without installing it
import sys

REPO_ROOT = StdlibPath(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pathapi import LocalPath
from pathapi.mempath import MemPath, MemPathBackend
from pathapi.sync import PathSyncer

SCHEMA = "pathapi.bench/1"
MAX_SAMPLES = 100


class _Recorder:
    """Collects every measured metric of one run for `--save`.

    A timing metric stores min/median/max milliseconds per call over its
    samples; each sample times `calls_per_sample` calls of the operation.
    """

    def __init__(self):
        self.metrics = {}
        self.counters = {}
        self.errors = {}
        self.skipped = {}

    def timing(self, name, samples, *, group, calls=1):
        per_call_ms = sorted(sample / calls * 1000.0 for sample in samples)
        self.metrics[name] = {
            "group": group,
            "min_ms": round(per_call_ms[0], 6),
            "median_ms": round(statistics.median(per_call_ms), 6),
            "max_ms": round(per_call_ms[-1], 6),
            "samples": len(per_call_ms),
            "calls_per_sample": calls,
        }

    def counter(self, name, values, *, group):
        self.counters[name] = {"group": group, **values}

    def error(self, name, message, *, group):
        self.errors[name] = {"group": group, "error": message}

    def skip(self, name, reason, *, group):
        self.skipped[name] = {"group": group, "reason": reason}


RECORDER = _Recorder()
_SAMPLES_OVERRIDE = None


def _reps(default):
    """Sample count for a case: `--samples` when given, else the case default."""
    return _SAMPLES_OVERRIDE or default


def _timeit(stmt, *, number, name, group, setup="pass", repeat=5):
    """`timeit.repeat` a statement, record it, return the median sample total."""
    samples = timeit.repeat(stmt, setup=setup, number=number, repeat=_reps(repeat))
    RECORDER.timing(name, samples, group=group, calls=number)
    return statistics.median(samples)


def benchmark_path_join():
    # Join paths using / operator 10,000 times
    setup = "from pathapi import MemPath; p = MemPath('/host/path')"
    code = "p / 'sub' / 'child'"
    return _timeit(code, setup=setup, number=10000, name="mem: path join", group="mem")


def benchmark_segments_name_access():
    # Access .segments and .name 10,000 times
    setup = "from pathapi import MemPath; u = MemPath('/host/a/b/c/d/e')"
    code = "_ = u.segments; _ = u.name"
    return _timeit(
        code, setup=setup, number=10000, name="mem: segments + name access", group="mem"
    )


def benchmark_suffix_stem():
    # Access .suffix and .stem 10,000 times
    setup = "from pathapi import MemPath; u = MemPath('/host/path/file.tar.gz')"
    code = "_ = u.suffix; _ = u.stem"
    return _timeit(
        code, setup=setup, number=10000, name="mem: suffix + stem access", group="mem"
    )


def benchmark_glob_mempath():
    # Glob over a 1k-file MemPath tree
    backend = MemPathBackend()
    root = MemPath("/", backend=backend)
    # Build 10 x 10 x 10 tree = 1000 files
    for i in range(10):
        for j in range(10):
            for k in range(10):
                p = root / f"dir_{i}" / f"sub_{j}" / f"file_{k}.txt"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("content")

    # Run glob 20 times per sample
    samples = timeit.repeat(
        lambda: list(root.glob("**/*.txt")), number=20, repeat=_reps(3)
    )
    RECORDER.timing(
        "mem: MemPath glob('**/*.txt') 1k files", samples, group="mem", calls=20
    )
    return statistics.median(samples)


def benchmark_localpath_vs_stdlib():
    # Measure LocalPath construct + stat vs raw pathlib.Path
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_file = StdlibPath(tmpdir) / "test.txt"
        temp_file.write_text("hello")

        setup_local = (
            f"from pathapi import LocalPath; p = LocalPath({repr(str(temp_file))})"
        )
        code_local = "p.stat()"
        t_local = _timeit(
            code_local,
            setup=setup_local,
            number=2000,
            name="local: LocalPath stat() (timeit)",
            group="local",
        )

        setup_std = f"from pathlib import Path; p = Path({repr(str(temp_file))})"
        code_std = "p.stat()"
        t_std = _timeit(
            code_std,
            setup=setup_std,
            number=2000,
            name="local: pathlib.Path stat() (timeit)",
            group="local",
        )

        ratio = t_local / t_std if t_std > 0 else 0.0
        return t_local, t_std, ratio


def benchmark_localpath_matrix():
    """Compare LocalPath against pathlib.Path on common local hot paths."""
    rows = []
    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        payload = os.urandom(64 * 1024)

        for i in range(6):
            for j in range(8):
                subdir = root / f"dir_{i}" / f"sub_{j}"
                subdir.mkdir(parents=True, exist_ok=True)
                for k in range(5):
                    (subdir / f"file_{k}.txt").write_text(
                        f"{i}-{j}-{k}\n", encoding="utf-8"
                    )

        sample_file = root / "dir_0" / "sub_0" / "sample.bin"
        sample_file.write_bytes(payload)

        def add_case(name, local_operation, std_operation, *, repeat=5, calls=1):
            t_local = _measure(
                local_operation,
                repeat=_reps(repeat),
                name=f"local: LocalPath {name}",
                group="local",
                calls=calls,
            )
            t_std = _measure(
                std_operation,
                repeat=_reps(repeat),
                name=f"local: pathlib.Path {name}",
                group="local",
                calls=calls,
            )
            ratio = t_local / t_std if t_std > 0 else float("inf")
            rows.append((name, t_local, t_std, ratio))

        add_case(
            "construct path (10k)",
            lambda: [LocalPath(str(sample_file)) for _ in range(10000)],
            lambda: [StdlibPath(str(sample_file)) for _ in range(10000)],
            repeat=3,
            calls=10000,
        )
        add_case(
            "join path (10k)",
            lambda base=LocalPath(str(root)): [
                base / "alpha" / "beta" / "gamma.txt" for _ in range(10000)
            ],
            lambda base=StdlibPath(str(root)): [
                base / "alpha" / "beta" / "gamma.txt" for _ in range(10000)
            ],
            repeat=3,
            calls=10000,
        )
        add_case(
            "stat() file (2k)",
            lambda path=LocalPath(str(sample_file)): [path.stat() for _ in range(2000)],
            lambda path=StdlibPath(str(sample_file)): [
                path.stat() for _ in range(2000)
            ],
            repeat=3,
            calls=2000,
        )
        add_case(
            "read_bytes() 64 KiB",
            lambda path=LocalPath(str(sample_file)): path.read_bytes(),
            lambda path=StdlibPath(str(sample_file)): path.read_bytes(),
            repeat=5,
        )
        add_case(
            "iterdir() 8 entries",
            lambda path=LocalPath(str(root / "dir_0")): list(path.iterdir()),
            lambda path=StdlibPath(str(root / "dir_0")): list(path.iterdir()),
            repeat=5,
        )
        add_case(
            "glob('**/*.txt') 240 files",
            lambda path=LocalPath(str(root)): list(path.glob("**/*.txt")),
            lambda path=StdlibPath(str(root)): list(path.glob("**/*.txt")),
            repeat=3,
        )
    return rows


def benchmark_pathsyncer_matrix():
    """Measure PathSyncer on local trees before considering parallelism."""
    rows = []
    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        source = root / "source"
        target = root / "target"
        source.mkdir()
        target.mkdir()
        for i in range(8):
            for j in range(16):
                path = source / f"dir_{i}" / f"file_{j:03d}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{i}-{j}\n", encoding="utf-8")
        for i in range(4):
            extra = target / f"extra_{i}.txt"
            extra.write_text("extra\n", encoding="utf-8")

        def checksum(entry):
            return entry.stat.st_size

        def fresh_target():
            if target.exists():
                shutil.rmtree(target)
            target.mkdir()

        def copy_all():
            fresh_target()
            PathSyncer(checksum).sync(LocalPath(source), LocalPath(target))

        def dry_run():
            fresh_target()
            PathSyncer(checksum).sync(
                LocalPath(source), LocalPath(target), dry_run=True
            )

        def remove_missing():
            fresh_target()
            for i in range(4):
                (target / f"extra_{i}.txt").write_text("extra\n", encoding="utf-8")
            PathSyncer(checksum, remove_missing=True).sync(
                LocalPath(source), LocalPath(target)
            )

        for name, operation in (
            ("copy 128 local files", copy_all),
            ("dry-run 128 local files", dry_run),
            ("remove-missing 4 extras + copy 128 local files", remove_missing),
        ):
            rows.append(
                (
                    name,
                    _measure(
                        operation,
                        repeat=_reps(3),
                        warmup=False,
                        name=f"syncer: {name}",
                        group="syncer",
                    ),
                )
            )
    return rows


def _write_tree(root: StdlibPath, files: dict[str, bytes | str]) -> None:
    for relpath, data in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")


def _measure(operation, *, repeat=5, warmup=True, name=None, group=None, calls=1):
    """Time `repeat` samples of `operation`; return the median sample seconds.

    With `name`, the samples are also recorded for `--save`.
    """
    if warmup:
        operation()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    if name is not None:
        RECORDER.timing(name, samples, group=group, calls=calls)
    return statistics.median(samples)


def _measure_status(
    operation, *, repeat=1, warmup=False, name=None, group=None, calls=1
):
    """Like `_measure`, but an exception becomes an `"Error: message"` result."""
    samples = []
    try:
        if warmup:
            operation()
        for _ in range(repeat):
            start = time.perf_counter()
            operation()
            samples.append(time.perf_counter() - start)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        if name is not None:
            RECORDER.error(name, message, group=group)
        return message
    if name is not None:
        RECORDER.timing(name, samples, group=group, calls=calls)
    return statistics.median(samples)


def _fmt_metric(value):
    if isinstance(value, (int, float)):
        return f"{value:.4f}s"
    return str(value)


def print_pathsyncer_results(rows, *, markdown=False):
    if markdown:
        print("| Benchmark Case | Time / Metric |")
        print("|---|---|")
        for name, metric in rows:
            print(f"| PathSyncer {name} | {_fmt_metric(metric)} |")
        return
    print("PathSyncer local comparison:")
    for name, metric in rows:
        print(f"   - {name}: {_fmt_metric(metric)}")


def _recursive_matrix_files():
    files = {}
    for i in range(4):
        for j in range(8):
            files[f"dir_{i}/file_{j:02d}.txt"] = f"{i}-{j}\n"
    files["dir_0/nested/deep.txt"] = "deep\n"
    return files


def _seed_stdlib_tree(root):
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _write_tree(root, _recursive_matrix_files())


def _seed_mem_tree(path):
    for relpath, data in _recursive_matrix_files().items():
        file_path = path / relpath
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(data)


def benchmark_recursive_matrix():
    """Narrow recursive operation probes for local and mem paths."""
    rows = []
    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        src = root / "src"
        dst = root / "dst"
        victim = root / "victim"
        _seed_stdlib_tree(src)

        def local_copy():
            if dst.exists():
                shutil.rmtree(dst)
            LocalPath(src).copy(LocalPath(dst), recursive=True)

        def local_rm():
            _seed_stdlib_tree(victim)
            LocalPath(victim).rm(recursive=True)

        for name, operation in (
            ("LocalPath copy(recursive=True) 33-file tree", local_copy),
            ("LocalPath rm(recursive=True) 33-file tree", local_rm),
        ):
            rows.append(
                (
                    name,
                    _measure_status(
                        operation,
                        repeat=_reps(3),
                        name=f"recursive: {name}",
                        group="recursive",
                    ),
                )
            )

    def mem_copy():
        backend = MemPathBackend()
        src = MemPath("/src", backend=backend)
        dst = MemPath("/dst", backend=backend)
        src.mkdir()
        _seed_mem_tree(src)
        src.copy(dst, recursive=True)

    def mem_rm():
        backend = MemPathBackend()
        victim = MemPath("/victim", backend=backend)
        victim.mkdir()
        _seed_mem_tree(victim)
        victim.rm(recursive=True)

    for name, operation in (
        ("MemPath copy(recursive=True) 33-file tree", mem_copy),
        ("MemPath rm(recursive=True) 33-file tree", mem_rm),
    ):
        rows.append(
            (
                name,
                _measure_status(
                    operation,
                    repeat=_reps(3),
                    name=f"recursive: {name}",
                    group="recursive",
                ),
            )
        )
    return rows


def print_recursive_matrix_results(rows, *, markdown=False):
    if markdown:
        print("| Benchmark Case | Time / Metric |")
        print("|---|---|")
        for name, metric in rows:
            print(f"| {name} | {_fmt_metric(metric)} |")
        return
    print("Recursive operation matrix:")
    for name, metric in rows:
        print(f"   - {name}: {_fmt_metric(metric)}")


#: The tree of the fan-out suite: directories of small files.
FANOUT_DIRS = 8
FANOUT_FILES = 8
FANOUT_BYTES = 512
#: What each operation of the simulated slow backend waits, in seconds.
FANOUT_DELAY = 0.002
#: The `max_workers` settings the fan-out suite measures; None is serial.
FANOUT_WORKERS = (None,)


class _DelayedMem(MemPath):
    """A `MemPath` whose every operation waits `FANOUT_DELAY` seconds: a
    simulated backend with a round trip per call, not a measurement of any
    real one. It may be used from several threads."""

    _threadsafe = True

    def _wait(self):
        time.sleep(FANOUT_DELAY)

    def stat(self, *, follow_symlinks=True):
        self._wait()
        return super().stat(follow_symlinks=follow_symlinks)

    def iterdir(self):
        self._wait()
        return super().iterdir()

    def _open(self, mode="r", buffering=-1):
        self._wait()
        return super()._open(mode, buffering)

    def _mkdir(self, mode):
        self._wait()
        return super()._mkdir(mode)

    def unlink(self, missing_ok=False):
        self._wait()
        return super().unlink(missing_ok)

    def rmdir(self):
        self._wait()
        return super().rmdir()


def _fanout_files():
    payload = "x" * FANOUT_BYTES
    return {
        f"dir_{i}/file_{j:02d}.txt": payload
        for i in range(FANOUT_DIRS)
        for j in range(FANOUT_FILES)
    }


def _fanout_label():
    return f"{FANOUT_DIRS * FANOUT_FILES}-file tree"


class _LoopbackFtpServer:
    """A pyftpdlib server on 127.0.0.1 serving a temporary directory."""

    def __init__(self, directory):
        import threading

        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.ioloop import Select
        from pyftpdlib.servers import FTPServer

        logging.getLogger("pyftpdlib").setLevel(logging.CRITICAL)
        directory.mkdir(exist_ok=True)
        authorizer = DummyAuthorizer()
        authorizer.add_user("user", "12345", str(directory), perm="elradfmwMT")
        handler = type("Handler", (FTPHandler,), {"authorizer": authorizer})
        self._ioloop = Select()
        self._server = FTPServer(("127.0.0.1", 0), handler, ioloop=self._ioloop)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            self._server.serve_forever()
        except OSError:
            pass

    def close(self):
        self._ioloop.call_later(0, self._server.close_all)
        self._thread.join(timeout=10)


def _measure_staged(setup, operation, *, repeat, name, group):
    """Like `_measure`, but `setup()` runs before each sample, outside the
    timing; the first run only opens the connections and is not recorded."""
    samples = []
    try:
        for index in range(repeat + 1):
            setup()
            start = time.perf_counter()
            operation()
            elapsed = time.perf_counter() - start
            if index:
                samples.append(elapsed)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        RECORDER.error(name, message, group=group)
        return message
    RECORDER.timing(name, samples, group=group)
    return statistics.median(samples)


def _fanout_cases(rows, backend, source, make_target, seed_victim, remove):
    """Time recursive copy, recursive rm and a first sync of the tree at
    `source` into the path `make_target()` returns, for every setting of
    `FANOUT_WORKERS`."""
    tree = _fanout_label()
    for workers in FANOUT_WORKERS:
        extra = {} if workers is None else {"max_workers": workers}
        label = "" if workers is None else f", max_workers={workers}"
        target = make_target("target")
        victim = make_target("victim")
        for case, setup, operation in (
            (
                f"{backend} copy(recursive=True) {tree}{label}",
                lambda: remove(target),
                lambda: source.copy(target, recursive=True, **extra),
            ),
            (
                f"{backend} rm(recursive=True) {tree}{label}",
                lambda: seed_victim(victim),
                lambda: victim.rm(recursive=True, **extra),
            ),
            (
                f"{backend} sync {tree}{label}",
                lambda: remove(target),
                lambda: PathSyncer(**extra).sync(source, target),
            ),
        ):
            rows.append(
                (
                    case,
                    _measure_staged(
                        setup,
                        operation,
                        repeat=_reps(3),
                        name=f"fanout: {case}",
                        group="fanout",
                    ),
                )
            )


def _seed_delayed(path, files):
    path.mkdir()
    for relpath, data in files.items():
        file_path = path / relpath
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(data)


def benchmark_fanout():
    """Recursive copy, rm and sync of a tree of small files on a loopback FTP
    server and on a simulated slow backend."""
    rows = []
    files = _fanout_files()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = StdlibPath(tmpdir)
        served = root / "served"
        _write_tree(root / "source", files)
        try:
            server = _LoopbackFtpServer(served)
        except ImportError as error:
            RECORDER.skip(
                "fanout: FTP", f"pyftpdlib is not installed: {error}", group="fanout"
            )
        else:
            from pathapi.ftp import FTPBackend, FTPPath

            backend = FTPBackend(
                "127.0.0.1", server.port, user="user", password="12345", timeout=30
            )

            def ftp_target(name):
                return FTPPath(f"/{name}", backend=backend)

            def ftp_remove(path):
                shutil.rmtree(served / path.name, ignore_errors=True)

            def ftp_seed(path):
                ftp_remove(path)
                _write_tree(served / path.name, files)

            try:
                _fanout_cases(
                    rows,
                    "FTP loopback",
                    LocalPath(root / "source"),
                    ftp_target,
                    ftp_seed,
                    ftp_remove,
                )
            finally:
                backend.close()
                server.close()

    def delayed(name):
        return _DelayedMem(f"/{name}", backend=MemPathBackend())

    def delayed_remove(path):
        if path.exists():
            path.rm(recursive=True)

    def delayed_seed(path):
        delayed_remove(path)
        _seed_delayed(path, files)

    mem_source = delayed("source")
    _seed_delayed(mem_source, files)
    _fanout_cases(
        rows,
        f"simulated {FANOUT_DELAY * 1000:g} ms per call",
        mem_source,
        delayed,
        delayed_seed,
        delayed_remove,
    )
    return rows


def print_fanout_results(rows, *, markdown=False):
    if markdown:
        print("| Benchmark Case | Time / Metric |")
        print("|---|---|")
        for name, metric in rows:
            print(f"| {name} | {_fmt_metric(metric)} |")
        return
    print("Fan-out matrix:")
    for name, metric in rows:
        print(f"   - {name}: {_fmt_metric(metric)}")


def main():
    print("Running benchmarks...")

    t_join = benchmark_path_join()
    print(f"1. Path Join (10k runs): {t_join:.4f}s")

    t_seg_name = benchmark_segments_name_access()
    print(f"2. Segments/Name Access (10k runs): {t_seg_name:.4f}s")

    t_suffix_stem = benchmark_suffix_stem()
    print(f"2b. Suffix/Stem Access (10k runs): {t_suffix_stem:.4f}s")

    t_glob_mem = benchmark_glob_mempath()
    print(f"3. Glob over 1k MemPath (20 runs): {t_glob_mem:.4f}s")

    t_local, t_std, local_ratio = benchmark_localpath_vs_stdlib()
    print(
        f"4. LocalPath stat (2k runs): {t_local:.4f}s vs pathlib.Path: "
        f"{t_std:.4f}s (Ratio: {local_ratio:.2f}x)"
    )
    local_matrix = benchmark_localpath_matrix()
    print("4b. LocalPath vs pathlib.Path matrix:")
    for name, t_local_case, t_std_case, case_ratio in local_matrix:
        winner = "LocalPath" if case_ratio < 1 else "pathlib.Path"
        print(
            f"   - {name}: LocalPath={t_local_case:.4f}s, "
            f"pathlib.Path={t_std_case:.4f}s "
            f"(LocalPath/pathlib={case_ratio:.2f}x, faster={winner})"
        )

    # Print Markdown table format for copy-pasting
    print("\n| Benchmark Case | Time / Metric |")
    print("|---|---|")
    print(f"| Path Join (10k) | {t_join:.4f}s |")
    print(f"| Segments/Name Access (10k) | {t_seg_name:.4f}s |")
    print(f"| Suffix/Stem Access (10k) | {t_suffix_stem:.4f}s |")
    print(f"| Glob 1k MemPath (20) | {t_glob_mem:.4f}s |")
    print(
        f"| LocalPath vs Stdlib (2k stat) | Local: {t_local:.4f}s, "
        f"Stdlib: {t_std:.4f}s (Ratio: {local_ratio:.2f}x) |"
    )
    for name, t_local_case, t_std_case, case_ratio in local_matrix:
        print(
            f"| LocalPath {name} | Local: {t_local_case:.4f}s, "
            f"Stdlib: {t_std_case:.4f}s (Local/Stdlib: {case_ratio:.2f}x) |"
        )


def _pyproject_version():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _git_state():
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": dirty}


def _interpreter_tag():
    """`<major.minor>-<os>-<arch>`, with the arch the interpreter was built for."""
    arch = sysconfig.get_platform().rsplit("-", 1)[-1]
    os_name = "darwin" if sys.platform == "darwin" else os.name
    return f"{sys.version_info[0]}.{sys.version_info[1]}-{os_name}-{arch}"


def build_result(suite, *, name, note=None):
    """The JSON document `--save` writes (schema: benchmarks/README.md)."""
    return {
        "schema": SCHEMA,
        "name": name,
        "suite": suite,
        "source": "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
        "note": note,
        "created_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "package": {"name": "pathapi", "version": _pyproject_version()},
        "git": _git_state(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "interpreter": _interpreter_tag(),
        "processor": f"{platform.system()}-{platform.machine()}",
        "cpu_count": os.cpu_count(),
        "env": {},
        "metrics": dict(sorted(RECORDER.metrics.items())),
        "counters": dict(sorted(RECORDER.counters.items())),
        "errors": dict(sorted(RECORDER.errors.items())),
        "skipped": dict(sorted(RECORDER.skipped.items())),
    }


def default_result_name(suite):
    name = f"{_pyproject_version()}-py{_interpreter_tag()}"
    return name if suite == "default" else f"{name}-{suite}"


def save_result(suite, *, name=None, results_dir=None, note=None):
    name = name or default_result_name(suite)
    result = build_result(suite, name=name, note=note)
    directory = (
        StdlibPath(results_dir) if results_dir else REPO_ROOT / "benchmarks" / "results"
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    with open(target, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(f"\nSaved {len(result['metrics'])} metrics to {target}")
    return target


def _samples_arg(value):
    count = int(value)
    if not 1 <= count <= MAX_SAMPLES:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_SAMPLES}")
    return count


SUITES = {
    "syncer": "run PathSyncer local tree probes",
    "recursive-matrix": "run narrow recursive local and mem call-count probes",
    "fanout": "run recursive copy, rm and sync on loopback FTP and a simulated slow backend",
}


def cli(argv=None):
    global _SAMPLES_OVERRIDE

    parser = argparse.ArgumentParser(
        description="Run pathapi benchmarks (the default suite when no suite is named).",
        epilog=(
            "Options go before the suite name, e.g. "
            "`python benchmarks/bench.py --save --samples 5 syncer`."
        ),
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="write the run as JSON to benchmarks/results/<name>.json",
    )
    parser.add_argument(
        "--name",
        help="result name for --save, without .json "
        "(default: <version>-py<major.minor>-<os>-<arch>[-<suite>])",
    )
    parser.add_argument(
        "--results-dir",
        help="directory for --save (default: the benchmarks/results directory beside this script)",
    )
    parser.add_argument(
        "--samples",
        type=_samples_arg,
        help=f"samples per metric for every case, 1-{MAX_SAMPLES} "
        "(default: each case's own count, 1-5)",
    )
    parser.add_argument("--note", help="free-text note stored in the saved JSON")
    subparsers = parser.add_subparsers(dest="command", metavar="suite")
    for suite_name, help_text in SUITES.items():
        subparsers.add_parser(suite_name, help=help_text)
    args = parser.parse_args(argv)
    _SAMPLES_OVERRIDE = args.samples

    run_suite(args.command)
    if args.save:
        save_result(
            args.command or "default",
            name=args.name,
            results_dir=args.results_dir,
            note=args.note,
        )


def run_suite(command):
    if command == "syncer":
        print("Running PathSyncer benchmarks...")
        print_pathsyncer_results(benchmark_pathsyncer_matrix(), markdown=True)
    elif command == "recursive-matrix":
        print("Running recursive operation matrix...")
        print_recursive_matrix_results(benchmark_recursive_matrix(), markdown=True)
    elif command == "fanout":
        print("Running fan-out benchmarks...")
        print_fanout_results(benchmark_fanout(), markdown=True)
    else:
        main()


if __name__ == "__main__":
    cli()
