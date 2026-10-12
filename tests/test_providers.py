"""`providers`: the LRU cache, the `notimplemented` stub and the mode and owner
arguments of `chmod()` and `chown()`."""

import pathlib
import stat
import subprocess
import sys
import textwrap

import pytest

import pathlib_next
from pathlib_next import providers


def test_lru_basic_cache_hit():
    calls = []

    def fn(x):
        calls.append(x)
        return x * 2

    lru = providers.LRU(fn)
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

    lru = providers.LRU(fn)
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

    lru = providers.LRU(fn, maxsize=3)
    lru(1)
    lru(2)
    lru(3)
    lru.maxsize = 1
    assert lru.maxsize == 1
    assert list(lru.cache.keys()) == [(3,)]
    lru(3)
    assert calls == [1, 2, 3]  # 3 still cached, not recomputed


def test_notimplemented_decorator_message():
    @providers.notimplemented
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

        from pathlib_next._lru import LRU, K

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
        cwd=pathlib.Path(providers.__file__).parents[2],
    )
    assert (
        "FALLBACK_OK" in result.stdout
    ), f"fallback import failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_lru_with_maxsize_zero_stores_nothing_and_evicts_nothing():
    evicted = []
    calls = []

    def make(key):
        calls.append(key)
        return [key]

    cache = providers.LRU(
        make, maxsize=0, on_evict=lambda key, value: evicted.append(value)
    )
    first = cache(1)
    second = cache(1)
    assert calls == [1, 1]
    assert first is not second
    assert evicted == []
    assert len(cache.cache) == 0


def test_lru_with_a_negative_maxsize_stores_nothing():
    cache = providers.LRU(lambda key: [key], maxsize=-3)
    cache(1)
    assert len(cache.cache) == 0


def test_lru_with_maxsize_none_is_unbounded():
    cache = providers.LRU(lambda key: [key], maxsize=None)
    values = [cache(index) for index in range(500)]
    assert len(cache.cache) == 500
    assert cache(7) is values[7]


def test_lru_maxsize_can_be_set_to_zero_and_none():
    evicted = []
    cache = providers.LRU(
        lambda key: [key], maxsize=4, on_evict=lambda key, value: evicted.append(key)
    )
    for index in range(3):
        cache(index)
    cache.maxsize = 0
    assert len(cache.cache) == 0
    assert sorted(evicted) == [(0,), (1,), (2,)]
    cache.maxsize = None
    cache(9)
    assert len(cache.cache) == 1


def test_lru_still_evicts_the_oldest_over_a_positive_maxsize():
    evicted = []
    cache = providers.LRU(
        lambda key: [key], maxsize=2, on_evict=lambda key, value: evicted.append(key)
    )
    for index in range(3):
        cache(index)
    assert evicted == [(0,)]


@pytest.mark.parametrize("bad", [-1, 0o200000, 1 << 40, "07777777777777", "-1"])
def test_as_mode_refuses_a_number_that_cannot_be_a_mode(bad):
    with pytest.raises(ValueError):
        providers.as_mode(bad)


@pytest.mark.parametrize("bad", [True, False])
def test_as_mode_refuses_a_bool(bad):
    with pytest.raises(TypeError):
        providers.as_mode(bad)


@pytest.mark.parametrize(
    "good,expected",
    [(0, 0), (0o644, 0o644), ("0755", 0o755), (0o7777, 0o7777), (0o100644, 0o100644)],
)
def test_as_mode_keeps_a_mode_and_the_type_bits_of_a_st_mode(good, expected):
    assert providers.as_mode(good) == expected


def test_a_whole_st_mode_still_goes_through_chmod(tmp_path):
    target = pathlib_next.LocalPath(tmp_path / "f")
    target.write_text("x")
    target.chmod(target.stat().st_mode)
    target.chmod(stat.S_IMODE(target.stat().st_mode))


@pytest.mark.parametrize("bad", [(True, None), (None, False), (True, False)])
def test_as_owner_refuses_a_bool(bad):
    with pytest.raises(TypeError):
        providers.as_owner(*bad)


@pytest.mark.parametrize("bad", [(-2, None), (None, -7), (1 << 32, None)])
def test_as_owner_refuses_an_id_that_cannot_exist(bad):
    with pytest.raises(ValueError):
        providers.as_owner(*bad)


def test_as_owner_keeps_ids_names_and_the_unchanged_sentinels():
    assert providers.as_owner(-1, 5) == (None, 5)
    assert providers.as_owner(0, 0) == (0, 0)
    assert providers.as_owner("root", None) == ("root", None)
    assert providers.as_owner((1 << 32) - 1, -1) == ((1 << 32) - 1, None)
