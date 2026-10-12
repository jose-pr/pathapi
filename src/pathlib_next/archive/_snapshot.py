from __future__ import annotations

import pathlib as _pathlib

from ..utils.stat import FileStat
from ._names import _normalize_member_name

MAX_LINKS = 40
"""The most link members one lookup follows before it is a loop
(`OSError(ELOOP)`)."""


class LinkTarget(_pathlib.PurePosixPath):
    """What `readlink()` of a link member returns: the stored text as a
    `PurePosixPath`, which spells it normalized (`./a//b` is `a/b`), and as
    `path`, which is the text exactly as stored. `PathSyncer` and `copy()`
    recreate a link from `path`."""

    __slots__ = ("_text",)

    @property
    def path(self) -> str:
        try:
            return self._text
        except AttributeError:
            return self.as_posix()


NO_LINK = object()
"""What a snapshot is given for an entry that is not a link."""


class _Unresolved(Exception):
    """A lookup that cannot get through the link members on its way: the one
    named `link` leaves the archive, is absolute or has no readable text
    (`loop` False), or the lookup followed `MAX_LINKS` links (`loop` True)."""

    def __init__(self, link: str, loop: bool):
        super().__init__(link)
        self.link = link
        self.loop = loop


class _Snapshot:
    """What one open handle holds, derived once from its entries and kept
    until the handle is dropped: the normalized-name index, every raw
    spelling of a name, the directories (implicit ones included) with their
    children, and the stat data of each entry. Nothing here touches the
    handle again, so a lookup or a listing sees one consistent archive
    however the file changes meanwhile.

    A member whose name escapes the root is left out entirely, so it can be
    neither listed nor looked up. When two spellings normalize to one name
    the later entry wins, as `zipfile`/`tarfile` resolve duplicates;
    `spellings` lists all of them."""

    __slots__ = (
        "index",
        "spellings",
        "infos",
        "files",
        "implicit",
        "children",
        "links",
        "_format",
    )

    def __init__(self, format: "type[_ArchiveBackend]", entries, links=None):
        # The backend class, not the backend: a snapshot that held its backend
        # would keep it alive in a reference cycle.
        self._format = format
        index: "dict[str, str]" = {}
        spellings: "dict[str, list[str]]" = {}
        infos: "dict[str, object]" = {}
        for raw, info in entries:
            infos[raw] = info
            normalized = _normalize_member_name(raw)
            if not normalized:
                continue
            previous = index.get(normalized)
            if previous is not None and previous != raw:
                others = spellings.setdefault(normalized, [previous])
                if raw not in others:
                    others.append(raw)
            index[normalized] = raw
        self.index = index
        self.spellings = spellings
        self.infos = infos
        # The link members, by normalized name: the text they store, or None
        # when it cannot be read. `links` maps raw names.
        self.links = (
            {name: links[raw] for name, raw in index.items() if raw in links}
            if links
            else {}
        )
        # Members that are files: nothing lies below one. A link is not one:
        # what lies below it lies below what it points at.
        self.files = {
            name
            for name, raw in index.items()
            if not name.endswith("/")
            and not format._is_directory(infos[raw])
            and name not in self.links
        }
        # Every proper prefix of a name is a directory, and `children` holds
        # the first component below each directory, in archive order.
        implicit: "set[str]" = set()
        children: "dict[str, dict[str, None]]" = {"": {}}
        for name, raw in index.items():
            parts = name.rstrip("/").split("/")
            directory = ""
            for position, part in enumerate(parts):
                children.setdefault(directory, {})[part] = None
                path = f"{directory}/{part}" if directory else part
                if position < len(parts) - 1:
                    implicit.add(path)
                directory = path
            if name.endswith("/") or format._is_directory(infos[raw]):
                children.setdefault(directory, {})
        self.implicit = implicit
        self.children = children

    def add(self, raw: str, info, link=NO_LINK) -> bool:
        """Adds the entry `raw`, or replaces its info, in place, giving the
        snapshot the entries it would have been built from with this one
        last. `link` is the text of a link entry (None when unreadable). False,
        and nothing changed, when `raw` names a member the snapshot already
        holds under another spelling. For a snapshot that no other thread
        reads."""
        normalized = _normalize_member_name(raw)
        current = self.index.get(normalized) if normalized else None
        if current is not None and current != raw:
            return False
        self.infos[raw] = info
        if not normalized:
            return True
        self.index[normalized] = raw
        if link is NO_LINK:
            self.links.pop(normalized, None)
        else:
            self.links[normalized] = link
        directory = normalized.endswith("/") or self._format._is_directory(info)
        if directory or link is not NO_LINK:
            self.files.discard(normalized)
        else:
            self.files.add(normalized)
        if current is not None:
            return True
        directory_path = ""
        parts = normalized.rstrip("/").split("/")
        for position, part in enumerate(parts):
            self.children.setdefault(directory_path, {})[part] = None
            path = f"{directory_path}/{part}" if directory_path else part
            if position < len(parts) - 1:
                self.implicit.add(path)
            directory_path = path
        if directory:
            self.children.setdefault(directory_path, {})
        return True

    def drop(self, raw: str) -> bool:
        """Removes the entry `raw` in place, giving the snapshot the entries it
        would have been built from without it: a directory that existed only
        through it goes too, and so does each directory above that was left
        with nothing. A directory keeps its place in the listing of its parent.
        False, and nothing changed, when its name has several spellings here.
        For a snapshot that no other thread reads."""
        normalized = _normalize_member_name(raw)
        if normalized and (
            self.index.get(normalized) != raw or normalized in self.spellings
        ):
            return False
        self.infos.pop(raw, None)
        if not normalized:
            return True
        del self.index[normalized]
        self.files.discard(normalized)
        self.links.pop(normalized, None)
        path = normalized.rstrip("/")
        while path:
            below = self.children.get(path)
            if not below:
                self.implicit.discard(path)
            if path in self.index or f"{path}/" in self.index or below:
                break
            parent, _, leaf = path.rpartition("/")
            self.children.pop(path, None)
            siblings = self.children.get(parent)
            if siblings is not None:
                siblings.pop(leaf, None)
            path = parent
        return True

    def resolve(self, member: str, *, follow: bool = True) -> str:
        """`member` with every link member on its way replaced by what it
        stores, as a normalized name that is not a link: a relative text is
        read from the directory that holds the link, an absolute one or one
        that leaves the root names nothing. The last component is left alone
        unless `follow`. Raises `_Unresolved`. Costs nothing when the archive
        holds no link."""
        links = self.links
        if not links:
            return member
        done: "list[str]" = []
        todo = member.split("/")[::-1] if member else []
        followed = 0
        last = member
        while todo:
            part = todo.pop()
            if part in ("", "."):
                continue
            if part == "..":
                if not done:
                    raise _Unresolved(last, False)
                done.pop()
                continue
            name = "/".join((*done, part))
            if name in links and (todo or follow):
                followed += 1
                last = name
                if followed > MAX_LINKS:
                    raise _Unresolved(name, True)
                text = links[name]
                if not text or text.startswith("/"):
                    raise _Unresolved(name, False)
                todo.extend(text.split("/")[::-1])
                continue
            done.append(part)
        return "/".join(done)

    def hidden(self, member: str) -> bool:
        """Whether a file member sits above `member`. A file has no children:
        `y/z` next to a file `y` does not exist, as for a name that escapes
        the root, so a lookup agrees with the listing that cannot show it."""
        files = self.files
        start = member.find("/")
        while start > 0:
            if member[:start] in files:
                return True
            start = member.find("/", start + 1)
        return False

    def stat(self, member: str) -> "FileStat | None":
        """The stat of the member `member` (normalized, no trailing slash
        unless it names a directory entry), or None when there is none. The
        root is a directory; a directory that exists only through its members
        is one too."""
        if member == "":
            return FileStat(is_dir=True)
        if self.hidden(member):
            return None
        raw = self.index.get(member)
        if raw is None:
            raw = self.index.get(f"{member}/")
        if raw is not None:
            return self._format._stat_of(self.infos[raw], self)
        if member in self.implicit:
            return FileStat(is_dir=True)
        return None

    def raw_names(self, normalized: str) -> "tuple[str, ...]":
        """Every key the archive holds the member `normalized` under, in
        archive order (empty when there is none): an archive can spell one
        member `norm` and `./norm`, and removing or renaming it has to act on
        all of them or the shadowed one comes back."""
        raw = self.index.get(normalized)
        if raw is None:
            return ()
        return tuple(self.spellings.get(normalized) or (raw,))
