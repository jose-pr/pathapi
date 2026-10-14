# pathapi — contributor orientation

Orientation for working in a checkout of this repository: layout, environment,
checks, conventions and release. It is not the API reference and it does not
ship.

| Shipped header | Covers |
| --- | --- |
| [`src/pathapi/AGENTS.md`](src/pathapi/AGENTS.md) | The public API contract: every export, signature and gotcha. It ships in the wheel, so it must stay self-contained (no repo-relative links) and must be updated in the same commit as any public API change. |

Deliberate differences from `pathlib.Path` are in
[`docs/divergences.md`](docs/divergences.md). `pathlib.Path` parity is the
contract; a behavioral divergence that is not recorded there is a bug.

This repository is the core alone: no runtime dependency, no extra, no console
script. The URI layer (`uripath`), SFTP (`sftppath`), the object stores
(`objpath`), the repository hosts (`repopath`) and the `requests` HTTP
transport (`pathapi-requests`) are distributions of their own that depend
on this one. What they read from here (the underscore methods of `Path`, the
`providers` helpers, `pathapi.http`) is their contract with it, written in
the shipped header.

## Layout

| Path | Contents |
| --- | --- |
| `src/pathapi/` | The package (`src/` layout). `py.typed` and the API header ship with it. A module whose name has no leading underscore is public and declares `__all__`; every other module is private. |
| `src/pathapi/{path,sync,glob,testing}/` | Packages of private modules; each `__init__` re-exports the public names. |
| `src/pathapi/{ftp,archive}/` | The standard-library providers: `FTPPath` and `FTPBackend`; `ZipPath`, `TarPath` and `ArchivePath`. |
| `src/pathapi/http/` | HTTP requests on the standard library: `HTTPClient`, the transport contract and `HTTPTransport`; transports are registered through the `pathapi.http_transports` entry points in `pyproject.toml`. |
| `src/pathapi/providers.py` | What a class built on `Path` calls, defined in private modules beside it; `exceptions.py` holds `PathError` and `NonRelativePatternError`. |
| `tests/` | The pytest suite. `tests/transport/` is the HTTP transport suite, which also ships in the wheel as `pathapi.http.testing`. |
| `tests/mutation/` | The mutation list; see Checks. Ships in the sdist with `tests/`. |
| `benchmarks/` | `bench.py` and committed JSON results; see [`benchmarks/README.md`](benchmarks/README.md). Not shipped. |
| `examples/` | Runnable scripts. Networked ones skip (exit 0) unless their environment variables are set. |
| `docs/`, `mkdocs.yml` | MkDocs site: hand-written pages plus a `mkdocstrings` API reference. |
| `CHANGELOG.md` | Keep a Changelog. Released sections are frozen records. |

## Environment

Python 3.9 is the floor (`requires-python = ">=3.9"`) and 3.14 is the latest
supported. Test on both ends before claiming a change works.

Keep one virtualenv per interpreter under `.venv/<version>-<os>-<arch>/`
(gitignored), where `<os>` is `os.name` (`nt`/`posix`) or `darwin`, and `<arch>`
is the architecture the interpreter was built for:

```bash
python -m venv .venv/3.14-posix-x86_64
.venv/3.14-posix-x86_64/bin/python -m pip install -e ".[dev,docs]"
```

On Windows the interpreter is `.venv\<name>\Scripts\python.exe`.

`dev` holds every package a test imports. A missing one does not fail the suite:
its tests are skipped instead (the TLS tests without `cryptography`, the typing
checks without `mypy` and `basedpyright`), so check `pytest -rs` before trusting
a green run.

## Checks

```bash
python -m pytest -q                                  # full suite (pythonpath=src is configured)
python -m pytest -q --cov=pathapi --cov-report=term-missing
python -m black src/ tests/ benchmarks/ examples/    # formatting; --check to verify
mkdocs build --strict                                # docs must build with no warnings
python -m build                                      # sdist + wheel into dist/
python tests/mutation/run.py --jobs 4                # every guard's tests must fail on a broken copy
```

- **The mutation list** (`tests/mutation/`) applies one single-line change to a guard
  of a temporary copy of `src/pathapi` and checks that the tests named for
  it fail. Run it on demand, never in CI, after writing or changing a safety
  test; a mutant whose line has moved is `STALE` and fails the run. Its
  `README.md` says how to add one. It ships in the sdist with `tests/`.
- No linter or type checker is enforced; formatting is black (see Conventions).

### Benchmarks

`python benchmarks/bench.py --help` lists the suites. `--save` writes a JSON
result per (version, interpreter, platform) into `benchmarks/results/`; the
schema and the reproduce command are in
[`benchmarks/README.md`](benchmarks/README.md). A local run is a sanity check;
performance claims in the changelog or release notes come from CI runs.

### CI

Workflows live in `.github/workflows/`:

- `test.yml` runs on `workflow_dispatch` (optional `ref` input) or on a pushed
  `ci-*` tag, never on ordinary pushes. To test a commit without the dashboard,
  push a uniquely named throwaway tag (`ci-<topic>-<timestamp>`), follow the run
  to completion, then delete the tag locally and on the remote. Its jobs are the
  test matrix, the bare install (the package, `pytest` and `pyftpdlib` and
  nothing else) and the typing checks. There is no `floors` job: the package has
  no runtime dependency to install at its lower bound. A dependency added later
  needs a range in `pyproject.toml` only (with the API that needs a raised
  floor named in a comment beside it) and a job that installs it at that floor.
- `release.yml` runs on a `v*` tag: the test gates, build (the tag must name the
  version built, `twine check`), an installed-wheel smoke test (the wheel
  imports, ships no URI layer, declares no dependency and defines no console
  script), a strict docs build, then the GitHub release whose notes come from
  that version's `CHANGELOG.md` section. For a final tag only (no `-` in it) it then publishes
  to PyPI through Trusted Publishing and dispatches `docs.yml` at the tag.
- `docs.yml` owns every GitHub Pages deploy: on a push to `main` that touches the
  docs sources and on `workflow_dispatch`, which `release.yml` fires for a final
  tag.

## Conventions

- **Formatting is black**, pinned to `target-version = ["py39"]` so it never
  emits syntax the floor cannot parse.
- **Every file is LF** (`.gitattributes` sets `* text=auto eol=lf`). On Windows,
  black writes CRLF: convert the files back to LF after formatting and check
  `git diff --stat` for whitespace-only churn.
- **3.9 compatibility**: any module using `X | Y` in a runtime-evaluated
  annotation needs `from __future__ import annotations`.
- **`*.local.*` files** are per-machine overrides: gitignored and excluded from
  both build targets. Keep hostnames and credentials in those, never in tracked
  files.

### Commits

- Logical commits in `type: description` form (`feat:`, `fix:`, `docs:`,
  `chore:`); keep code with its tests, and docs/config/CI in separate commits.
- No agent attribution in commit messages: no `Co-Authored-By:` naming a model
  or assistant, no `*-Session:` trailers, no session URLs, no "generated with"
  footers.

## Releasing

1. Move the `[Unreleased]` entries under a new `## [x.y.z] - <date>` heading and
   add its link definition at the bottom of `CHANGELOG.md`.
2. Bump `version` in `pyproject.toml` in the same commit (PEP 440 syntax there;
   SemVer in tags and the changelog).
3. Before 1.0, bump the minor only when the documented API breaks. New methods,
   new optional arguments and fixes are patch releases.
   A fix that corrects behaviour (to `pathlib` parity, to what the
   documentation intends, or to remove a way to lose data) is a patch even
   where an earlier header described the old behaviour; when a caller can
   notice it, its changelog entry goes under `Changed` and says what to do.
4. Run the full suite on the floor and latest interpreters, `mkdocs build
   --strict`, `python -m build`, and the maintainer's leak check (a scan for
   private references and agent-attribution commit trailers). Judge it by exit
   code.
5. Push `main`, then the `v*` tag. Publishing is irreversible, so the tag is
   pushed only with the maintainer's explicit consent for that specific
   release.

Changelog entries say what changed and what a user must do about it. They do
not describe how the work was done.
