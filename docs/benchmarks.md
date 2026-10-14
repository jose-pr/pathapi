# Benchmarks

`pathlib_next` includes a benchmark harness at `benchmarks/bench.py` for
checking hot-path behavior of the local and in-memory classes and of
`PathSyncer`. It needs nothing beyond the package:
`pip install -e ".[dev]"`.

## Run It

```bash
python benchmarks/bench.py            # default suite
python benchmarks/bench.py --help     # options and suites; runs nothing
```

Narrower suites:

```bash
python benchmarks/bench.py syncer
python benchmarks/bench.py recursive-matrix
```

## Saved Results

Every metric is timed over several samples. `--save` writes the run as JSON to
`benchmarks/results/<name>.json`, with the minimum, median and maximum
milliseconds per call of each metric plus the interpreter, platform and git
commit it ran on:

```bash
python benchmarks/bench.py --save                 # default suite
python benchmarks/bench.py --save --samples 9 syncer
```

Options go before the suite name. `--samples N` applies one sample count to
every case; without it each case uses its own (1-5). The result schema and the
committed baselines are described in
[`benchmarks/README.md`](https://github.com/jose-pr/pathlib-next/blob/main/benchmarks/README.md).

Compare two runs on their medians, and only between runs from the same machine
and interpreter. A run on a developer machine is a sanity check; performance
claims in release notes come from CI runs.

## What It Covers

- generic path joins and name/suffix access (`MemPath`)
- `MemPath` recursive glob
- `LocalPath` vs `pathlib.Path` on common local operations
- `PathSyncer` on local trees, and recursive copy/remove on local and
  in-memory trees

The benchmarks of the classes in other distributions (URIs, HTTP listings,
SFTP, object stores) live with those distributions.

## Past Snapshots

The tables below are dated snapshots from July 2026, before results were
saved as JSON. Each row is a single run (the harness took no repeated samples
then), so treat them as a shape of performance, not a stable contract.

### Local run, July 12, 2026

A local Windows run with Python 3.12.

| Benchmark Case | Time / Metric |
| --- | --- |
| Path Join (10k) | 0.1599s |
| Segments/Name Access (10k) | 0.0014s |
| Suffix/Stem Access (10k) | 0.0010s |
| Glob 1k MemPath (20) | 0.5281s |
| LocalPath vs Stdlib (2k stat) | Local: 0.0388s, Stdlib: 0.0332s |
| LocalPath construct path (10k) | Local: 0.0175s, Stdlib: 0.0184s |
| LocalPath join path (10k) | Local: 0.0498s, Stdlib: 0.0456s |
| LocalPath stat() file (2k) | Local: 0.0296s, Stdlib: 0.0328s |
| LocalPath read_bytes() 64 KiB | Local: 0.0002s, Stdlib: 0.0003s |
| LocalPath iterdir() 8 entries | Local: 0.0003s, Stdlib: 0.0003s |
| LocalPath glob('**/*.txt') 240 files | Local: 0.0143s, Stdlib: 0.0120s |

The path-join row timed a URI path in that run; the harness now times a
`MemPath`, so the numbers are not comparable with a current run.

### CI run, July 12, 2026

GitHub Actions run `Test #3` completed the benchmark job on `ubuntu-latest`,
`windows-latest`, and `macos-latest` with Python 3.12.

| Case | Ubuntu | Windows | macOS |
| --- | --- | --- | --- |
| LocalPath stat() file (2k) | 0.0030s vs stdlib 0.0028s | 0.0105s vs stdlib 0.0132s | 0.0054s vs stdlib 0.0052s |

## Findings From Past Runs

- `python benchmarks/bench.py syncer` on a local run reported PathSyncer copy
  of 128 local files at `0.4524s`, dry-run at `0.0534s`, and remove-missing
  plus copy at `0.9260s`. After PathSyncer started reusing listing metadata,
  a later run reported copy at `0.4390s`, dry-run at `0.0535s`, and
  remove-missing plus copy at `0.4764s`: a clear remove-missing improvement
  and a roughly neutral copy/dry-run result.
- `python benchmarks/bench.py recursive-matrix` on a local Windows Python 3.12
  run reported: `LocalPath` recursive copy `0.3115s`, `LocalPath` recursive
  remove `0.3363s`, `MemPath` recursive copy `0.0377s`, and `MemPath`
  recursive remove `0.0197s` for a 33-file tree. Local filesystem timings are
  noisy; use the command primarily for trend checks.
- `LocalPath` was competitive with `pathlib.Path` on several hot local
  operations in these runs, but trailed on recursive globbing and the sampled
  `read_bytes()` case.

### Not measured by wall clock

- **Native checksum protocol.** The saving is structural: when `PathSyncer`'s
  default policy can use both sides' native digest (a class whose backend
  computes one, such as an SFTP server implementing the filexfer draft's
  `check-file-handle` extension), a file comparison transfers **zero content
  bytes** for a match-or-mismatch verdict, versus the streaming fallback's full
  read on *both* sides (`2 * file_size` for an unchanged file that still needs
  comparing).
- **`PathSyncer(quick_check=True)`.** Also structural. For an unchanged
  non-local file with matching `st_size`/`st_mtime`, the pre-check skips the
  checksum step (native or streaming) entirely: **zero content bytes and zero
  extension round trips**, versus the native-checksum path's one request per
  file. It only applies when metadata already agrees; any mismatch (including
  an unchanged file whose mtime a prior copy did not preserve) still pays for a
  real checksum.

## Caveats

- Benchmark output is environment-sensitive: Python version, OS, filesystem,
  CPU, and installed packages all matter.
- For regressions, compare before/after runs on the same machine and Python
  version rather than comparing absolute times across environments.
