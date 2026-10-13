"""The mutation list: one single-line change to a guard of the library per
entry, and the tests that must fail because of it.

`run.py` applies each entry to a temporary copy of `src/pathlib_next`. `old`
is the exact text of the line (or lines) to replace and must occur once in
`file`, which is relative to `src/pathlib_next`; when the code moves on and
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

# --- s3: each request's failure is translated, naming the path ---------------

_S3 = "uri/schemes/s3.py"
_S3_TABLE = "tests/test_s3_errors.py::test_a_failed_request_is_an_oserror_naming_the_path_and_changes_nothing"
_RAISE = "raise _oserror(error, self) from None"

mutant(
    "s3-exclusive-create-store-error",
    _S3,
    f"            if _is_not_implemented(error):\n                return False\n            {_RAISE}\n",
    "            if _is_not_implemented(error):\n                return False\n            raise\n",
    _S3_TABLE,
    "open('x') lets the SDK's ClientError out untranslated",
)
mutant(
    "s3-exclusive-create-transport-error",
    _S3,
    f"        except _TRANSPORT_ERRORS as error:\n            {_RAISE}\n        return True\n\n    def _stat_root",
    "        except _TRANSPORT_ERRORS as error:\n            raise\n        return True\n\n    def _stat_root",
    _S3_TABLE,
    "open('x') lets a broken connection out untranslated",
)
mutant(
    "s3-stat-bucket-error",
    _S3,
    f"            self._client.head_bucket(Bucket=self.bucket)\n        except _S3_ERRORS as error:\n            {_RAISE}\n",
    "            self._client.head_bucket(Bucket=self.bucket)\n        except _S3_ERRORS as error:\n            raise\n",
    _S3_TABLE,
    "stat() of a bucket lets the SDK's error out untranslated",
)
mutant(
    "s3-checksum-store-error",
    _S3,
    f"            if _is_not_found(error):\n                return None\n            {_RAISE}\n",
    "            if _is_not_found(error):\n                return None\n            raise\n",
    _S3_TABLE,
    "checksum() lets the SDK's ClientError out untranslated",
)
mutant(
    "s3-checksum-transport-error",
    _S3,
    f"        except _TRANSPORT_ERRORS as error:\n            {_RAISE}\n        digests = {{}}",
    "        except _TRANSPORT_ERRORS as error:\n            raise\n        digests = {}",
    _S3_TABLE,
    "checksum() lets a broken connection out untranslated",
)
mutant(
    "s3-existence-check-error",
    _S3,
    f"            {_RAISE}\n        return True\n\n    def _has_prefix",
    "            raise\n        return True\n\n    def _has_prefix",
    _S3_TABLE,
    "the existence check of a key lets the SDK's error out untranslated",
)
mutant(
    "s3-first-keys-error",
    _S3,
    f"                Bucket=self.bucket, Prefix=prefix, MaxKeys=limit\n            )\n        except _S3_ERRORS as error:\n            {_RAISE}\n",
    "                Bucket=self.bucket, Prefix=prefix, MaxKeys=limit\n            )\n        except _S3_ERRORS as error:\n            raise\n",
    "tests/test_s3_errors.py::test_a_listing_that_fails_while_removing_a_directory_keeps_it",
    "rmdir() lets a failed listing of the directory's keys out untranslated",
)
mutant(
    "s3-marker-write-error",
    _S3,
    f'                self._client.put_object(Bucket=self.bucket, Key=key, Body=b"")\n            except _S3_ERRORS as error:\n                {_RAISE}\n',
    '                self._client.put_object(Bucket=self.bucket, Key=key, Body=b"")\n            except _S3_ERRORS as error:\n                raise\n',
    "tests/test_s3_errors.py::test_a_marker_write_that_fails_after_conditional_writes_are_refused_is_translated",
    "mkdir() lets the failed marker write out untranslated",
)
mutant(
    "s3-delete-object-error",
    _S3,
    f"            self._client.delete_object(Bucket=self.bucket, Key=key)\n        except _S3_ERRORS as error:\n            {_RAISE}\n",
    "            self._client.delete_object(Bucket=self.bucket, Key=key)\n        except _S3_ERRORS as error:\n            raise\n",
    _S3_TABLE,
    "unlink() lets the SDK's error out untranslated",
)
mutant(
    "s3-batch-delete-declined-error-swallowed",
    _S3,
    "                if not on_error(translated):\n                    raise translated from None\n                continue\n",
    "                if not on_error(translated):\n                    pass\n                continue\n",
    _S3_TABLE,
    "a failed batch delete that the caller did not tolerate is skipped",
)
mutant(
    "s3-batch-delete-error-not-offered",
    _S3,
    "                translated = _oserror(error, self)\n                if not on_error(translated):",
    "                translated = _oserror(error, self)\n                if True:",
    "tests/test_s3_errors.py::test_a_batch_delete_that_fails_is_offered_translated_and_the_walk_goes_on",
    "a failed batch delete is raised although the caller tolerates it",
)
mutant(
    "s3-rename-source-error",
    _S3,
    f"                return None\n            {_RAISE}\n\n    def _rename_copy",
    "                return None\n            raise\n\n    def _rename_copy",
    _S3_TABLE,
    "rename() lets the SDK's error for the source's HEAD out untranslated",
)
mutant(
    "s3-rename-copy-error",
    _S3,
    f"            client.delete_object(Bucket=self.bucket, Key=self.key)\n        except _S3_ERRORS as error:\n            {_RAISE}\n",
    "            client.delete_object(Bucket=self.bucket, Key=self.key)\n        except _S3_ERRORS as error:\n            raise\n",
    _S3_TABLE,
    "rename() lets the SDK's error for the copy out untranslated",
)
mutant(
    "objstore-recursive-rm-existence-error-reads-as-missing",
    "uri/schemes/_objstore.py",
    "            if not on_error(error):\n                raise\n            return\n\n        if not keys:\n            marker",
    "            pass\n\n        if not keys:\n            marker",
    "tests/test_s3_errors.py::test_a_failed_existence_check_stops_a_recursive_remove_before_any_delete",
    "rm(recursive=True) reads a failed existence check as 'no such object' and deletes the prefix tree",
)

# --- copy(follow_symlinks=False) of a link -------------------------------------

mutant(
    "copy-link-onto-itself",
    "path/_path.py",
    "        if _same_file(self, target):\n",
    "        if False:\n",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_onto_itself_is_refused_and_keeps_the_link",
    "copy(follow_symlinks=False) of a link onto itself unlinks and recreates it",
)
mutant(
    "copy-link-replaces-existing-link",
    "path/_path.py",
    "            target.unlink(missing_ok=True)\n        target.symlink_to(link,",
    "            pass\n        target.symlink_to(link,",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_replaces_a_link_at_the_target_when_asked_to",
    "copy(overwrite=True) of a link leaves the old link in the way",
)
mutant(
    "copy-link-onto-directory",
    "path/_path.py",
    "            if target_stat.is_dir() and not target_stat.is_symlink():\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "            if False:\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_never_removes_a_directory_at_the_target",
    "copy(overwrite=True) of a link tries to unlink a directory at the target",
)
mutant(
    "copy-link-without-readlink",
    "path/_path.py",
    "        if not callable(readlink):\n            raise NotImplementedError(",
    "        if False:\n            raise NotImplementedError(",
    "tests/test_copy_move_fidelity.py::test_a_link_of_a_backend_that_cannot_read_links_is_not_copied_as_content",
    "copy(follow_symlinks=False) of a link of a backend that cannot read links goes on",
)

# --- http: and dav: deletes ---------------------------------------------------

mutant(
    "http-unlink-of-a-directory",
    "uri/schemes/http.py",
    "        if self.is_dir():\n            raise IsADirectoryError(\n",
    "        if False:\n            raise IsADirectoryError(\n",
    "tests/test_http.py::test_http_unlink_on_directory_sends_no_delete",
    "HttpPath.unlink() sends DELETE for a collection",
)
mutant(
    "http-rmdir-of-a-file",
    "uri/schemes/http.py",
    "        if not self.is_dir():\n            raise NotADirectoryError(self)\n",
    "        if False:\n            raise NotADirectoryError(self)\n",
    "tests/test_http.py::test_http_rmdir_on_file_raises_notadirectoryerror",
    "HttpPath.rmdir() sends DELETE for a file",
)
mutant(
    "http-rmdir-of-a-non-empty-directory",
    "uri/schemes/http.py",
    '        for _ in self._scandir():\n            raise OSError(_errno.ENOTEMPTY, "Directory not empty", str(self))\n        self._delete()\n',
    "        self._delete()\n",
    "tests/test_http.py::test_http_rmdir_not_empty",
    "HttpPath.rmdir() sends DELETE for a directory that lists children",
)
mutant(
    "http-delete-answered-404-with-missing-ok",
    "uri/schemes/http.py",
    "                if missing_ok:\n                    return\n                raise FileNotFoundError(self)\n",
    "                if False:\n                    return\n                raise FileNotFoundError(self)\n",
    "tests/test_http.py::test_http_unlink_missing_ok_accepts_a_delete_answered_404",
    "unlink(missing_ok=True) raises for a DELETE answered 404",
)
mutant(
    "dav-delete-answered-404-without-missing-ok",
    "uri/schemes/dav.py",
    "        except FileNotFoundError:\n            if not missing_ok:\n                raise\n",
    "        except FileNotFoundError:\n            if False:\n                raise\n",
    "tests/test_webdav.py::test_unlink_without_missing_ok_raises_for_a_delete_answered_404",
    "unlink() swallows a DELETE answered 404 although missing_ok is False",
)

# --- guards of the library, from the first review of the suite -------------
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
    "path/_path.py",
    '                        if policy == "follow" and child.is_dir():\n',
    "                        if child.is_dir():\n",
    "tests/test_destructive_safety.py::test_file_uri_rm_recursive_does_not_follow_directory_symlink",
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
    "source-repr-shows-the-password",
    "uri/source.py",
    'f"userinfo={self._redacted_userinfo()!r}, host={self.host!r}, "',
    'f"userinfo={self.userinfo!r}, host={self.host!r}, "',
    "tests/test_uri_parse.py::test_source_str_and_repr_redact_password",
    "Source.__repr__ shows the password",
)
mutant(
    "source-str-shows-the-password",
    "uri/source.py",
    "        return self.as_str(sanitize=True)\n",
    "        return self.as_str(sanitize=False)\n",
    "tests/test_uri_parse.py::test_source_str_and_repr_redact_password",
    "Source.__str__ shows the password",
)
mutant(
    "sftp-listing-keeps-unsafe-names",
    "uri/schemes/sftp/__init__.py",
    "            if not _providers.is_safe_child_name(name):\n",
    "            if False:\n",
    "tests/test_sftp.py::test_a_hostile_listing_name_never_becomes_a_child",
    "SftpPath listing filter no-op",
)
mutant(
    "http-listing-keeps-unsafe-names",
    "uri/schemes/http.py",
    "            if not _providers.is_safe_child_name(name):\n",
    "            if False:\n",
    "tests/test_http.py::test_http_listing_leaves_out_a_name_that_is_not_a_safe_component",
    "HttpPath listing filter no-op",
)
mutant(
    "dav-listing-keeps-unsafe-names",
    "uri/schemes/dav.py",
    "            if _providers.is_safe_child_name(name):\n",
    "            if True:\n",
    "tests/test_webdav.py::test_propfind_dot_entries_are_not_children",
    "DavPath listing filter no-op",
)
mutant(
    "copy-replaces-an-existing-target-by-default",
    "path/_path.py",
    "            if not overwrite:\n                raise FileExistsError(target)\n\n        # Open the source",
    "            if False:\n                raise FileExistsError(target)\n\n        # Open the source",
    "tests/test_parity_io.py::test_copy_overwrite",
    "copy(): existing target replaced without overwrite=True",
)
mutant(
    "path-same-filesystem-always-true",
    "path/_path.py",
    '        return getattr(self, "_backend", None) is getattr(other, "_backend", None)\n',
    "        return True\n",
    "tests/test_same_filesystem.py::test_the_default_tells_two_stores_apart_and_one_store_together",
    "Path._same_filesystem always True",
)
mutant(
    "uri-same-filesystem-always-true",
    "uri/__init__.py",
    "        return mine is None or theirs is None or mine is theirs\n",
    "        return True\n",
    "tests/test_same_filesystem_uri.py::test_two_supplied_backends_are_two_hosts",
    "UriPath._same_filesystem always True",
)
mutant(
    "uri-same-filesystem-always-false",
    "uri/__init__.py",
    "        return mine is None or theirs is None or mine is theirs\n",
    "        return False\n",
    "tests/test_same_filesystem_uri.py::test_separately_built_paths_to_one_url_are_the_same_file",
    "UriPath._same_filesystem always False (self-copy guard off for remote)",
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
    "            if not include_hidden and child.is_hidden():\n",
    "            if False:\n",
    "tests/test_glob_parity.py::test_include_hidden_false_leaves_hidden_entries_out_of_a_final_recursive_wildcard",
    "glob: ** no longer hides dotfiles",
)
mutant(
    "http-404-is-a-generic-oserror",
    "uri/schemes/http.py",
    "        if status in (404, 410):\n            raise _path_error(FileNotFoundError, path_obj) from None\n",
    "        if status in (410,):\n            raise _path_error(FileNotFoundError, path_obj) from None\n",
    "tests/test_destructive_safety.py::test_copy_http_404_source_leaves_local_target_intact",
    "HTTP 404 maps to generic OSError(EIO)",
)
mutant(
    "http-connection-error-chains-the-request-error",
    "uri/schemes/http.py",
    '        raise ConnectionError(\n            f"Connection error for {path_obj} ({type(e).__name__})"\n        ) from None\n',
    '        raise ConnectionError(\n            f"Connection error for {path_obj} ({type(e).__name__})"\n        ) from e\n',
    "tests/test_transport_security.py::test_http_refused_connection_carries_no_password",
    "HTTP ConnectionError chains the requests exception (may carry proxy creds)",
)
mutant(
    "lru-never-calls-on-evict",
    "_lru.py",
    "                on_evict(key, value)\n",
    "                pass\n",
    "tests/test_transport_security.py::test_ftps_verify_false_opt_out_accepts",
    "LRU never calls on_evict",
)
mutant(
    "copy-leaves-a-half-written-target",
    "path/_path.py",
    "                if created:\n",
    "                if False:\n",
    "tests/test_destructive_safety.py::test_copy_failing_mid_stream_removes_partial_target",
    "copy(): half-written target left behind on failure",
)
mutant(
    "copy-recursive-accepts-unsafe-child-names",
    "path/_path.py",
    "                    if not _names.is_safe_child_name(\n",
    "                    if False and not _names.is_safe_child_name(\n",
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
    "path/_path.py",
    "                if not self.is_dir():\n                    raise error\n",
    "                if False:\n                    raise error\n",
    "tests/test_path_gaps.py::test_symlink_to_force_reports_a_failed_unlink_of_a_file_and_makes_no_link",
    "symlink_to(force=True): unlink errors swallowed for non-directories too",
)
mutant(
    "move-directory-over-file-removes-the-directory",
    "path/_path.py",
    "                    if not src_stat.is_dir():\n                        raise IsADirectoryError(target)\n",
    "                    if False:\n                        raise IsADirectoryError(target)\n",
    "tests/test_destructive_safety.py::test_move_file_onto_directory_refuses_and_keeps_both",
    "move(overwrite=True) of a file onto a directory rm -r's the directory",
)
mutant(
    "rename-accepts-another-location",
    "uri/__init__.py",
    "        if not self._same_location(result):\n",
    "        if False:\n",
    "tests/test_routing.py::test_ftp_rename_to_another_server_raises_not_implemented",
    "rename() no longer refuses a target on another host/bucket",
)
mutant(
    "rm-follow-none-removes-the-entry",
    "path/_removal.py",
    '    if policy is None:\n        return "ignore"\n',
    '    if policy is None:\n        return "rm"\n',
    "tests/test_destructive_safety.py::test_rm_ignore_leaves_them_in_place",
    "rm(follow_symlinks=None / follow_binds=None) removes the entry instead of leaving it",
)
mutant(
    "paramiko-accepts-unknown-host-keys-by-default",
    "uri/schemes/sftp/_paramiko.py",
    "            _paramiko.RejectPolicy() if hostkeypolicy is None else hostkeypolicy\n",
    "            _paramiko.AutoAddPolicy() if hostkeypolicy is None else hostkeypolicy\n",
    "tests/test_sftp_errors.py::test_an_unknown_host_key_is_a_host_key_error",
    "paramiko backend accepts unknown host keys by default",
)
mutant(
    "ftps-data-connection-unencrypted",
    "ftp/_backend.py",
    "            if self.tls:\n                client.prot_p()\n",
    "            if False:\n                client.prot_p()\n",
    "tests/test_transport_security.py::test_ftps_verify_false_opt_out_accepts",
    "ftps: data connections are no longer encrypted",
)
mutant(
    "ftps-certificate-verification-off",
    "ftp/_backend.py",
    "        if not self.verify:\n            context.check_hostname = False\n",
    "        if True:\n            context.check_hostname = False\n",
    "tests/test_transport_security.py::test_ftps_self_signed_rejected_by_default_before_password",
    "ftps: certificate verification off by default",
)
mutant(
    "checkfile-reply-names-another-algorithm",
    "uri/schemes/sftp/_checkfile.py",
    "    if reply_algorithm != algorithm:\n",
    "    if False:\n",
    "tests/test_sftp.py::test_check_file_reply_naming_another_algorithm_is_refused_at_the_requested_size",
    "check-file reply naming another algorithm is trusted",
)
mutant(
    "uri-str-shows-the-password",
    "uri/__init__.py",
    "        return self.as_uri(sanitize=True)\n",
    "        return self.as_uri(sanitize=False)\n",
    "tests/test_transport_security.py::test_http_error_text_and_traceback_carry_no_password",
    "str(Uri) shows the password",
)
mutant(
    "http-request-url-keeps-the-password",
    "uri/schemes/_http/_follow.py",
    "    return url[:start] + hostport + url[end:], (auth if any(auth) else None)\n",
    "    return url, (auth if any(auth) else None)\n",
    "tests/test_transport_security.py::test_http_sends_userinfo_as_authorization_not_in_url",
    "HTTP requests keep user:password in the request URL",
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
    "http-403-is-a-generic-oserror",
    "uri/schemes/http.py",
    "        elif status in (401, 403):\n",
    "        elif status in (401,):\n",
    "tests/test_transport_security.py::test_http_error_text_and_traceback_carry_no_password",
    "HTTP 403 maps to generic OSError(EIO)",
)
mutant(
    "http-timeout-chains-the-request-error",
    "uri/schemes/http.py",
    '        raise TimeoutError(f"Timeout for {path_obj} ({type(e).__name__})") from None\n',
    '        raise TimeoutError(f"Timeout for {path_obj} ({type(e).__name__})") from e\n',
    "tests/test_transport_security.py::test_http_timeout_carries_no_password",
    "HTTP TimeoutError chains the requests exception",
)
mutant(
    "rm-missing-ok-is-ignored",
    "path/_path.py",
    "        if stat is None:\n            if not missing_ok:\n",
    "        if stat is None:\n            if True:\n",
    "tests/test_parity_io.py::test_rm_missing_ok",
    "rm(missing_ok=True) raises anyway",
)
mutant(
    "is-dir-binding-never-reports-a-binding",
    "path/_path.py",
    "                if test():\n                    return True\n",
    "                if test():\n                    return False\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "is_dir_binding() never reports a junction/mount",
    platform="nt",
)
mutant(
    "copy-onto-itself",
    "path/_path.py",
    "        if _same_file(src, target, identity=False):\n",
    "        if False:\n",
    "tests/test_destructive_safety.py::test_copy_onto_itself_raises_and_keeps_content",
    "copy() of a file onto itself is not refused",
)
mutant(
    "copy-into-itself",
    "path/_path.py",
    "            if contains(src, target):\n",
    "            if False:\n",
    "tests/test_destructive_safety.py::test_mem_copy_directory_into_a_relative_spelling_of_itself_refuses",
    "copy(recursive=True) of a directory into itself is not refused",
)
mutant(
    "copy-opens-the-source-after-removing-the-target",
    "path/_path.py",
    '        with src.open("rb") as input:\n',
    '        with (target.unlink(missing_ok=True) if target_exists else None, src.open("rb"))[1] as input:\n',
    "tests/test_destructive_safety.py::test_copy_missing_source_leaves_existing_target_intact",
    "copy(overwrite=True) removes the target before it knows the source is readable",
)
mutant(
    "copy-applies-a-placeholder-mode",
    "path/_transfer.py",
    '        if getattr(stat, "mode_known", True) and stat.st_mode:\n',
    "        if stat.st_mode:\n",
    "tests/test_io_parity.py::test_copy_from_synthetic_mode_backend_leaves_target_writable",
    "copy() applies a backend's placeholder st_mode to the target",
)
mutant(
    "move-replaces-an-existing-target-by-default",
    "path/_path.py",
    "            if target_stat is not None and not overwrite:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "            if False:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "tests/test_destructive_safety.py::test_move_directory_onto_its_ancestor_without_overwrite_raises_file_exists",
    "move() replaces an existing target without overwrite=True",
)
mutant(
    "move-onto-itself",
    "path/_path.py",
    "        target_stat = None\n        if relation != _SAME:\n",
    "        target_stat = None\n        if True:\n",
    "tests/test_destructive_safety.py::test_move_directory_onto_itself_keeps_the_tree",
    "move() of a path onto itself removes it as an existing target",
)
mutant(
    "rm-descends-into-a-junction-or-mount",
    "path/_path.py",
    "                        if child.is_dir_binding():\n",
    "                        if False:\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "rm(recursive=True) empties what a junction or mount below the tree points at",
    platform="nt",
)
mutant(
    "rm-root-junction-or-mount-is-emptied",
    "path/_path.py",
    '            binding = self.is_dir_binding()\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    '            binding = False\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    "tests/test_destructive_safety.py::test_rm_recursive_on_junction_itself_removes_only_the_link",
    "rm(recursive=True) of a junction or mount empties what is behind it",
    platform="nt",
)
mutant(
    "rm-builds-a-child-from-an-unsafe-listed-name",
    "path/_path.py",
    '                    if not _names.is_safe_child_name(name):\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _entries._child(path, name)',
    '                    if False:\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _entries._child(path, name)',
    "tests/test_listing_names.py::test_rm_recursive_offers_each_unsafe_name_to_ignore_error",
    "rm(recursive=True) builds a child from a listed name such as '..'",
)
mutant(
    "walk-yields-an-unsafe-listed-name",
    "path/_path.py",
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
    "uri-backend-inherited-by-another-endpoint",
    "uri/__init__.py",
    "            if source is mine or _same_authority(source, mine):\n                return backend, self._backend_derived\n",
    "            if True:\n                return backend, self._backend_derived\n",
    "tests/test_uri_path.py::test_a_same_endpoint_uri_destination_keeps_the_configured_backend",
    "a copy() destination on another host reuses this path's backend and its credentials",
)
mutant(
    "uri-join-keeps-a-backend-of-another-endpoint",
    "uri/__init__.py",
    "            if not _same_authority(segment.source, source):\n                continue\n            if backend is not None:",
    "            if False:\n                continue\n            if backend is not None:",
    "tests/test_backend_family.py::test_a_path_that_crosses_to_another_endpoint_builds_its_own",
    "a backend held by a join segment crosses to a path on another endpoint",
)
mutant(
    "objstore-bucket-root-is-removed-recursively",
    "uri/schemes/_objstore.py",
    '        if not self.key:\n            error = PermissionError(f"recursive {self._TOP} delete is not enabled")',
    '        if False:\n            error = PermissionError(f"recursive {self._TOP} delete is not enabled")',
    "tests/test_s3.py::test_rm_recursive_root_guard",
    "rm(recursive=True) of a bucket root empties the bucket",
)
mutant(
    "s3-mtime-is-zero",
    "uri/schemes/s3.py",
    'st_mtime=int(head["LastModified"].timestamp()),',
    "st_mtime=0,",
    "tests/test_s3.py::test_stat_and_listing_report_the_last_modified_time_as_an_epoch",
    "S3Path.stat().st_mtime is always 0",
)
mutant(
    "checkfile-digest-of-the-wrong-length",
    "uri/schemes/sftp/_checkfile.py",
    "    if len(digest) != expected:\n",
    "    if False:\n",
    "tests/test_sftp.py::test_check_file_reply_parser_rejects_malformed_replies",
    "a check-file reply with a digest of the wrong length is trusted",
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
    "objstore-recursive-copy-of-unreachable-keys",
    "uri/schemes/_objstore.py",
    '    unreachable = path._unreachable_keys()\n    if unreachable:\n        raise refusal(path, "copy", unreachable)',
    '    unreachable = path._unreachable_keys()\n    if False:\n        raise refusal(path, "copy", unreachable)',
    "tests/test_s3.py::test_copy_recursive_of_such_a_prefix_is_refused_before_anything_is_written",
    "a recursive copy of a prefix that holds keys no walk reaches goes on and leaves them behind",
)
mutant(
    "objstore-recursive-remove-of-unreachable-keys",
    "uri/schemes/_objstore.py",
    '            if unreachable:\n                error = refusal(self, "remove", unreachable)',
    '            if False:\n                error = refusal(self, "remove", unreachable)',
    "tests/test_s3.py::test_rm_recursive_of_such_a_prefix_removes_nothing",
    "rm(recursive=True) of a prefix that holds keys no walk reaches removes them",
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
mutant(
    "http-write-redirect-reads-as-success",
    "uri/schemes/_http/_follow.py",
    "    if not 300 <= resp.status < 400:\n        return resp\n    location = redirect_location(url, resp)",
    "    if True:\n        return resp\n    location = redirect_location(url, resp)",
    "tests/test_http_writes.py",
    "a write answered by a redirect that is not followed reads as done",
)
mutant(
    "http-write-follows-redirects-to-another-host",
    "uri/schemes/_http/_follow.py",
    '    elif origin(location) != origin(url):\n        reason = "it leaves the scheme, host and port of the request"\n',
    '    elif False:\n        reason = "it leaves the scheme, host and port of the request"\n',
    "tests/test_http_writes.py",
    "a write is sent again to the host a 307 names, with its body and credentials",
)
mutant(
    "http-write-stream-uploads-before-it-is-ready",
    "uri/schemes/http.py",
    "            if self._ready:\n                self._upload(self.getvalue())\n",
    "            if True:\n                self._upload(self.getvalue())\n",
    "tests/test_http_writes.py",
    "a write stream whose constructor raised uploads its half-built buffer when collected",
)
mutant(
    "sftp-known-hosts-sequence-reads-the-first-file-only",
    "uri/schemes/sftp/_paramiko.py",
    "            return [str(path) for path in known_hosts]\n",
    "            return [str(known_hosts[0])]\n",
    "tests/test_sftp_transport.py::test_paramiko_reads_every_known_hosts_file_of_a_sequence",
    "a list of known_hosts files is read only as far as its first",
)
mutant(
    "sftp-failed-client-keeps-its-transport",
    "uri/schemes/sftp/_paramiko.py",
    "    except BaseException as error:\n        transport.close()\n        translated = _connect_error(error, source)",
    "    except BaseException as error:\n        translated = _connect_error(error, source)",
    "tests/test_sftp_transport.py::test_paramiko_closes_the_connection_when_the_sftp_subsystem_is_refused",
    "an SFTP client that fails to start leaves its connection open",
)
mutant(
    "sftp-failed-connect-keeps-its-proxy",
    "uri/schemes/sftp/_paramiko.py",
    "                    sock.close()\n                except Exception:",
    "                    pass\n                except Exception:",
    "tests/test_sftp_transport.py::test_paramiko_closes_the_proxy_it_started_when_the_connection_fails",
    "a connection that fails leaves the ProxyCommand it started running",
)
mutant(
    "sftp-failed-connect-closes-the-callers-socket",
    "uri/schemes/sftp/_paramiko.py",
    '            if sock is not None and "sock" not in self.connect_opts:',
    "            if sock is not None:",
    "tests/test_sftp_transport.py::test_paramiko_leaves_a_socket_the_caller_supplied_open_when_the_connection_fails",
    "a connection that fails closes a socket the caller supplied",
)

# --- sftp: check-file ----------------------------------------------------------

mutant(
    "sftp-check-file-asks-for-another-extension",
    "uri/schemes/sftp/_paramiko.py",
    "            _checkfile.EXTENSION,\n            file.handle,\n",
    '            "check-file@openssh.com",\n            file.handle,\n',
    "tests/test_sync_sftp_parity.py::test_checksum_is_the_server_side_digest",
    "the paramiko backend asks for an extension OpenSSH does not have",
)
mutant(
    "sftp-check-file-asks-for-two-algorithms",
    "uri/schemes/sftp/_paramiko.py",
    "            algorithm,  # hash-algorithm-list: just the one wanted\n",
    '            algorithm + ",md5",  # hash-algorithm-list: just the one wanted\n',
    "tests/test_sync_sftp_parity.py::test_checksum_is_the_server_side_digest",
    "the request names more algorithms than the one wanted",
)
mutant(
    "sftp-check-file-refusal-is-not-remembered",
    "uri/schemes/sftp/_checkfile.py",
    "                if refused(error):\n                    record.refused.add(algorithm)\n",
    "                if False:\n                    record.refused.add(algorithm)\n",
    "tests/test_sync_sftp_parity.py::test_checksum_refusal_costs_one_request_per_connection",
    "a server without the extension is asked again for every file",
)
mutant(
    "github-head-believes-a-reply-that-is-not-the-api-s",
    "uri/schemes/github.py",
    '            if "X-GitHub-Media-Type" not in resp.headers:\n                return None\n            _raise_for_status(resp)',
    "            _raise_for_status(resp)",
    "tests/test_github_stat.py::test_a_reply_that_is_not_the_api_s_own_is_not_believed",
    "a proxy's reply to a HEAD is read as the size of the file, or as its absence",
)
mutant(
    "sftp-check-file-leaks-its-handle",
    "uri/schemes/sftp/_checkfile.py",
    "        finally:\n            file.close()\n        digest = parse_reply(payload, algorithm)",
    "        finally:\n            pass\n        digest = parse_reply(payload, algorithm)",
    (
        "tests/test_sftp.py::test_paramiko_checksum_closes_handle_even_when_request_raises",
        "tests/test_sftp.py::test_paramiko_checksum_sends_correct_extended_request",
    ),
    "a check-file request leaves the handle it opened, whether or not it succeeds",
)

# --- the scheme registry: a name is held by one class -----------------------------

_SCHEME_TESTS = "tests/test_scheme_registry.py"

mutant(
    "scheme-registry-second-claim-is-dropped-silently",
    "uri/__init__.py",
    "            elif name in _REPLACED and _is_builtin_class(name, cls):\n",
    "            elif True:\n",
    (
        f"{_SCHEME_TESTS}::test_a_second_class_listing_a_scheme_raises_at_definition",
        f"{_SCHEME_TESTS}::test_a_class_refused_for_one_name_registers_none_of_its_names",
    ),
    "a second class listing a held scheme is neither refused nor registered",
)
mutant(
    "scheme-registry-built-in-name-is-free-until-its-module-is-imported",
    "uri/__init__.py",
    "                if name in _BUILTIN_SCHEMES and not _is_builtin_class(name, cls):\n",
    "                if False:\n",
    f"{_SCHEME_TESTS}::test_a_built_in_scheme_is_refused_before_its_module_is_imported",
    "a class takes a built-in scheme whose module has not been imported yet",
)
mutant(
    "scheme-registry-built-in-class-refused-after-a-replacement",
    "uri/__init__.py",
    "            elif name in _REPLACED and _is_builtin_class(name, cls):\n",
    "            elif False:\n",
    f"{_SCHEME_TESTS}::test_a_replaced_built_in_scheme_stays_replaced_when_its_module_is_imported",
    "importing a built-in module fails once a class has replaced one of its schemes",
)
mutant(
    "scheme-lookup-asks-entry-points-before-the-built-in-table",
    "uri/__init__.py",
    "            cls._load_builtin_scheme(scheme, strict=strict)\n"
    "            or cls._load_entry_point(scheme)\n",
    "            cls._load_entry_point(scheme)\n"
    "            or cls._load_builtin_scheme(scheme, strict=strict)\n",
    f"{_SCHEME_TESTS}::test_an_entry_point_named_like_a_built_in_scheme_does_not_displace_it",
    "a distribution's entry point for a built-in name is loaded ahead of the built-in",
)
mutant(
    "scheme-listing-leaves-out-schemes-not-imported-yet",
    "uri/__init__.py",
    "        return tuple(sorted({*claimed, *_BUILTIN_SCHEMES, *_declared_entry_points()}))\n",
    "        return tuple(sorted({*claimed}))\n",
    (
        f"{_SCHEME_TESTS}::test_schemes_lists_every_built_in_name_without_importing_its_module",
        f"{_SCHEME_TESTS}::test_schemes_lists_an_entry_point_whose_module_is_not_imported",
    ),
    "schemes() lists only the classes already imported",
)
mutant(
    "scheme-registry-same-qualified-name-in-another-module-is-the-same-class",
    "uri/__init__.py",
    "    return a.__module__ == b.__module__ and a.__qualname__ == b.__qualname__\n",
    "    return a.__qualname__ == b.__qualname__\n",
    f"{_SCHEME_TESTS}::test_a_class_of_the_same_qualified_name_in_another_module_is_still_refused",
    "a class of another module takes a scheme from a class that has the same name",
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
    (
        f"{_ARCHIVE_PATH}::test_backend_for_shares_a_handle_unless_asked_for_its_own",
        "tests/test_uri_schemesmap.py",
    ),
    "backend_for(shared=False) returns the shared handle",
)

# --- a write stream that commits on close, and a block that raised ---------------

_DISCARD = "tests/test_write_discard.py"
mutant(
    "write-block-that-raised-does-not-discard",
    "_discard.py",
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
    "binary-stream-with-discard-is-not-guarded",
    "_discard.py",
    "    guarded = _guarded_class(type(stream))\n",
    "    guarded = None\n",
    (
        f"{_DISCARD}::test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed",
        f"{_DISCARD}::test_write_bytes_and_write_text_whose_write_fails_commit_nothing",
        f"{_DISCARD}::test_a_guarded_stream_pickles_as_the_class_it_was",
    ),
    "a binary stream that has discard() but no __exit__ of its own is committed by a failed block",
)
mutant(
    "open-gives-a-discarding-stream-a-stock-text-wrapper",
    "protocols/io.py",
    "            if callable(discard):\n",
    "            if False:\n",
    (
        f"{_DISCARD}::test_a_block_that_raised_tells_the_stream_to_discard_before_it_is_closed",
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
mutant(
    "http-upload-stream-uploads-after-a-discard",
    "uri/schemes/http.py",
    "        self._ready = False\n        if not self.closed:\n",
    "        self._ready = True\n        if not self.closed:\n",
    f"tests/test_http_writes.py::test_a_write_block_that_raised_sends_no_upload",
    "an http: or dav: write whose with block raised is sent on close",
)
mutant(
    "objstore-upload-stream-uploads-after-a-discard",
    "uri/schemes/_objstore.py",
    "        self._dropped = True\n        self._file.close()\n",
    "        pass\n",
    (
        "tests/test_s3.py::test_a_write_block_that_raised_sends_no_upload_moto",
        "tests/test_gs_fake.py::test_a_write_block_that_raised_uploads_nothing",
        "tests/test_az_fake.py::test_a_write_block_that_raised_uploads_nothing",
    ),
    "an s3:, gs: or az: write whose with block raised is uploaded on close",
)

# --- http transports: the guards every transport keeps --------------------------

_HTTP_PKG = "uri/schemes/_http"

mutant(
    "http-authorization-kept-across-origins",
    f"{_HTTP_PKG}/_follow.py",
    '        if origin(current) != home:\n            headers = drop_header(headers, "Authorization")\n',
    '        if False:\n            headers = drop_header(headers, "Authorization")\n',
    (
        "tests/test_http_redirects.py::test_authorization_is_dropped_by_a_redirect_to_another_port",
        "tests/test_http_redirects.py::test_authorization_follows_only_a_redirect_that_keeps_scheme_host_and_port",
    ),
    "a followed redirect to another scheme, host or port carries Authorization along",
)
mutant(
    "http-redirect-limit-off-by-one",
    f"{_HTTP_PKG}/_follow.py",
    "        if hops >= MAX_REDIRECTS:",
    "        if hops > MAX_REDIRECTS:",
    "tests/test_http_redirects.py::test_a_redirect_loop_stops_after_thirty_redirects",
    "a GET follows one redirect more than the limit",
)
mutant(
    "http-unrepeatable-request-resent",
    f"{_HTTP_PKG}/_builtin.py",
    "                if reused and repeatable:\n                    connection, reused = None, False\n                    continue\n                raise dropped.error from None",
    "                if reused:\n                    connection, reused = None, False\n                    continue\n                raise dropped.error from None",
    (
        "tests/test_http_pool.py::test_a_request_that_cannot_be_repeated_is_not_sent_again",
        "tests/test_http_pool.py::test_a_body_that_cannot_be_sent_twice_is_not_sent_again",
    ),
    "a POST, or a PUT with a file body, is sent again on a new connection when the server dropped the old one",
)
mutant(
    "http-timeout-treated-as-a-dropped-connection",
    f"{_HTTP_PKG}/_builtin.py",
    "            BrokenPipeError,\n            _ssl.SSLEOFError,",
    "            OSError,\n            _ssl.SSLEOFError,",
    "tests/test_http_pool.py::test_a_timeout_on_a_reused_connection_is_not_sent_again",
    "a request that timed out on a reused connection is sent again",
)
mutant(
    "http-stale-connection-trusted-by-an-unrepeatable-request",
    f"{_HTTP_PKG}/_builtin.py",
    "            and _now() - connection.released_at > REUSE_WINDOW",
    "            and _now() - connection.released_at > REUSE_WINDOW + 1e9",
    "tests/test_http_pool.py::test_a_request_that_cannot_be_repeated_does_not_trust_a_stale_connection",
    "a POST uses an idle connection however long ago it last carried a reply",
)
mutant(
    "http-verification-off-by-default",
    f"{_HTTP_PKG}/_builtin.py",
    '        verify: bool = True,\n        proxies: "_ty.Any" = None,',
    '        verify: bool = False,\n        proxies: "_ty.Any" = None,',
    "tests/test_http_tls.py::test_a_server_of_an_unknown_authority_is_refused_by_default",
    "the built-in transport accepts any certificate unless told to verify",
)
mutant(
    "http-verify-false-beats-the-ssl-context",
    f"{_HTTP_PKG}/_builtin.py",
    "        if self._ssl_context is not None:\n            return self._ssl_context",
    "        if self._ssl_context is not None and self._verify:\n            return self._ssl_context",
    "tests/test_http_tls.py::test_an_ssl_context_wins_over_verify_false",
    "verify=False replaces an ssl_context the caller gave",
)
mutant(
    "http-proxy-credentials-in-a-repr",
    f"{_HTTP_PKG}/_proxies.py",
    '        return f"ProxyAddress({self.host!r}, {self.port})"',
    '        return f"ProxyAddress({self.host!r}, {self.port}, {self.authorization!r})"',
    "tests/test_http_proxy.py::test_proxy_credentials_are_in_no_repr",
    "a proxy's Proxy-Authorization value shows in its repr",
)
mutant(
    "http-proxy-credentials-in-a-message",
    f"{_HTTP_PKG}/_proxies.py",
    '            f"the built-in HTTP transport has no {scheme!r} proxy support; "\n            + REQUESTS_HINT,',
    '            f"the built-in HTTP transport has no {scheme!r} proxy support ({url}); "\n            + REQUESTS_HINT,',
    "tests/test_http_proxy.py::test_a_proxy_scheme_that_is_not_http_is_refused_naming_the_other_transport",
    "the error for an unsupported proxy scheme quotes the proxy URL with its credentials",
)
mutant(
    "http-header-line-break-accepted",
    f"{_HTTP_PKG}/_headers.py",
    r'_CONTROL = _re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")',
    r'_CONTROL = _re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")',
    "tests/test_http_transport.py::test_a_header_with_a_line_break_or_a_bad_name_is_refused_before_sending",
    "a header value with a bare line feed is sent",
)
mutant(
    "http-url-line-break-accepted",
    f"{_HTTP_PKG}/_headers.py",
    r'_URL_CONTROL = _re.compile(r"[\x00-\x1f\x7f]")',
    r'_URL_CONTROL = _re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")',
    "tests/test_http_transport.py::test_a_url_with_a_control_character_is_refused_before_sending",
    "a URL with a line break or a tab reaches the request line",
)
mutant(
    "http-short-body-read-as-complete",
    f"{_HTTP_PKG}/_builtin.py",
    "        if self._expected is not None and self._received < self._expected:",
    "        if False:",
    "tests/test_http_transport.py::test_a_body_shorter_than_its_length_is_an_io_error_not_a_short_read",
    "a body that ends before its Content-Length reads as a complete one",
)
mutant(
    "http-inflation-not-bounded",
    f"{_HTTP_PKG}/_response.py",
    "_STEP = 256 * 1024",
    "_STEP = 1 << 40",
    "tests/test_http_transport.py::test_a_small_body_that_inflates_enormously_is_read_in_bounded_steps",
    "a gzip body is inflated whole into memory because a header says it is small",
)
mutant(
    "http-closed-stream-still-read",
    f"{_HTTP_PKG}/_response.py",
    "        if self._closed and not self._kept:",
    "        if False:",
    "tests/test_http_transport.py::test_reading_a_streamed_reply_after_closing_it_is_an_error",
    "a closed streaming reply keeps handing out bytes",
)
mutant(
    "http-idle-connection-limit",
    f"{_HTTP_PKG}/_pool.py",
    "MAX_IDLE_PER_THREAD = 8",
    "MAX_IDLE_PER_THREAD = 100",
    "tests/test_http_pool.py::test_a_thread_keeps_at_most_eight_idle_connections",
    "a thread keeps more than eight idle connections",
)
mutant(
    "http-default-transport-is-requests",
    f"{_HTTP_PKG}/_select.py",
    'DEFAULT_TRANSPORT = "builtin"',
    'DEFAULT_TRANSPORT = "requests"',
    "tests/test_http_default.py::test_without_a_setting_the_built_in_transport_is_used",
    "a backend without a transport builds a requests session",
)
mutant(
    "http-idle-connection-used-without-a-check",
    f"{_HTTP_PKG}/_builtin.py",
    "        if connection is not None and _unusable(connection.http.sock):\n",
    "        if False:\n",
    (
        "tests/test_http_pool.py::test_bytes_the_server_sends_after_a_reply_are_not_the_next_reply",
        "tests/test_http_pool.py::test_an_idle_connection_the_server_closed_is_replaced_before_sending",
    ),
    "an idle connection the server closed, or sent extra bytes on, carries the next request",
)
mutant(
    "http-closed-connection-reopens-itself",
    f"{_HTTP_PKG}/_builtin.py",
    "        http.auto_open = 0\n",
    "        http.auto_open = 1\n",
    (
        "tests/test_http_pool.py::test_a_connection_closed_before_the_request_is_written_is_not_reopened",
        "tests/test_http_pool.py::test_a_transport_closed_before_the_request_is_written_does_not_send_it",
        "tests/test_http_pool.py::test_a_body_that_is_being_sent_when_the_transport_is_closed_is_not_finished_in_the_clear",
    ),
    "http.client reconnects a closed connection by itself, in plain TCP outside the TLS session and the proxy",
)
mutant(
    "http-interim-reply-taken-as-the-final-one",
    f"{_HTTP_PKG}/_builtin.py",
    "            if status == _client.CONTINUE:\n                return version, status, reason  # `begin()` skips its headers\n",
    "            if True:\n                return version, status, reason  # `begin()` skips its headers\n",
    (
        "tests/test_http_transport.py::test_the_built_in_transport_skips_interim_replies_and_returns_the_final_one",
        "tests/test_http_transport.py::test_an_interim_reply_is_never_the_previous_answer_of_the_next_request",
    ),
    "a 103 Early Hints is the reply, and the real one is read as the answer to the next request",
)
mutant(
    "http-interim-replies-not-counted",
    f"{_HTTP_PKG}/_builtin.py",
    "            if self._interim > MAX_INTERIM_REPLIES:\n",
    "            if False:\n",
    (
        "tests/test_http_transport.py::test_a_bounded_number_of_interim_replies_is_skipped",
        "tests/test_http_transport.py::test_an_endless_stream_of_100_continue_is_given_up_not_waited_out",
    ),
    "a server that answers with endless interim replies is waited on without limit",
)
mutant(
    "http-no-proxy-cidr-ignored",
    f"{_HTTP_PKG}/_proxies.py",
    '        if _in_no_proxy_network(host, chosen.get("no", "")):\n',
    "        if False:\n",
    "tests/test_http_proxy.py::test_a_cidr_block_in_no_proxy_sends_an_ip_literal_host_direct",
    "a request to a host inside a NO_PROXY block goes to the proxy, which sees its Authorization",
)
_PEER = "tests/test_http_peer_text.py"

mutant(
    "http-peer-text-not-escaped",
    f"{_HTTP_PKG}/_headers.py",
    '        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")\n',
    "        char\n",
    f"{_PEER}::test_peer_text_escapes_what_a_terminal_would_act_on",
    "text from the peer keeps its control characters in an error message",
)
mutant(
    "http-reason-phrase-unescaped",
    "uri/schemes/http.py",
    '        reason = peer_text(response.reason or "")\n',
    '        reason = response.reason or ""\n',
    (
        f"{_PEER}::test_a_reason_phrase_is_shown_escaped",
        f"{_PEER}::test_a_long_reason_phrase_is_cut",
    ),
    "an HTTP error's reason phrase reaches the message as the server sent it",
)
mutant(
    "git-reason-phrase-unescaped",
    "uri/schemes/_gitrepo.py",
    '        reason = peer_text(response.reason or "")\n',
    '        reason = response.reason or ""\n',
    f"{_PEER}::test_the_reason_phrase_of_a_git_api_reply_is_shown_escaped",
    "a git API error's reason phrase reaches the message as the server sent it",
)
mutant(
    "http-refused-redirect-location-unbounded",
    f"{_HTTP_PKG}/_follow.py",
    '    where = peer_text(shown(location), 200) if location else "(none)"\n',
    '    where = shown(location) if location else "(none)"\n',
    f"{_PEER}::test_a_redirect_that_is_refused_is_shown_cut_and_with_an_escaped_reason",
    "a refused redirect's message holds the whole Location",
)
mutant(
    "http-redirect-reask-forgets-the-origin",
    "uri/schemes/http.py",
    "            method, location, sent, None, None, stream, timeout, first=url\n",
    "            method, location, sent, None, None, stream, timeout, first=None\n",
    (
        "tests/test_http_redirect_origin.py::test_a_redirect_to_another_origin_does_not_carry_authorization",
        "tests/test_http_redirect_origin.py::test_a_patch_mode_append_asks_the_other_origin_without_authorization",
    ),
    "stat() and what is built on it re-ask the redirect target as if it were the first origin, with Authorization",
)
mutant(
    "http-redirect-reask-sends-the-backend-headers-again",
    "uri/schemes/_http/_follow.py",
    "        if origin(current) != home:\n",
    "        if origin(current) != origin(url):\n",
    "tests/test_http_redirect_origin.py::test_a_redirect_to_another_origin_does_not_carry_authorization",
    "a re-request compares the redirect target with itself instead of with the origin it was asked for",
)
mutant(
    "http-unusable-location-reaches-the-transport",
    f"{_HTTP_PKG}/_follow.py",
    "        check_url(absolute)\n        return absolute\n",
    "        return absolute\n",
    "tests/test_http_redirects.py::test_a_location_the_url_check_refuses_is_oserror_never_valueerror",
    "a Location the URL check refuses is sent on, and its ValueError reaches the caller",
)
mutant(
    "http-redirect-without-a-usable-location-is-the-reply",
    f"{_HTTP_PKG}/_follow.py",
    '        if location is None:\n            _discard(resp)\n            raise refused_redirect(method, current, resp, None, "no usable Location")\n        if hops >= MAX_REDIRECTS:',
    "        if location is None:\n            return resp\n        if hops >= MAX_REDIRECTS:",
    "tests/test_http_redirects.py::test_a_location_that_is_not_an_http_url_is_a_refused_redirect",
    "a redirect with an unusable Location reads as the answer itself",
)
_LIMITS = "tests/test_http_reply_limits.py"

mutant(
    "http-reply-limit-not-applied",
    f"{_HTTP_PKG}/_response.py",
    "            if limit is not None and len(data) > limit:\n",
    "            if False:\n",
    (
        f"{_LIMITS}::test_a_propfind_reply_over_the_limit_is_efbig_counted_in_decoded_bytes",
        f"{_LIMITS}::test_a_git_api_reply_over_the_limit_is_efbig_and_a_file_is_not_limited",
        f"{_LIMITS}::test_the_backend_reads_a_reply_whole_only_up_to_the_limit",
    ),
    "a reply read whole is held in memory however long it decodes to",
)
mutant(
    "http-dropped-reply-read-without-a-bound",
    f"{_HTTP_PKG}/_status.py",
    "            remaining -= len(chunk)\n",
    "            pass\n",
    f"{_LIMITS}::test_the_reply_to_a_write_is_dropped_and_its_connection_is_not_kept",
    "the body of a reply nobody looks at is read to its end, however long",
)
mutant(
    "dav-propfind-reply-read-unbounded",
    "uri/schemes/dav.py",
    "        body = _read_limited(resp, http_module.MAX_LISTING_BYTES, self)\n",
    "        body = resp.read()\n",
    f"{_LIMITS}::test_a_propfind_reply_over_the_limit_is_efbig_counted_in_decoded_bytes",
    "a PROPFIND reply is read whole whatever its size",
)
mutant(
    "git-json-reply-read-unbounded",
    f"{_HTTP_PKG}/_status.py",
    "    return _json.loads(read_limited(resp, limit, what))",
    "    return _json.loads(resp.read())",
    f"{_LIMITS}::test_a_git_api_reply_over_the_limit_is_efbig_and_a_file_is_not_limited",
    "a git API's JSON reply is read whole whatever its size",
)
mutant(
    "http-backend-buffers-without-a-cap",
    "uri/schemes/http.py",
    "            resp._buffered(limit, _too_large(_follow.shown(url), limit))\n",
    "            resp._buffered()\n",
    f"{_LIMITS}::test_the_backend_reads_a_reply_whole_only_up_to_the_limit",
    "request(stream=False) reads a reply to its end whatever its size",
)
mutant(
    "http-exchange-runs-under-the-connect-timeout",
    f"{_HTTP_PKG}/_builtin.py",
    "                sock.settimeout(read_timeout)\n",
    "                sock.settimeout(connect_timeout)\n",
    "tests/test_http_transport.py::test_a_server_that_never_answers_is_a_timeout_after_the_read_timeout",
    "the wait for a reply is bounded by the connect half of the timeout",
)
_BODY = "tests/test_http_body.py"
mutant(
    "http-body-length-ignores-the-position",
    f"{_HTTP_PKG}/_body.py",
    "        return max(0, end - here)\n",
    "        return max(0, end)\n",
    (
        f"{_BODY}::test_a_bytes_io_part_way_sends_what_is_left_under_that_length",
        f"{_BODY}::test_an_open_file_part_way_sends_what_is_left_under_that_length",
    ),
    "a file read from the middle is sent under the length of the whole file",
)
mutant(
    "http-body-sent-past-its-length",
    f"{_HTTP_PKG}/_body.py",
    "        want = self._left if amount is None or amount < 0 else min(amount, self._left)\n",
    "        want = self._left if amount is None or amount < 0 else amount\n",
    (
        f"{_BODY}::test_a_file_that_grew_after_it_was_measured_sends_only_the_length_it_stated",
        f"{_BODY}::test_a_length_the_caller_set_cuts_a_longer_body_at_that_length",
    ),
    "a body longer than its Content-Length sends the rest into the next request",
)
mutant(
    "http-body-short-of-its-length-is-not-an-error",
    f"{_HTTP_PKG}/_body.py",
    "        if not chunk:\n            raise OSError(\n",
    "        if False:\n            raise OSError(\n",
    (
        f"{_BODY}::test_a_file_that_ends_before_the_length_it_stated_is_an_error",
        f"{_BODY}::test_a_generator_that_ends_before_the_length_the_caller_set_is_an_error",
    ),
    "a body that ends before its Content-Length leaves the server waiting for the rest",
)
mutant(
    "http-length-larger-than-the-body-is-sent",
    f"{_HTTP_PKG}/_body.py",
    "        if known is not None and declared > known:\n",
    "        if False:\n",
    f"{_BODY}::test_a_length_larger_than_a_body_whose_size_is_known_is_refused",
    "a Content-Length larger than a file or list is sent and the upload stalls",
)
