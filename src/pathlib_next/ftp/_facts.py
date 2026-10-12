from __future__ import annotations

import calendar as _calendar
import stat as _stat
import time as _time

from ..utils import is_safe_child_name as _is_safe_child_name
from ..utils.stat import FileStat

# Reply codes meaning "command not implemented" (RFC 959): the server lacks
# the command, as opposed to refusing it for this path.
UNSUPPORTED_REPLIES = ("500", "502", "504")

PERMISSION_WORDS = ("permission", "privilege", "denied", "not allowed", "access")


def reply_code(error: BaseException) -> str:
    return str(error)[:3]


def is_child_entry(name: str, facts: dict) -> bool:
    """Whether an MLSD entry names a child of the listed directory. The
    `cdir`/`pdir` entries describe the directory itself and its parent, and
    may carry any name -- RFC 3659's own example lists `tmp` and `/tmp` --
    so they are skipped by type, not only as `.`/`..`; a name that is not one
    path component (`utils.is_safe_child_name()`) is not a child name either."""
    kind = str(facts.get("type", "")).lower()
    return kind not in ("cdir", "pdir") and _is_safe_child_name(name)


def _parse_mlsd_time(value: str) -> int:
    # MLSD "modify" fact: YYYYMMDDHHMMSS[.sss], always UTC (RFC 3659) --
    # timegm, not datetime.timestamp(), which reads a naive time as local.
    try:
        return _calendar.timegm(_time.strptime(value[:14], "%Y%m%d%H%M%S"))
    except ValueError:
        return 0


def _facts_size(value) -> int:
    """`st_size` from an MLSD `size` fact. A value that is not a plain
    non-negative decimal number, or too large for any file, is unknown (0), as
    a missing fact is."""
    text = str(value or "")
    if not (text.isascii() and text.isdigit()):
        return 0
    size = int(text)
    return size if size < 1 << 63 else 0


def _parse_fact_line(line: str) -> "tuple[str, dict]":
    """`(name, facts)` of one RFC 3659 `facts SP name` line; fact names are
    lower-cased, as `ftplib.FTP.mlsd()` does."""
    found, _, name = line.rstrip("\r\n").partition(" ")
    facts = {}
    for fact in found[:-1].split(";") if found else ():
        key, _, value = fact.partition("=")
        facts[key.lower()] = value
    return name, facts


def lists_feature(reply: str, feature: str) -> bool:
    """Whether a `FEAT` reply has a `feature` line (RFC 2389)."""
    for line in reply.splitlines():
        words = line.split(None, 1)
        if line[:1] == " " and words and words[0].upper() == feature:
            return True
    return False


def mlst_facts(reply: str) -> "dict | None":
    """The facts of the entry in an `MLST` reply (RFC 3659 7.2: `250-` line,
    one entry line starting with a space, `250` line), or None when it holds
    no entry that says what it is."""
    for line in reply.splitlines()[1:]:
        if line[:1] == " ":
            _name, facts = _parse_fact_line(line[1:])
            return facts if "type" in facts else None
    return None


def _facts_mode(facts: dict, is_dir: bool) -> "int | None":
    """`st_mode` from MLSD facts: `unix.mode` verbatim when the server sends
    it, else the RFC 3659 `perm` fact (the login user's rights) as
    read/write bits. None when neither fact is present."""
    kind = _stat.S_IFDIR if is_dir else _stat.S_IFREG
    unix_mode = facts.get("unix.mode")
    if unix_mode:
        try:
            return kind | (int(unix_mode, 8) & 0o7777)
        except ValueError:
            pass
    perm = facts.get("perm")
    if perm is None:
        return None
    perm = set(perm.lower())
    if is_dir:
        bits = (0o555 if perm & set("el") else 0) | (0o200 if perm & set("cmp") else 0)
    else:
        bits = (0o444 if "r" in perm else 0) | (0o200 if perm & set("aw") else 0)
    return kind | bits


def facts_to_filestat(facts: dict) -> FileStat:
    """The `FileStat` of an entry described by MLSD/MLST `facts`."""
    # Fact values are case-insensitive (RFC 3659 7.5.1).
    kind = str(facts.get("type", "file")).lower()
    size = _facts_size(facts.get("size"))
    modify = facts.get("modify")
    mtime = _parse_mlsd_time(modify) if modify else 0
    is_dir = kind in ("dir", "cdir", "pdir")
    return FileStat(
        st_mode=_facts_mode(facts, is_dir),
        st_size=size,
        st_mtime=mtime,
        is_dir=is_dir,
    )
