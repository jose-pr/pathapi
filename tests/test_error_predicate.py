"""`rm()`, `copy(on_error=)` and `PathSyncer(ignore_error=)` take one kind of
error predicate: it is called once per error with the entry that failed,
`True` skips the entry, `False` raises the error, and a predicate that raises
ends the operation with its own exception, which is not offered to it again.
"""

from __future__ import annotations

import pytest

from pathlib_next import LocalPath
from pathlib_next.mempath import MemPath
from pathlib_next.utils.sync import PathSyncer


class Stop(Exception):
    """What a predicate raises to end the operation."""


class FailingMem(MemPath):
    """A `MemPath` that cannot unlink `bad.txt`."""

    __slots__ = ()

    def unlink(self, missing_ok=False):
        if self.name == "bad.txt":
            raise PermissionError(13, "denied", str(self))
        return super().unlink(missing_ok=missing_ok)


class FailingLocal(LocalPath):
    """A `LocalPath` that cannot unlink `bad.txt`."""

    def unlink(self, missing_ok=False):
        if self.name == "bad.txt":
            raise PermissionError(13, "denied", str(self))
        return LocalPath.unlink(self, missing_ok=missing_ok)


def predicate(decision, offered):
    """A predicate for any of the three call sites: it reads only the error
    and the first path it is given, whatever else the site passes."""

    def decide(error, where, *rest):
        entry = getattr(where, "path", where)  # a PathAndStat holds its path
        offered.append((type(error).__name__, entry.name))
        if decision == "raise":
            raise Stop("the predicate stops the operation")
        return decision == "swallow"

    return decide


DECISIONS = ["swallow", "decline", "raise"]


def outcome(call):
    try:
        call()
    except Stop:
        return "stop"
    except OSError as error:
        return type(error).__name__
    return "returned"


# A tree three directories deep whose innermost entry cannot be handled.


def mem_rm_tree(root):
    root = FailingMem(root)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "bad.txt").write_text("x")
    return root


def local_rm_tree(tmp_path):
    root = FailingLocal(tmp_path / "rm")
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "bad.txt").write_text("x")
    return root


def clash_trees(make):
    """`(source, target)`: `a/b/clash` is a file in the source and a
    directory holding a file in the target."""
    source, target = make("src"), make("dst")
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "b" / "clash").write_text("1")
    (target / "a" / "b" / "clash").mkdir(parents=True)
    (target / "a" / "b" / "clash" / "kept").write_text("2")
    return source, target


def mem_clash():
    return clash_trees(lambda name: MemPath("/" + name))


def local_clash(tmp_path):
    return clash_trees(lambda name: LocalPath(tmp_path / name))


@pytest.mark.parametrize("decision", DECISIONS)
def test_copy_on_error_offers_the_failing_child_once(decision):
    source, target = mem_clash()
    offered = []

    result = outcome(
        lambda: source.copy(
            target,
            recursive=True,
            overwrite=True,
            on_error=predicate(decision, offered),
        )
    )

    assert offered == [("IsADirectoryError", "clash")]
    assert (
        result
        == {
            "swallow": "returned",
            "decline": "IsADirectoryError",
            "raise": "stop",
        }[decision]
    )
    assert (target / "a" / "b" / "clash" / "kept").read_text() == "2"


@pytest.mark.parametrize("decision", DECISIONS)
def test_copy_on_error_decides_for_local_paths_too(tmp_path, decision):
    source, target = local_clash(tmp_path)
    offered = []

    result = outcome(
        lambda: source.copy(
            target,
            recursive=True,
            overwrite=True,
            on_error=predicate(decision, offered),
        )
    )

    assert offered == [("IsADirectoryError", "clash")]
    assert (
        result
        == {
            "swallow": "returned",
            "decline": "IsADirectoryError",
            "raise": "stop",
        }[decision]
    )
    assert (target / "a" / "b" / "clash" / "kept").read_text() == "2"


def test_copy_on_error_skips_one_child_and_copies_the_others():
    source, target = mem_clash()
    (source / "a" / "b" / "fine").write_text("3")

    source.copy(target, recursive=True, overwrite=True, on_error=lambda e, p: True)

    assert (target / "a" / "b" / "fine").read_text() == "3"
    assert (target / "a" / "b" / "clash").is_dir()


def test_copy_on_error_takes_a_bool_like_ignore_error():
    source, target = mem_clash()
    source.copy(target, recursive=True, overwrite=True, on_error=True)
    with pytest.raises(IsADirectoryError):
        source.copy(target, recursive=True, overwrite=True, on_error=False)


def test_copy_on_error_is_not_asked_about_the_root():
    source, target = mem_clash()
    offered = []

    with pytest.raises(FileExistsError):
        source.copy(target, recursive=True, on_error=predicate("swallow", offered))

    assert offered == []


def test_copy_with_both_error_keywords_raises_before_copying_anything():
    source, target = MemPath("/s"), MemPath("/t")
    (source / "a").mkdir(parents=True)
    (source / "a" / "f").write_text("1")

    with pytest.raises(TypeError, match="ignore_error or on_error"):
        source.copy(
            target, recursive=True, ignore_error=True, on_error=lambda e, p: True
        )

    assert not target.exists()


def test_copy_into_passes_on_error_on():
    source, target = mem_clash()
    offered = []

    copied = (source / "a").copy_into(
        target,
        recursive=True,
        overwrite=True,
        on_error=predicate("swallow", offered),
    )

    assert copied == target / "a"
    assert offered == [("IsADirectoryError", "clash")]
    with pytest.raises(TypeError):
        source.copy_into(target, ignore_error=True, on_error=True)


def test_copy_ignore_error_still_notifies_and_swallows():
    source, target = mem_clash()
    seen = []

    source.copy(target, recursive=True, overwrite=True, ignore_error=seen.append)

    assert [type(error) for error in seen] == [IsADirectoryError]


def test_copy_passes_on_error_to_a_child_only_when_it_was_given():
    class Old(MemPath):
        """A `copy()` written before `on_error` existed."""

        __slots__ = ()
        keywords = []

        def copy(
            self,
            target,
            *,
            overwrite=False,
            follow_symlinks=True,
            preserve_metadata=True,
            recursive=False,
            ignore_error=None,
            progress=None,
        ):
            Old.keywords.append(ignore_error)
            return MemPath.copy(
                self,
                target,
                overwrite=overwrite,
                follow_symlinks=follow_symlinks,
                preserve_metadata=preserve_metadata,
                recursive=recursive,
                ignore_error=ignore_error,
                progress=progress,
            )

    source, target = Old("/s"), Old("/t")
    (source / "d").mkdir(parents=True)
    (source / "d" / "f").write_text("1")

    source.copy(target, recursive=True, ignore_error=False)

    assert Old.keywords and set(Old.keywords) == {False}
    assert (target / "d" / "f").read_text() == "1"


# The same predicate at the three call sites.


@pytest.mark.parametrize("decision", DECISIONS)
def test_rm_offers_the_failing_entry_and_not_its_own_exception(decision):
    root = mem_rm_tree("/r")
    offered = []

    result = outcome(
        lambda: root.rm(recursive=True, ignore_error=predicate(decision, offered))
    )

    assert offered[0] == ("PermissionError", "bad.txt")
    if decision == "swallow":
        assert result == "returned"
    elif decision == "decline":
        assert result == "PermissionError" and len(offered) == 1
    else:
        assert result == "stop" and len(offered) == 1


@pytest.mark.parametrize("decision", DECISIONS)
def test_rm_of_local_paths_offers_the_failing_entry_once(tmp_path, decision):
    root = local_rm_tree(tmp_path)
    offered = []

    result = outcome(
        lambda: root.rm(recursive=True, ignore_error=predicate(decision, offered))
    )

    assert offered[0] == ("PermissionError", "bad.txt")
    if decision != "swallow":
        assert len(offered) == 1
        assert result == {"decline": "PermissionError", "raise": "stop"}[decision]


@pytest.mark.parametrize("decision", DECISIONS)
def test_sync_offers_the_failing_entry_once(decision):
    source, target = MemPath("/s"), MemPath("/d")
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "b" / "f").write_text("1")
    (target / "a" / "b" / "f").mkdir(parents=True)
    (target / "a" / "b" / "f" / "g").write_text("2")
    offered = []

    result = outcome(
        lambda: PathSyncer(ignore_error=predicate(decision, offered)).sync(
            source, target
        )
    )

    assert offered == [("IsADirectoryError", "f")]
    assert (
        result
        == {
            "swallow": "returned",
            "decline": "IsADirectoryError",
            "raise": "stop",
        }[decision]
    )


def test_the_same_predicate_gives_the_same_outcome_at_all_three_sites():
    results = {}
    for decision in DECISIONS:
        found = []
        offered = []
        chosen = predicate(decision, offered)

        root = mem_rm_tree("/r")
        found.append(outcome(lambda: root.rm(recursive=True, ignore_error=chosen)))

        source, target = mem_clash()
        found.append(
            outcome(
                lambda: source.copy(
                    target, recursive=True, overwrite=True, on_error=chosen
                )
            )
        )

        src, dst = MemPath("/s"), MemPath("/d")
        src.mkdir()
        (src / "f").write_text("1")
        (dst / "f").mkdir(parents=True)
        (dst / "f" / "g").write_text("2")
        found.append(outcome(lambda: PathSyncer(ignore_error=chosen).sync(src, dst)))
        results[decision] = [
            "stop" if one == "stop" else ("raised" if one != "returned" else one)
            for one in found
        ]

    assert results == {
        "swallow": ["returned"] * 3,
        "decline": ["raised"] * 3,
        "raise": ["stop"] * 3,
    }
