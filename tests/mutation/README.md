# The mutation list

A safety test can pass for the wrong reason. This list answers "can it fail?":
each entry is one single-line change to a guard of the library (a check
removed, a comparison inverted, an error left untranslated) and the tests that
must fail because of it.

```bash
python tests/mutation/run.py                 # every mutant, one at a time
python tests/mutation/run.py --jobs 4        # four at once
python tests/mutation/run.py ID [ID ...]     # only these
python tests/mutation/run.py --list          # ids, files and what each breaks
```

Run it with the interpreter of the environment you test in, from the
repository root, with the same `PYTHONPATH` the test suite needs. It is run on
demand and never in CI; nothing in this directory is collected by pytest.

For each mutant `run.py` copies `src/pathlib_next` to a temporary directory
(nothing under `src/` is written), applies the replacement there, and runs the
named tests with the copy first on the import path. A pytest plugin
(`mutation_check.py`) stops the run if the checkout was imported instead.
Before any mutant, the named tests run once against an unmutated copy: a test
that fails there would "kill" every mutant.

| Verdict | Meaning |
| --- | --- |
| `KILLED` | a named test failed: the guard is protected |
| `SURVIVED` | every named test passed against the mutant: add or sharpen a test |
| `STALE` | the `old` text no longer occurs exactly once in its file: re-express the entry |
| `ERROR` | pytest could not run the tests, they were all skipped here, or they timed out |
| `N/A` | the entry names a platform other than this one |

The exit status is 0 only when every mutant that applies was `KILLED`.

## Adding a mutant

Append a call to `mutants.py`:

```python
mutant(
    "copy-onto-itself",                      # a unique id: what breaks, in words
    "path.py",                               # relative to src/pathlib_next
    "        if _same_file(src, target, identity=False):\n",   # exact text, once
    "        if False:\n",                                      # its replacement
    "tests/test_destructive_safety.py::test_copy_onto_itself_raises_and_keeps_content",
    "copy() of a file onto itself is not refused",             # the note
)
```

- Change one guard, so that the code still imports and runs. Replace the
  condition, not the block: a guard turned into `if False:` keeps its body
  indented and valid.
- Take enough context in `old` that it occurs exactly once; `run.py` reports
  it `STALE` otherwise.
- Name the test (or tests, as a sequence) that should catch it, as pytest
  arguments: a node id without its parameters runs all of them. Pick a test
  that runs in the usual environment; one that is skipped there is an `ERROR`.
  Add `platform="nt"` when only that operating system can run them.
- Run `python tests/mutation/run.py ID`. If it `SURVIVED`, the guard has no
  test that depends on it: write one, then run it again.
- A mutant that cannot be killed because another check makes it equivalent (a
  second layer that still refuses the same input) does not belong in the list.

When the code a mutant targets moves, the run reports it `STALE` and exits
non-zero; re-express the entry against the new text and keep its tests.
