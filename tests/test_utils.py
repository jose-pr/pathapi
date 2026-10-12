import calendar
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

from pathlib_next import utils


def test_lru_basic_cache_hit():
    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    lru = utils.LRU(fn)
    assert lru(2) == 4
    assert lru(2) == 4
    assert calls == [2]  # cached, fn only called once


def test_lru_invalidate_b5():
    # B5 regression: invalidate() did `with self.lock():` -- RLock isn't
    # callable, so this raised TypeError instead of invalidating.
    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    lru = utils.LRU(fn)
    lru(3)
    lru.invalidate(3)
    assert calls == [3, 3]  # recomputed after invalidation


def test_lru_maxsize_shrink_evicts_oldest_n1():
    # N1 regression: the maxsize setter called `cache.pop(last=False)` --
    # OrderedDict.pop() takes a key, not a `last` kwarg -- so shrinking
    # maxsize below the current fill raised TypeError instead of evicting.
    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    lru = utils.LRU(fn, maxsize=3)
    lru(1)
    lru(2)
    lru(3)
    lru.maxsize = 1
    assert lru.maxsize == 1
    assert list(lru.cache.keys()) == [(3,)]
    lru(3)
    assert calls == [1, 2, 3]  # 3 still cached, not recomputed


def test_notimplemented_decorator_message():
    @utils.notimplemented
    def some_method(): ...

    try:
        some_method()
        assert False, "should have raised"
    except NotImplementedError as e:
        assert "some_method" in str(e)
        assert "  " not in str(e)  # no leftover double-space typo


@pytest.mark.skipif(
    sys.version_info >= (3, 10),
    reason="the ParamSpec fallback is only reachable on 3.9 (3.10+ has typing.ParamSpec)",
)
def test_paramspec_fallback_importable_without_typing_extensions():
    # Regression: on Python 3.9 `typing.ParamSpec` does not exist, so the module
    # falls back to `typing_extensions.ParamSpec` and, failing that, to a local
    # shim. The old fallback was a bare `TypeVar`, which has no `.args`, so the
    # `*args: K.args` annotations raised `AttributeError: 'TypeVar' object has no
    # attribute 'args'` at import. It went unnoticed because every dev/CI env had
    # `typing_extensions` installed transitively -- but it is not a runtime
    # dependency, so a clean 3.9 install of the package could not import it.
    #
    # 3.9-only by nature, not by convenience: `Generic[K, V]` on 3.9 rejects a
    # plain object ("Parameters to generic types must be types"), so the shim must
    # subclass `TypeVar` -- and `TypeVar` stopped being subclassable in 3.12. The
    # branch is unreachable on 3.10+ anyway, since `typing.ParamSpec` exists there.
    #
    # Runs in a subprocess with `typing_extensions` blocked so the fallback is
    # exercised for real rather than mocked.
    probe = textwrap.dedent("""
        import builtins

        _real_import = builtins.__import__

        def _blocked(name, *a, **k):
            if name == "typing_extensions":
                raise ImportError("emulating an env without typing_extensions")
            return _real_import(name, *a, **k)

        builtins.__import__ = _blocked

        from pathlib_next.utils import LRU, K

        assert K.args is not None, "ParamSpec fallback lost .args"
        assert K.kwargs is not None, "ParamSpec fallback lost .kwargs"
        lru = LRU(lambda x: x * 2, maxsize=4)
        assert lru(21) == 42
        assert lru(21) == 42
        lru.invalidate(21)
        assert lru(21) == 42
        print("FALLBACK_OK")
        """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=pathlib.Path(utils.__file__).parents[3],
    )
    assert (
        "FALLBACK_OK" in result.stdout
    ), f"fallback import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
