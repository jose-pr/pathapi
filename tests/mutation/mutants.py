"""The mutation list: one single-line change to a guard of the library per
entry, and the tests that must fail because of it.

`run.py` applies each entry to a temporary copy of `src/pathapi`. `old`
is the exact text of the line (or lines) to replace and must occur once in
`file`, which is relative to `src/pathapi`; when the code moves on and
the text goes, the entry is reported stale until it is re-expressed. `tests`
are pytest arguments (node ids, or a file when any of its tests may be the
one to catch the mutant) that all pass on the unmutated code. README.md says
how to add an entry.
"""

from __future__ import annotations

from typing import NamedTuple


class Mutant(NamedTuple):
    id: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]
    note: str
    platform: str | None = None


MUTANTS: list[Mutant] = []


def mutant(
    id: str,
    file: str,
    old: str,
    new: str,
    tests,
    note: str,
    platform: str | None = None,
) -> None:
    """Add an entry to the list. `tests` is one pytest argument or a sequence;
    `platform` is the `os.name` its tests need ("nt"), or None for any."""
    if isinstance(tests, str):
        tests = (tests,)
    MUTANTS.append(Mutant(id, file, old, new, tuple(tests), note, platform))


# --- archive members ----------------------------------------------------------

mutant(
    "archive-rename-escaping-name",
    "archive/_write.py",
    '        if not new_path:\n            raise ValueError(\n                f"member name escapes the archive root: {target.as_posix()!r}"\n            )\n',
    '        if False:\n            raise ValueError(\n                f"member name escapes the archive root: {target.as_posix()!r}"\n            )\n',
    "tests/test_archive_safety.py::test_renaming_to_a_name_that_escapes_the_archive_is_refused_and_changes_nothing",
    "rename() accepts a target name that normalizes to nothing",
)

# --- copy(follow_symlinks=False) of a link -------------------------------------

mutant(
    "copy-link-onto-itself",
    "protocols/transfer.py",
    "        if _same_file(self, target):\n",
    "        if False:\n",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_onto_itself_is_refused_and_keeps_the_link",
    "copy(follow_symlinks=False) of a link onto itself unlinks and recreates it",
)
mutant(
    "copy-link-replaces-existing-link",
    "protocols/transfer.py",
    "            target.unlink(missing_ok=True)\n        target.symlink_to(link,",
    "            pass\n        target.symlink_to(link,",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_replaces_a_link_at_the_target_when_asked_to",
    "copy(overwrite=True) of a link leaves the old link in the way",
)
mutant(
    "copy-link-onto-directory",
    "protocols/transfer.py",
    "            if target_stat.is_dir() and not target_stat.is_symlink():\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "            if False:\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_never_removes_a_directory_at_the_target",
    "copy(overwrite=True) of a link tries to unlink a directory at the target",
)
mutant(
    "copy-link-without-readlink",
    "protocols/transfer.py",
    "        if not supports(self, Readlink):\n            raise NotImplementedError(",
    "        if False:\n            raise NotImplementedError(",
    "tests/test_copy_move_fidelity.py::test_a_link_of_a_backend_that_cannot_read_links_is_not_copied_as_content",
    "copy(follow_symlinks=False) of a link of a backend that cannot read links goes on",
)

# --- guards of the library -------------------------------------------------
mutant(
    "safe-child-name-accepts-everything",
    "_names.py",
    '    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:\n',
    '    return True\n    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:\n',
    "tests/test_destructive_safety.py::test_unsafe_child_names_rejected_on_every_flavour",
    "is_safe_child_name always True",
)
mutant(
    "rm-follows-a-directory-symlink",
    "protocols/tree.py",
    '                        if policy == "follow" and child.is_dir():\n',
    "                        if child.is_dir():\n",
    "tests/test_destructive_safety.py::test_rm_removes_a_link_and_a_binding_not_what_is_behind_them",
    "rm(recursive=True) descends into directory symlinks by default",
)
mutant(
    "sync-overlap-check-dropped",
    "sync/_syncer.py",
    "            elif _paths_overlap(source.path, target.path):\n",
    "            elif False:\n",
    "tests/test_sync_safety.py::test_source_inside_target_raises_and_source_survives",
    "PathSyncer: overlap check dropped",
)
mutant(
    "sync-missing-source-root-is-no-error",
    "sync/_syncer.py",
    "            error = None\n            if not source.exists():\n",
    "            error = None\n            if False:\n",
    "tests/test_sync_safety.py::test_missing_root_source_raises_and_keeps_target",
    "PathSyncer: missing root source no longer an error",
)
mutant(
    "copy-replaces-an-existing-target-by-default",
    "protocols/transfer.py",
    "            if not overwrite:\n                raise FileExistsError(target)\n\n        # Open the source",
    "            if False:\n                raise FileExistsError(target)\n\n        # Open the source",
    "tests/test_parity_io.py::test_copy_overwrite",
    "copy(): existing target replaced without overwrite=True",
)
mutant(
    "path-same-filesystem-always-true",
    "protocols/transfer.py",
    '        return getattr(self, "_backend", None) is getattr(other, "_backend", None)\n',
    "        return True\n",
    "tests/test_same_filesystem.py::test_the_default_tells_two_stores_apart_and_one_store_together",
    "Path._same_filesystem always True",
)
mutant(
    "mempath-same-filesystem-always-true",
    "mempath.py",
    "        return self._backend is other._backend\n",
    "        return True\n",
    "tests/test_same_filesystem.py::test_mempath_answers_by_backend_identity",
    "MemPath._same_filesystem always True",
)
mutant(
    "archive-name-with-leading-dotdot-is-dropped",
    "archive/_names.py",
    "                return None  # escapes the archive root\n",
    "                continue  # escapes the archive root\n",
    "tests/test_archive_safety.py::test_archive_listing_skips_escaping_member_names",
    "archive normalizer: an escaping '..' is dropped instead of rejecting the name",
)
mutant(
    "archive-member-safety-check-off",
    "archive/_names.py",
    "    if not _is_safe_member_name(normalized):\n",
    "    if False:\n",
    "tests/test_archive_safety.py::test_a_member_name_with_a_nul_is_refused_and_the_archive_is_unchanged",
    "archive normalizer: final safety check off",
)
mutant(
    "glob-wildcard-matches-dotfiles-when-hidden-excluded",
    "glob/_selection.py",
    '    skip_hidden = not opts.include_hidden and not part.startswith(".")\n',
    "    skip_hidden = False\n",
    "tests/test_parity_io.py::test_glob_hidden_included_by_default_like_pathlib",
    "glob: wildcard step no longer hides dotfiles",
)
mutant(
    "glob-recursive-wildcard-enters-hidden",
    "glob/_recursion.py",
    "            if not include_hidden and _is_hidden(child):\n",
    "            if False:\n",
    "tests/test_glob_parity.py::test_include_hidden_false_leaves_hidden_entries_out_of_a_final_recursive_wildcard",
    "glob: ** no longer hides dotfiles",
)
mutant(
    "lru-never-calls-on-evict",
    "_lru.py",
    "                on_evict(key, value)\n",
    "                pass\n",
    (
        "tests/test_providers.py::test_lru_still_evicts_the_oldest_over_a_positive_maxsize",
        "tests/test_ftp_security.py::test_ftps_verify_false_opt_out_accepts",
    ),
    "LRU never calls on_evict",
)
mutant(
    "copy-leaves-a-half-written-target",
    "protocols/_writing.py",
    "        if created and not discarding and supports(target, Remove):\n",
    "        if False:\n",
    "tests/test_destructive_safety.py::test_copy_failing_mid_stream_removes_partial_target",
    "copy(): half-written target left behind on failure",
)
mutant(
    "copy-recursive-accepts-unsafe-child-names",
    "protocols/_transfer.py",
    "    if not _names.is_safe_child_name(name, windows=windows):\n",
    "    if False and not _names.is_safe_child_name(name, windows=windows):\n",
    "tests/test_destructive_safety.py::test_copy_recursive_never_merges_names_windows_rewrites",
    "copy(recursive): unsafe child-name refusal off",
)
mutant(
    "sync-accepts-unsafe-child-names",
    "sync/_syncer.py",
    "                if _names.is_safe_child_name(name, windows=windows_target):\n",
    "                if True:\n",
    "tests/test_destructive_safety.py::test_sync_never_merges_names_windows_rewrites",
    "PathSyncer: unsafe child-name refusal off",
)
mutant(
    "sync-replaces-a-non-empty-target-directory",
    "sync/_syncer.py",
    "        if self.remove_missing or target.is_symlink() or not target.is_dir():\n",
    "        if True:\n",
    "tests/test_low_sync.py::test_dir_to_file_without_remove_missing_raises_and_keeps_tree",
    "PathSyncer: non-empty target directory replaced with remove_missing=False",
)
mutant(
    "symlink-to-force-swallows-unlink-errors-of-files",
    "protocols/links.py",
    "                if not self.is_dir():\n                    raise error\n",
    "                if False:\n                    raise error\n",
    "tests/test_path_gaps.py::test_symlink_to_force_reports_a_failed_unlink_of_a_file_and_makes_no_link",
    "symlink_to(force=True): unlink errors swallowed for non-directories too",
)
mutant(
    "move-directory-over-file-removes-the-directory",
    "protocols/transfer.py",
    "                    if not src_stat.is_dir():\n                        raise IsADirectoryError(target)\n",
    "                    if False:\n                        raise IsADirectoryError(target)\n",
    "tests/test_destructive_safety.py::test_move_file_onto_directory_refuses_and_keeps_both",
    "move(overwrite=True) of a file onto a directory rm -r's the directory",
)
mutant(
    "rm-follow-none-removes-the-entry",
    "protocols/_removal.py",
    '    if policy is None:\n        return "ignore"\n',
    '    if policy is None:\n        return "rm"\n',
    "tests/test_destructive_safety.py::test_rm_ignore_leaves_them_in_place",
    "rm(follow_symlinks=None / follow_binds=None) removes the entry instead of leaving it",
)
mutant(
    "ftps-data-connection-unencrypted",
    "ftp/_backend.py",
    "            if self.tls:\n                client.prot_p()\n",
    "            if False:\n                client.prot_p()\n",
    "tests/test_ftp_security.py::test_ftps_verify_false_opt_out_accepts",
    "ftps: data connections are no longer encrypted",
)
mutant(
    "ftps-certificate-verification-off",
    "ftp/_backend.py",
    "        if not self.verify:\n            context.check_hostname = False\n",
    "        if True:\n            context.check_hostname = False\n",
    "tests/test_ftp_security.py::test_ftps_self_signed_rejected_by_default_before_password",
    "ftps: certificate verification off by default",
)
mutant(
    "sync-removes-extras-without-remove-missing",
    "sync/_syncer.py",
    "            if self.remove_missing and not target_absent:\n",
    "            if not target_absent:\n",
    "tests/test_sync_safety.py::test_root_target_symlink_is_followed",
    "PathSyncer deletes target-only entries with remove_missing=False",
)
mutant(
    "sync-leaves-its-temp-file-after-a-failed-copy",
    "sync/_syncer.py",
    "                                temp.unlink(missing_ok=True)\n",
    "                                pass\n",
    "tests/test_sync_safety.py::test_unlink_refused_during_the_fallback_keeps_the_old_version",
    "PathSyncer leaves its temporary file behind after a failed copy",
)
mutant(
    "windows-child-name-with-a-drive-accepted",
    "_names.py",
    '        if "\\\\" in name or ":" in name:\n',
    '        if "\\\\" in name:\n',
    "tests/test_destructive_safety.py::test_windows_only_unsafe_child_names",
    "is_safe_child_name(windows=True) accepts 'D:x'",
)
mutant(
    "child-name-with-nul-accepted",
    "_names.py",
    '    if "\\0" in name:\n',
    "    if False:\n",
    "tests/test_destructive_safety.py::test_unsafe_child_names_rejected_on_every_flavour",
    "is_safe_child_name accepts a NUL",
)
mutant(
    "rm-missing-ok-is-ignored",
    "protocols/tree.py",
    "        if stat is None:\n            if not missing_ok:\n",
    "        if stat is None:\n            if True:\n",
    "tests/test_parity_io.py::test_rm_missing_ok",
    "rm(missing_ok=True) raises anyway",
)
mutant(
    "is-dir-binding-never-reports-a-binding",
    "protocols/fs.py",
    "                if test():\n                    return True\n",
    "                if test():\n                    return False\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "is_dir_binding() never reports a junction/mount",
    platform="nt",
)
mutant(
    "copy-onto-itself",
    "protocols/transfer.py",
    "        if _same_file(src, target, identity=False):\n",
    "        if False:\n",
    "tests/test_destructive_safety.py::test_copy_onto_itself_raises_and_keeps_content",
    "copy() of a file onto itself is not refused",
)
mutant(
    "copy-into-itself",
    "protocols/transfer.py",
    "            if contains(src, target):\n",
    "            if False:\n",
    "tests/test_destructive_safety.py::test_mem_copy_directory_into_a_relative_spelling_of_itself_refuses",
    "copy(recursive=True) of a directory into itself is not refused",
)
mutant(
    "copy-opens-the-source-after-removing-the-target",
    "protocols/transfer.py",
    '        with src.open("rb") as input:\n',
    '        with (target.unlink(missing_ok=True) if target_exists else None, src.open("rb"))[1] as input:\n',
    "tests/test_destructive_safety.py::test_copy_missing_source_leaves_existing_target_intact",
    "copy(overwrite=True) removes the target before it knows the source is readable",
)
mutant(
    "copy-applies-a-placeholder-mode",
    "protocols/_transfer.py",
    '        if getattr(stat, "mode_known", True) and stat.st_mode:\n',
    "        if stat.st_mode:\n",
    "tests/test_io_parity.py::test_copy_from_synthetic_mode_backend_leaves_target_writable",
    "copy() applies a backend's placeholder st_mode to the target",
)
mutant(
    "move-replaces-an-existing-target-by-default",
    "protocols/transfer.py",
    "            if target_stat is not None and not overwrite:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "            if False:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "tests/test_destructive_safety.py::test_move_directory_onto_its_ancestor_without_overwrite_raises_file_exists",
    "move() replaces an existing target without overwrite=True",
)
mutant(
    "move-onto-itself",
    "protocols/transfer.py",
    "        target_stat = None\n        if relation != _SAME:\n",
    "        target_stat = None\n        if True:\n",
    "tests/test_destructive_safety.py::test_move_directory_onto_itself_keeps_the_tree",
    "move() of a path onto itself removes it as an existing target",
)
mutant(
    "rm-descends-into-a-junction-or-mount",
    "protocols/tree.py",
    "                        if child.is_dir_binding():\n",
    "                        if False:\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "rm(recursive=True) empties what a junction or mount below the tree points at",
    platform="nt",
)
mutant(
    "rm-root-junction-or-mount-is-emptied",
    "protocols/tree.py",
    '            binding = self.is_dir_binding()\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    '            binding = False\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    "tests/test_destructive_safety.py::test_rm_recursive_on_junction_itself_removes_only_the_link",
    "rm(recursive=True) of a junction or mount empties what is behind it",
    platform="nt",
)
mutant(
    "rm-builds-a-child-from-an-unsafe-listed-name",
    "protocols/tree.py",
    '                    if not _names.is_safe_child_name(name):\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _entries._child(path, name)',
    '                    if False:\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _entries._child(path, name)',
    "tests/test_listing_names.py::test_rm_recursive_offers_each_unsafe_name_to_ignore_error",
    "rm(recursive=True) builds a child from a listed name such as '..'",
)
mutant(
    "walk-yields-an-unsafe-listed-name",
    "protocols/tree.py",
    "                if not _names.is_safe_child_name(name):\n                    # A listing is untrusted input",
    "                if False:\n                    # A listing is untrusted input",
    "tests/test_listing_names.py::test_walk_omits_unsafe_names_and_reports_them_to_on_error",
    "walk() descends into a listed name such as '..'",
)
mutant(
    "unpack-writes-outside-the-destination",
    "archive/_pack.py",
    "    if not parts or not all(_is_safe_name(p, windows, rewrites) for p in parts):\n",
    "    if not parts:\n",
    "tests/test_archive_safety.py::test_unpack_archive_never_writes_outside_dest",
    "unpack_archive() writes a member whose name has a '..' part",
)
mutant(
    "windows-child-name-with-trailing-dot-accepted",
    "_names.py",
    '        name[-1] in " ."\n        or name.partition',
    "        False\n        or name.partition",
    "tests/test_destructive_safety.py::test_windows_only_unsafe_child_names",
    "a destination read with Windows rules accepts 'report.', which is stored as 'report'",
)
mutant(
    "sync-overlap-is-not-resolved-through-links",
    "sync/_paths.py",
    "    return _relation(source, target, follow=True, identity=False) is not None\n",
    "    return _relation(source, target, follow=False, identity=False) is not None\n",
    "tests/test_sync_safety.py::test_overlap_hidden_behind_symlink_is_detected",
    "PathSyncer does not see an overlap that a symlink hides",
)
mutant(
    "glob-loop-guard-is-off",
    "glob/_recursion.py",
    "    if not bound_loops:\n        return True, ancestors\n    key = _identity(child)",
    "    if True:\n        return True, ancestors\n    key = _identity(child)",
    "tests/test_glob_parity.py::test_bound_loops_bounds_a_windows_junction_loop",
    "glob(bound_loops=True) descends into a directory that is its own ancestor",
    platform="nt",
)
mutant(
    "archive-link-beyond-another-link-is-entered",
    "archive/_path.py",
    "        if beyond or around:\n",
    "        if around:\n",
    "tests/test_archive_link_listing.py::test_nested_links_are_bounded_for_every_operation",
    "a following listing enters a link met below another link",
)
mutant(
    "archive-link-to-its-own-ancestor-is-entered",
    "archive/_path.py",
    "        if beyond or around:\n",
    "        if beyond:\n",
    "tests/test_archive_link_listing.py::test_a_following_walk_lists_a_link_to_its_own_directory_and_does_not_enter_it",
    "a following listing enters a link to the directory that holds it or above",
)
mutant(
    "archive-link-chain-is-unbounded",
    "archive/_snapshot.py",
    "                if followed > MAX_LINKS:\n",
    "                if followed > MAX_LINKS + 1:\n",
    ("tests/test_archive_links.py", "tests/test_archive_link_listing.py"),
    "a lookup follows one link more than the bound",
)

# --- ftp: FTPPath and FTPBackend -------------------------------------------------

_FTP_PATH = "tests/test_ftp_path.py"
mutant(
    "ftp-path-join-drops-the-backend",
    "ftp/_path.py",
    "        return type(self)(text, backend=self._backend)\n",
    '        return type(self)(text, host="other")\n',
    f"{_FTP_PATH}::test_every_path_derived_from_one_keeps_its_backend",
    "a path derived from an FTPPath connects through a backend of its own",
)
mutant(
    "ftp-repr-shows-the-password",
    "ftp/_backend.py",
    "        shown = [repr(self.host), str(self.port)]\n",
    '        shown = [repr(self.host), str(self.port), f"password={self.password!r}"]\n',
    f"{_FTP_PATH}::test_repr_names_the_endpoint_and_never_the_password",
    "repr() of an FTPBackend shows its password",
)
mutant(
    "ftp-backend-and-endpoint-keywords-together",
    "ftp/_path.py",
    "        if backend is not None and endpoint:\n",
    "        if False:\n",
    f"{_FTP_PATH}::test_a_backend_cannot_be_given_together_with_endpoint_keywords",
    "backend= and an endpoint keyword are both accepted, and one is ignored",
)
mutant(
    "ftp-close-leaves-the-connections-open",
    "ftp/_backend.py",
    "        keys = [key for key in _CACHED_CLIENTS.cache if key[0] is backend]\n",
    "        keys = []\n",
    (
        f"{_FTP_PATH}::test_close_ends_the_connections_the_backend_holds",
        f"{_FTP_PATH}::test_leaving_a_with_block_closes_the_connections",
    ),
    "FTPBackend.close() ends no connection",
)
mutant(
    "ftp-equal-endpoints-are-two-trees",
    "ftp/_path.py",
    "        return endpoint is not None and endpoint == theirs.endpoint\n",
    "        return False\n",
    f"{_FTP_PATH}::test_two_backends_of_one_endpoint_are_one_tree",
    "two paths to one server and account through two backends are not one tree",
)

# --- archive: ZipPath, TarPath and ArchivePath -----------------------------------

_ARCHIVE_PATH = "tests/test_archive_path.py"
mutant(
    "archive-join-takes-a-handle-of-its-own",
    "archive/_path.py",
    "            backend = donor._backend\n",
    "            backend = self.backend_for(donor.archive, shared=False)\n",
    f"{_ARCHIVE_PATH}::test_a_join_keeps_the_archive_and_its_handle",
    "a path joined from an archive path opens a handle of its own",
)
mutant(
    "archive-in-memory-archives-of-one-name-share-a-handle",
    "archive/_backend.py",
    "        return id(space), names\n",
    "        return outer.as_uri()\n",
    f"{_ARCHIVE_PATH}::test_two_in_memory_archives_with_one_name_are_two_archives",
    "two archives on different in-memory trees are one archive when they are spelled alike",
)
mutant(
    "archive-memory-archive-counts-as-writable",
    "archive/_zip.py",
    "        return _local_outer_path(self.outer) is not None\n",
    "        return True\n",
    f"{_ARCHIVE_PATH}::test_a_local_zip_is_written_in_place_and_a_memory_one_is_not",
    "a zip that is not a local file is written as if it were",
)
mutant(
    "archive-backend-for-always-shares",
    "archive/_path.py",
    "        if not shared:\n",
    "        if False:\n",
    f"{_ARCHIVE_PATH}::test_backend_for_shares_a_handle_unless_asked_for_its_own",
    "backend_for(shared=False) returns the shared handle",
)

# --- a write stream that commits on close, and a block that raised ---------------

_DISCARD = "tests/test_write_discard.py"
mutant(
    "write-block-that-raised-does-not-discard",
    "_commit.py",
    "        try:\n            if exc_type is not None:\n                self.discard()\n",
    "        try:\n            if False:\n                self.discard()\n",
    (
        f"{_DISCARD}::test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed",
        f"{_DISCARD}::test_commit_on_close_leaving_a_block_by_an_exception_commits_nothing",
        f"{_DISCARD}::test_ftp_a_failed_block_leaves_the_old_bytes",
        f"{_DISCARD}::test_zip_a_failed_block_leaves_the_member_it_would_have_replaced",
    ),
    "a binary write stream is closed, and so committed, when its with block raised",
)
mutant(
    "write-block-that-raised-does-not-discard-in-text-mode",
    "_discard.py",
    "        self._dropping = True\n        try:\n            self._discard()\n        finally:\n            super().__exit__(exc_type, exc_value, traceback)\n",
    "        self._dropping = True\n        try:\n            pass\n        finally:\n            super().__exit__(exc_type, exc_value, traceback)\n",
    (
        f"{_DISCARD}::test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed",
        f"{_DISCARD}::test_ftp_a_failed_block_does_not_create_the_file",
    ),
    "a text write stream is committed (empty) when its with block raised",
)
mutant(
    "text-wrapper-writes-the-text-of-a-failed-block",
    "_discard.py",
    "        if not self._dropping:\n            super().flush()\n",
    "        if True:\n            super().flush()\n",
    f"{_DISCARD}::test_text_pending_in_the_wrapper_never_reaches_the_stream_of_a_failed_block",
    "the text a wrapper holds is written to the stream before a failed block closes it",
)
mutant(
    "text-wrapper-that-cannot-flush-still-commits",
    "_discard.py",
    "            try:\n                self._discard()\n            finally:\n                super().close()\n            raise\n",
    "            try:\n                pass\n            finally:\n                super().close()\n            raise\n",
    f"{_DISCARD}::test_write_bytes_and_write_text_whose_write_fails_commit_nothing",
    "a text file whose last flush failed is committed with the text it did receive",
)
mutant(
    "open-gives-a-discarding-stream-a-stock-text-wrapper",
    "protocols/io.py",
    "            if callable(discard):\n",
    "            if False:\n",
    (
        f"{_DISCARD}::test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed",
        f"{_DISCARD}::test_a_text_file_over_a_stream_with_discard_is_discarded_without_the_mixin",
        f"{_DISCARD}::test_ftp_a_failed_block_does_not_create_the_file",
    ),
    "a text file over a stream with discard() is wrapped by a wrapper that flushes and commits",
)
mutant(
    "open-commits-a-stream-whose-text-wrapper-failed-to-build",
    "protocols/io.py",
    "            _discard.abandon(fh)\n",
    "            fh.close()\n",
    f"{_DISCARD}::test_a_wrapper_that_fails_to_build_discards_and_closes_the_stream",
    "an open() with an unknown encoding commits an empty file on a store that commits on close",
)
mutant(
    "commit-on-close-commits-after-a-discard",
    "_commit.py",
    "            if self._dirty and not self._discarded:\n",
    "            if self._dirty:\n",
    (
        f"{_DISCARD}::test_commit_on_close_drops_the_buffer_and_commits_nothing_after_a_discard",
        f"{_DISCARD}::test_commit_on_close_discard_on_a_file_opened_with_content_leaves_it_alone",
    ),
    "close() commits a buffer that was discarded",
)

_COMMIT = "tests/test_commit_on_close.py"
mutant(
    "unclosed-stream-is-committed-at-collection",
    "_commit.py",
    "            if not self.closed and self._holds_uncommitted():\n                self.discard()\n",
    "            if not self.closed and self._holds_uncommitted():\n                pass\n",
    (
        f"{_COMMIT}::test_an_unclosed_written_buffer_is_discarded_at_collection_with_a_warning",
        f"{_COMMIT}::test_an_unclosed_new_file_is_not_created_at_collection",
        f"{_COMMIT}::test_a_subclass_of_the_mixin_is_discarded_unclosed_unless_it_says_otherwise",
    ),
    "a stream collected unclosed commits what was never finished",
)
mutant(
    "unmodified-stream-warns-at-collection",
    "_commit.py",
    "        return self._dirty and not self._discarded\n",
    "        return True\n",
    (
        f"{_COMMIT}::test_an_unclosed_buffer_that_was_not_modified_is_collected_silently",
        f"{_COMMIT}::test_a_discarded_buffer_is_collected_silently",
    ),
    "a buffer with nothing to commit warns when collected",
)
mutant(
    "unclosed-text-file-is-committed-at-collection",
    "_discard.py",
    "                self._dropping = True\n                self._discard()\n                _warnings.warn(",
    "                _warnings.warn(",
    (
        f"{_COMMIT}::test_an_unclosed_text_file_over_a_buffer_is_discarded_at_collection",
    ),
    "a text file collected unclosed commits what it holds",
)

mutant(
    "commit-on-close-puts-bytesio-ahead-of-the-mixin",
    "_commit.py",
    "class CommitOnClose(DiscardOnError, _io.BytesIO):\n",
    "class CommitOnClose(_io.BytesIO, DiscardOnError):\n",
    (
        f"{_DISCARD}::test_commit_on_close_leaving_a_block_by_an_exception_commits_nothing",
        f"{_DISCARD}::test_ftp_a_failed_block_leaves_the_old_bytes",
    ),
    "the __exit__ of BytesIO closes, and so commits, a CommitOnClose whose block raised",
)
# --- pathapi.http: the guards of the transport, the client and the helpers ---

mutant(
    "client-404-is-a-generic-oserror",
    "http/_errors.py",
    "        if status in (404, 410):\n            raise os_error(FileNotFoundError, subject) from None\n",
    "        if status in (410,):\n            raise os_error(FileNotFoundError, subject) from None\n",
    "tests/transport/test_client_errors.py::test_a_status_becomes_the_exception_pathlib_raises_for_its_meaning",
    "HTTP 404 maps to generic OSError(EIO)",
)
mutant(
    "client-connection-error-chains-the-request-error",
    "http/_errors.py",
    '        raise ConnectionError(\n            f"Connection error for {subject} ({type(e).__name__})"\n        ) from None\n',
    '        raise ConnectionError(\n            f"Connection error for {subject} ({type(e).__name__})"\n        ) from e\n',
    "tests/transport/test_client_errors.py::test_a_refused_connection_carries_no_cause",
    "HTTP ConnectionError chains the requests exception (may carry proxy creds)",
)
mutant(
    "client-request-url-keeps-the-password",
    "http/_follow.py",
    "    return url[:start] + hostport + url[end:], (auth if any(auth) else None)\n",
    "    return url, (auth if any(auth) else None)\n",
    (
        "tests/transport/test_client_urls.py::test_userinfo_is_split_out_of_a_url_as_the_octets_it_stands_for",
        "tests/transport/test_client_urls.py::test_the_url_left_after_the_split_carries_no_password",
    ),
    "HTTP requests keep user:password in the request URL",
)
mutant(
    "client-403-is-a-generic-oserror",
    "http/_errors.py",
    "        elif status in (401, 403):\n",
    "        elif status in (401,):\n",
    "tests/transport/test_client_errors.py::test_a_status_becomes_the_exception_pathlib_raises_for_its_meaning",
    "HTTP 403 maps to generic OSError(EIO)",
)
mutant(
    "client-timeout-chains-the-request-error",
    "http/_errors.py",
    '        raise TimeoutError(f"Timeout for {subject} ({type(e).__name__})") from None\n',
    '        raise TimeoutError(f"Timeout for {subject} ({type(e).__name__})") from e\n',
    "tests/transport/test_client_errors.py::test_a_stalled_server_is_a_timeout_error_without_a_cause",
    "HTTP TimeoutError chains the requests exception",
)
mutant(
    "client-write-redirect-reads-as-success",
    "http/_follow.py",
    "    if not 300 <= resp.status < 400:\n        return resp\n    location = redirect_location(url, resp)",
    "    if True:\n        return resp\n    location = redirect_location(url, resp)",
    "tests/transport/test_client_write_redirects.py",
    "a write answered by a redirect that is not followed reads as done",
)
mutant(
    "client-write-follows-redirects-to-another-host",
    "http/_follow.py",
    '    elif origin(location) != origin(url):\n        reason = "it leaves the scheme, host and port of the request"\n',
    '    elif False:\n        reason = "it leaves the scheme, host and port of the request"\n',
    "tests/transport/test_client_write_redirects.py::test_307_to_another_scheme_host_or_port_is_not_followed",
    "a write is sent again to the host a 307 names, with its body and credentials",
)
mutant(
    "transport-provided-elsewhere-that-is-not-installed-does-not-raise",
    "http/_transports.py",
    "    if isinstance(name, str) and name in _PROVIDED:\n",
    "    if False:\n",
    "tests/transport/test_transport_registry.py::test_a_transport_provided_elsewhere_that_is_not_installed_names_the_distribution",
    "get('requests') answers with a ValueError instead of naming pathapi-requests",
)
mutant(
    "transport-entry-point-replaces-the-built-in-transport",
    "http/_transports.py",
    "    if name == _BUILTIN:\n",
    "    if False:\n",
    "tests/transport/test_transport_registry.py::test_an_entry_point_cannot_replace_the_built_in_transport",
    "a distribution that declares an entry called builtin replaces the built-in transport",
)
mutant(
    "prepare-request-keeps-the-fragment",
    "http/_transports.py",
    '    return PreparedRequest(method, url.split("#", 1)[0], sent, body, timeout, decode)\n',
    "    return PreparedRequest(method, url, sent, body, timeout, decode)\n",
    "tests/transport/test_transport_prepare.py::test_the_method_is_upper_case_and_the_fragment_is_dropped",
    "a transport built on prepare_request() sends the fragment of a URL",
)
mutant(
    "prepare-request-offers-no-content-coding",
    "http/_transports.py",
    '    if not _has_header(sent, "Accept-Encoding"):\n',
    "    if False:\n",
    "tests/transport/test_transport_prepare.py::test_gzip_and_deflate_are_asked_for_unless_the_caller_names_a_coding",
    "a transport built on prepare_request() does not ask for gzip or deflate",
)
mutant(
    "prepare-request-sends-bytes-without-a-length",
    "http/_transports.py",
    "        if isinstance(body, (bytes, bytearray, memoryview)):\n",
    "        if False:\n",
    "tests/transport/test_transport_prepare.py::test_a_body_of_known_length_is_sent_under_its_length",
    "a bytes body is sent without the Content-Length the contract asks for",
)
mutant(
    "prepare-request-does-not-say-a-put-has-no-body",
    "http/_transports.py",
    "        elif body is None and method in _EXPECTING_BODY:\n",
    "        elif False:\n",
    "tests/transport/test_transport_prepare.py::test_a_request_that_expects_a_body_and_has_none_says_so",
    "a PUT without a body is sent with no Content-Length",
)
mutant(
    "response-buffered-leaves-the-body-unread",
    "http/_response.py",
    "        return self._buffered()\n",
    "        return self\n",
    "tests/transport/test_transport_prepare.py::test_buffered_reads_the_body_closes_the_source_and_returns_the_response",
    "send(stream=False) returns a response whose body is still on the connection",
)
mutant(
    "client-authorization-kept-across-origins",
    "http/_follow.py",
    '        if origin(current) != home:\n            headers = drop_header(headers, "Authorization")\n',
    '        if False:\n            headers = drop_header(headers, "Authorization")\n',
    (
        "tests/transport/test_client_redirects.py::test_authorization_is_dropped_by_a_redirect_to_another_port",
        "tests/transport/test_client_redirects.py::test_authorization_follows_only_a_redirect_that_keeps_scheme_host_and_port",
    ),
    "a followed redirect to another scheme, host or port carries Authorization along",
)
mutant(
    "client-redirect-limit-off-by-one",
    "http/_follow.py",
    "        if hops >= MAX_REDIRECTS:",
    "        if hops > MAX_REDIRECTS:",
    "tests/transport/test_client_redirects.py::test_a_redirect_loop_stops_after_thirty_redirects",
    "a GET follows one redirect more than the limit",
)
mutant(
    "transport-unrepeatable-request-resent",
    "http/_builtin.py",
    "                if reused and repeatable:\n                    connection, reused = None, False\n                    continue\n                raise dropped.error from None",
    "                if reused:\n                    connection, reused = None, False\n                    continue\n                raise dropped.error from None",
    (
        "tests/transport/test_transport_pool.py::test_a_request_that_cannot_be_repeated_is_not_sent_again",
        "tests/transport/test_transport_pool.py::test_a_body_that_cannot_be_sent_twice_is_not_sent_again",
    ),
    "a POST, or a PUT with a file body, is sent again on a new connection when the server dropped the old one",
)
mutant(
    "transport-timeout-treated-as-a-dropped-connection",
    "http/_builtin.py",
    "            BrokenPipeError,\n            _ssl.SSLEOFError,",
    "            OSError,\n            _ssl.SSLEOFError,",
    "tests/transport/test_transport_pool.py::test_a_timeout_on_a_reused_connection_is_not_sent_again",
    "a request that timed out on a reused connection is sent again",
)
mutant(
    "transport-stale-connection-trusted-by-an-unrepeatable-request",
    "http/_builtin.py",
    "            and _now() - connection.released_at > REUSE_WINDOW",
    "            and _now() - connection.released_at > REUSE_WINDOW + 1e9",
    "tests/transport/test_transport_pool.py::test_a_request_that_cannot_be_repeated_does_not_trust_a_stale_connection",
    "a POST uses an idle connection however long ago it last carried a reply",
)
mutant(
    "transport-verification-off-by-default",
    "http/_builtin.py",
    '        verify: bool = True,\n        proxies: "_ty.Any" = None,',
    '        verify: bool = False,\n        proxies: "_ty.Any" = None,',
    "tests/transport/test_transport_tls.py::test_a_server_of_an_unknown_authority_is_refused_by_default",
    "the built-in transport accepts any certificate unless told to verify",
)
mutant(
    "transport-verify-false-beats-the-ssl-context",
    "http/_builtin.py",
    "        if self._ssl_context is not None:\n            return self._ssl_context",
    "        if self._ssl_context is not None and self._verify:\n            return self._ssl_context",
    "tests/transport/test_transport_tls.py::test_an_ssl_context_wins_over_verify_false",
    "verify=False replaces an ssl_context the caller gave",
)
mutant(
    "transport-proxy-credentials-in-a-repr",
    "http/_proxies.py",
    '        return f"ProxyAddress({self.host!r}, {self.port})"',
    '        return f"ProxyAddress({self.host!r}, {self.port}, {self.authorization!r})"',
    "tests/transport/test_transport_proxy.py::test_proxy_credentials_are_in_no_repr",
    "a proxy's Proxy-Authorization value shows in its repr",
)
mutant(
    "transport-proxy-credentials-in-a-message",
    "http/_proxies.py",
    '            f"the built-in HTTP transport has no {scheme!r} proxy support; "\n            + REQUESTS_HINT,',
    '            f"the built-in HTTP transport has no {scheme!r} proxy support ({url}); "\n            + REQUESTS_HINT,',
    "tests/transport/test_transport_proxy.py::test_a_proxy_scheme_that_is_not_http_is_refused_naming_the_other_transport",
    "the error for an unsupported proxy scheme quotes the proxy URL with its credentials",
)
mutant(
    "transport-header-line-break-accepted",
    "http/_headers.py",
    '_CONTROL = _re.compile(r"[\\x00-\\x08\\x0a-\\x1f\\x7f]")',
    '_CONTROL = _re.compile(r"[\\x00-\\x08\\x0b-\\x1f\\x7f]")',
    "tests/transport/test_transport_send.py::test_a_header_with_a_line_break_or_a_bad_name_is_refused_before_sending",
    "a header value with a bare line feed is sent",
)
mutant(
    "transport-url-line-break-accepted",
    "http/_headers.py",
    '_URL_CONTROL = _re.compile(r"[\\x00-\\x1f\\x7f]")',
    '_URL_CONTROL = _re.compile(r"[\\x00-\\x08\\x0b-\\x1f\\x7f]")',
    "tests/transport/test_transport_send.py::test_a_url_with_a_control_character_is_refused_before_sending",
    "a URL with a line break or a tab reaches the request line",
)
mutant(
    "transport-short-body-read-as-complete",
    "http/_builtin.py",
    "        if self._expected is not None and self._received < self._expected:",
    "        if False:",
    "tests/transport/test_transport_send.py::test_a_body_shorter_than_its_length_is_an_io_error_not_a_short_read",
    "a body that ends before its Content-Length reads as a complete one",
)
mutant(
    "transport-inflation-not-bounded",
    "http/_response.py",
    "_STEP = 256 * 1024",
    "_STEP = 1 << 40",
    "tests/transport/test_transport_send.py::test_a_small_body_that_inflates_enormously_is_read_in_bounded_steps",
    "a gzip body is inflated whole into memory because a header says it is small",
)
mutant(
    "transport-closed-stream-still-read",
    "http/_response.py",
    "        if self._closed and not self._kept:",
    "        if False:",
    "tests/transport/test_transport_send.py::test_reading_a_streamed_reply_after_closing_it_is_an_error",
    "a closed streaming reply keeps handing out bytes",
)
mutant(
    "transport-idle-connection-limit",
    "http/_pool.py",
    "MAX_IDLE_PER_THREAD = 8",
    "MAX_IDLE_PER_THREAD = 100",
    "tests/transport/test_transport_pool.py::test_a_thread_keeps_at_most_eight_idle_connections",
    "a thread keeps more than eight idle connections",
)
mutant(
    "client-default-transport-is-requests",
    "http/_transports.py",
    "    kind = _BUILTIN if value is None else value\n",
    '    kind = "requests" if value is None else value\n',
    "tests/transport/test_client_default.py::test_without_a_setting_the_built_in_transport_is_used",
    "a backend without a transport builds a requests session",
)
mutant(
    "transport-idle-connection-used-without-a-check",
    "http/_builtin.py",
    "        if connection is not None and _unusable(connection.http.sock):\n",
    "        if False:\n",
    (
        "tests/transport/test_transport_pool.py::test_bytes_the_server_sends_after_a_reply_are_not_the_next_reply",
        "tests/transport/test_transport_pool.py::test_an_idle_connection_the_server_closed_is_replaced_before_sending",
    ),
    "an idle connection the server closed, or sent extra bytes on, carries the next request",
)
mutant(
    "transport-closed-connection-reopens-itself",
    "http/_builtin.py",
    "        http.auto_open = 0\n",
    "        http.auto_open = 1\n",
    (
        "tests/transport/test_transport_pool.py::test_a_connection_closed_before_the_request_is_written_is_not_reopened",
        "tests/transport/test_transport_pool.py::test_a_transport_closed_before_the_request_is_written_does_not_send_it",
        "tests/transport/test_transport_pool.py::test_a_body_that_is_being_sent_when_the_transport_is_closed_is_not_finished_in_the_clear",
    ),
    "http.client reconnects a closed connection by itself, in plain TCP outside the TLS session and the proxy",
)
mutant(
    "transport-interim-reply-taken-as-the-final-one",
    "http/_builtin.py",
    "            if status == _client.CONTINUE:\n                return version, status, reason  # `begin()` skips its headers\n",
    "            if True:\n                return version, status, reason  # `begin()` skips its headers\n",
    (
        "tests/transport/test_transport_send.py::test_the_built_in_transport_skips_interim_replies_and_returns_the_final_one",
        "tests/transport/test_transport_send.py::test_an_interim_reply_is_never_the_previous_answer_of_the_next_request",
    ),
    "a 103 Early Hints is the reply, and the real one is read as the answer to the next request",
)
mutant(
    "transport-interim-replies-not-counted",
    "http/_builtin.py",
    "            if self._interim > MAX_INTERIM_REPLIES:\n",
    "            if False:\n",
    (
        "tests/transport/test_transport_send.py::test_a_bounded_number_of_interim_replies_is_skipped",
        "tests/transport/test_transport_send.py::test_an_endless_stream_of_100_continue_is_given_up_not_waited_out",
    ),
    "a server that answers with endless interim replies is waited on without limit",
)
mutant(
    "transport-no-proxy-cidr-ignored",
    "http/_proxies.py",
    '        if _in_no_proxy_network(host, chosen.get("no", "")):\n',
    "        if False:\n",
    "tests/transport/test_transport_proxy.py::test_a_cidr_block_in_no_proxy_sends_an_ip_literal_host_direct",
    "a request to a host inside a NO_PROXY block goes to the proxy, which sees its Authorization",
)
mutant(
    "client-peer-text-not-escaped",
    "http/_headers.py",
    '        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")\n',
    "        char\n",
    "tests/transport/test_client_peer_text.py::test_peer_text_escapes_what_a_terminal_would_act_on",
    "text from the peer keeps its control characters in an error message",
)
mutant(
    "client-reason-phrase-unescaped",
    "http/_errors.py",
    '        reason = peer_text(response.reason or "")\n',
    '        reason = response.reason or ""\n',
    (
        "tests/transport/test_client_peer_text.py::test_a_reason_phrase_is_shown_escaped",
        "tests/transport/test_client_peer_text.py::test_a_long_reason_phrase_is_cut",
        "tests/transport/test_client_errors.py::test_a_reply_status_error_text_has_the_reason_escaped",
    ),
    "an HTTP error's reason phrase reaches the message as the server sent it",
)
mutant(
    "client-refused-redirect-location-unbounded",
    "http/_follow.py",
    '    where = peer_text(shown(location), 200) if location else "(none)"\n',
    '    where = shown(location) if location else "(none)"\n',
    "tests/transport/test_client_peer_text.py::test_a_redirect_that_is_refused_is_shown_cut_and_with_an_escaped_reason",
    "a refused redirect's message holds the whole Location",
)
mutant(
    "client-redirect-reask-forgets-the-origin",
    "http/_client.py",
    "            method, location, sent, None, None, stream, timeout, first=url\n",
    "            method, location, sent, None, None, stream, timeout, first=None\n",
    "tests/transport/test_client_redirect_origin.py::test_a_request_asked_again_at_another_origin_does_not_carry_authorization",
    "stat() and what is built on it re-ask the redirect target as if it were the first origin, with Authorization",
)
mutant(
    "client-redirect-reask-sends-the-client-headers-again",
    "http/_follow.py",
    "        if origin(current) != home:\n",
    "        if origin(current) != origin(url):\n",
    "tests/transport/test_client_redirect_origin.py::test_a_request_asked_again_at_another_origin_does_not_carry_authorization",
    "a re-request compares the redirect target with itself instead of with the origin it was asked for",
)
mutant(
    "client-unusable-location-reaches-the-transport",
    "http/_follow.py",
    "        check_url(absolute)\n        return absolute\n",
    "        return absolute\n",
    "tests/transport/test_client_redirects.py::test_a_location_the_url_check_refuses_is_oserror_never_valueerror",
    "a Location the URL check refuses is sent on, and its ValueError reaches the caller",
)
mutant(
    "client-redirect-without-a-usable-location-is-the-reply",
    "http/_follow.py",
    '        if location is None:\n            _discard(resp)\n            raise refused_redirect(method, current, resp, None, "no usable Location")\n        if hops >= MAX_REDIRECTS:',
    "        if location is None:\n            return resp\n        if hops >= MAX_REDIRECTS:",
    "tests/transport/test_client_redirects.py::test_a_location_that_is_not_an_http_url_is_a_refused_redirect",
    "a redirect with an unusable Location reads as the answer itself",
)
mutant(
    "client-reply-limit-not-applied",
    "http/_response.py",
    "            if limit is not None and len(data) > limit:\n",
    "            if False:\n",
    (
        "tests/transport/test_client_reply_limits.py::test_a_reply_over_the_limit_is_efbig_counted_in_decoded_bytes",
        "tests/transport/test_client_reply_limits.py::test_the_client_reads_a_reply_whole_only_up_to_the_limit",
    ),
    "a reply read whole is held in memory however long it decodes to",
)
mutant(
    "client-dropped-reply-read-without-a-bound",
    "http/_status.py",
    "            remaining -= len(chunk)\n",
    "            pass\n",
    "tests/transport/test_client_reply_limits.py::test_a_dropped_reply_over_the_limit_does_not_keep_its_connection",
    "the body of a reply nobody looks at is read to its end, however long",
)
mutant(
    "client-json-reply-read-unbounded",
    "http/_status.py",
    "    return _json.loads(read_limited(resp, what, limit))",
    "    return _json.loads(resp.read())",
    "tests/transport/test_client_reply_limits.py::test_a_json_reply_over_the_limit_is_efbig",
    "a git API's JSON reply is read whole whatever its size",
)
mutant(
    "client-buffers-without-a-cap",
    "http/_client.py",
    "            resp._buffered(limit, too_large(_follow.shown(url), limit))\n",
    "            resp._buffered()\n",
    "tests/transport/test_client_reply_limits.py::test_the_client_reads_a_reply_whole_only_up_to_the_limit",
    "request(stream=False) reads a reply to its end whatever its size",
)
mutant(
    "transport-exchange-runs-under-the-connect-timeout",
    "http/_builtin.py",
    "                sock.settimeout(read_timeout)\n",
    "                sock.settimeout(connect_timeout)\n",
    "tests/transport/test_transport_send.py::test_a_server_that_never_answers_is_a_timeout_after_the_read_timeout",
    "the wait for a reply is bounded by the connect half of the timeout",
)
mutant(
    "transport-body-length-ignores-the-position",
    "http/_body.py",
    "        return max(0, end - here)\n",
    "        return max(0, end)\n",
    (
        "tests/transport/test_transport_body.py::test_a_bytes_io_part_way_sends_what_is_left_under_that_length",
        "tests/transport/test_transport_body.py::test_an_open_file_part_way_sends_what_is_left_under_that_length",
    ),
    "a file read from the middle is sent under the length of the whole file",
)
mutant(
    "transport-body-sent-past-its-length",
    "http/_body.py",
    "        want = self._left if amount is None or amount < 0 else min(amount, self._left)\n",
    "        want = self._left if amount is None or amount < 0 else amount\n",
    (
        "tests/transport/test_transport_body.py::test_a_file_that_grew_after_it_was_measured_sends_only_the_length_it_stated",
        "tests/transport/test_transport_body.py::test_a_length_the_caller_set_cuts_a_longer_body_at_that_length",
    ),
    "a body longer than its Content-Length sends the rest into the next request",
)
mutant(
    "transport-body-short-of-its-length-is-not-an-error",
    "http/_body.py",
    "        if not chunk:\n            raise OSError(\n",
    "        if False:\n            raise OSError(\n",
    (
        "tests/transport/test_transport_body.py::test_a_file_that_ends_before_the_length_it_stated_is_an_error",
        "tests/transport/test_transport_body.py::test_a_generator_that_ends_before_the_length_the_caller_set_is_an_error",
    ),
    "a body that ends before its Content-Length leaves the server waiting for the rest",
)
mutant(
    "transport-length-larger-than-the-body-is-sent",
    "http/_body.py",
    "        if known is not None and declared > known:\n",
    "        if False:\n",
    "tests/transport/test_transport_body.py::test_a_length_larger_than_a_body_whose_size_is_known_is_refused",
    "a Content-Length larger than a file or list is sent and the upload stalls",
)

# --- the core's two documented-behaviour guards -------------------------------

mutant(
    "touch-drops-a-mode-the-backend-cannot-apply",
    "protocols/fs.py",
    "                # The caller asked for a mode this backend cannot apply: leave\n"
    "                # no file that has other permissions than the ones asked for.\n"
    "                try:\n"
    "                    self.unlink()\n"
    "                except (OSError, NotImplementedError):\n"
    "                    pass\n"
    "                raise\n",
    "                pass\n",
    (
        "tests/test_io_parity.py::test_generic_touch_with_a_mode_the_backend_cannot_apply_raises",
        "tests/test_io_parity.py::test_generic_touch_with_a_mode_the_backend_cannot_apply_leaves_no_file",
    ),
    "touch(mode=...) on a backend without chmod() returns as if the mode had been applied",
)
mutant(
    "pathname-is-an-os-pathlike-without-fspath",
    "path/_pure.py",
    "class Pathname(_ty.Generic[_P]):",
    "class Pathname(FsPathLike, _ty.Generic[_P]):",
    "tests/test_pathname.py::test_a_path_without_fspath_is_not_an_os_pathlike",
    "every Pathname is an os.PathLike whose __fspath__ raises NotImplementedError",
)
mutant(
    "move-from-a-source-that-cannot-be-removed",
    "protocols/transfer.py",
    "        if not supports(src, Remove) and not (native and supports(src, Move)):\n",
    "        if False:\n",
    "tests/test_consumer_needs.py::test_move_from_a_class_without_remove_raises_before_the_target_exists",
    "move() copies a file it cannot then remove, leaving two",
)
mutant(
    "copy-writes-an-existing-target-in-place",
    "protocols/_writing.py",
    "    if not replacing:\n",
    "    if True:\n",
    "tests/test_copy_overwrite.py::test_a_source_failing_half_way_leaves_the_old_target",
    "copy(overwrite=True) truncates the old target before the new content is secured",
)
mutant(
    "copy-removes-a-target-that-commits-on-close",
    "protocols/_writing.py",
    "        if created and not discarding and supports(target, Remove):\n",
    "        if created and supports(target, Remove):\n",
    "tests/test_copy_overwrite.py::test_a_target_that_cannot_rename_keeps_its_content_when_the_source_fails",
    "a failed copy removes the old file of a class that commits on close",
)
mutant(
    "copy-from-accepts-unsafe-child-names",
    "protocols/transfer.py",
    "                _require_safe_child(self, child.name, windows)\n",
    "                pass\n",
    "tests/test_copy_from_names.py",
    "_copy_from() joins a listed name that is not one safe component",
)
