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
    "uri/schemes/archive/_base.py",
    '        if not new_path:\n            raise ValueError(f"member name escapes the archive root: {target.path!r}")\n',
    '        if False:\n            raise ValueError(f"member name escapes the archive root: {target.path!r}")\n',
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
    "path.py",
    "        if _same_file(self, target):\n",
    "        if False:\n",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_onto_itself_is_refused_and_keeps_the_link",
    "copy(follow_symlinks=False) of a link onto itself unlinks and recreates it",
)
mutant(
    "copy-link-replaces-existing-link",
    "path.py",
    "            target.unlink(missing_ok=True)\n        target.symlink_to(link,",
    "            pass\n        target.symlink_to(link,",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_replaces_a_link_at_the_target_when_asked_to",
    "copy(overwrite=True) of a link leaves the old link in the way",
)
mutant(
    "copy-link-onto-directory",
    "path.py",
    "            if target_stat.is_dir() and not target_stat.is_symlink():\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "            if False:\n                raise IsADirectoryError(target)\n            if not overwrite:",
    "tests/test_copy_move_fidelity.py::test_copying_a_link_never_removes_a_directory_at_the_target",
    "copy(overwrite=True) of a link tries to unlink a directory at the target",
)
mutant(
    "copy-link-without-readlink",
    "path.py",
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
    "utils/__init__.py",
    '    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:\n',
    '    return True\n    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name:\n',
    "tests/test_destructive_safety.py::test_unsafe_child_names_rejected_on_every_flavour",
    "is_safe_child_name always True",
)
mutant(
    "rm-follows-a-directory-symlink",
    "path.py",
    '                        if policy == "follow" and child.is_dir():\n',
    "                        if child.is_dir():\n",
    "tests/test_destructive_safety.py::test_file_uri_rm_recursive_does_not_follow_directory_symlink",
    "rm(recursive=True) descends into directory symlinks by default",
)
mutant(
    "sync-overlap-check-dropped",
    "utils/sync.py",
    "            elif _paths_overlap(source.path, target.path):\n",
    "            elif False:\n",
    "tests/test_sync_safety.py::test_source_inside_target_raises_and_source_survives",
    "PathSyncer: overlap check dropped",
)
mutant(
    "sync-missing-source-root-is-no-error",
    "utils/sync.py",
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
    "            if not _utils.is_safe_child_name(name):\n",
    "            if False:\n",
    "tests/test_sftp.py::test_a_hostile_listing_name_never_becomes_a_child",
    "SftpPath listing filter no-op",
)
mutant(
    "http-listing-keeps-unsafe-names",
    "uri/schemes/http.py",
    "            if not _utils.is_safe_child_name(name):\n",
    "            if False:\n",
    "tests/test_http.py::test_http_listing_leaves_out_a_name_that_is_not_a_safe_component",
    "HttpPath listing filter no-op",
)
mutant(
    "dav-listing-keeps-unsafe-names",
    "uri/schemes/dav.py",
    "            if _utils.is_safe_child_name(name):\n",
    "            if True:\n",
    "tests/test_webdav.py::test_propfind_dot_entries_are_not_children",
    "DavPath listing filter no-op",
)
mutant(
    "copy-replaces-an-existing-target-by-default",
    "path.py",
    "            if not overwrite:\n                raise FileExistsError(target)\n\n        # Open the source",
    "            if False:\n                raise FileExistsError(target)\n\n        # Open the source",
    "tests/test_parity_io.py::test_copy_overwrite",
    "copy(): existing target replaced without overwrite=True",
)
mutant(
    "path-same-filesystem-always-true",
    "path.py",
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
    "uri/schemes/archive/_base.py",
    "                return None  # escapes the archive root\n",
    "                continue  # escapes the archive root\n",
    "tests/test_archive_safety.py::test_archive_listing_skips_escaping_member_names",
    "archive normalizer: an escaping '..' is dropped instead of rejecting the name",
)
mutant(
    "archive-member-safety-check-off",
    "uri/schemes/archive/_base.py",
    "    if not _is_safe_member_name(normalized):\n",
    "    if False:\n",
    "tests/test_archive_safety.py::test_a_member_name_with_a_nul_is_refused_and_the_archive_is_unchanged",
    "archive normalizer: final safety check off",
)
mutant(
    "glob-wildcard-matches-dotfiles-when-hidden-excluded",
    "utils/glob.py",
    '    skip_hidden = not opts.include_hidden and not part.startswith(".")\n',
    "    skip_hidden = False\n",
    "tests/test_parity_io.py::test_glob_hidden_included_by_default_like_pathlib",
    "glob: wildcard step no longer hides dotfiles",
)
mutant(
    "glob-recursive-wildcard-enters-hidden",
    "utils/glob.py",
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
    "utils/__init__.py",
    "                on_evict(key, value)\n",
    "                pass\n",
    "tests/test_transport_security.py::test_ftps_verify_false_opt_out_accepts",
    "LRU never calls on_evict",
)
mutant(
    "copy-leaves-a-half-written-target",
    "path.py",
    "                if created:\n",
    "                if False:\n",
    "tests/test_destructive_safety.py::test_copy_failing_mid_stream_removes_partial_target",
    "copy(): half-written target left behind on failure",
)
mutant(
    "copy-recursive-accepts-unsafe-child-names",
    "path.py",
    "                    if not _utils.is_safe_child_name(\n",
    "                    if False and not _utils.is_safe_child_name(\n",
    "tests/test_destructive_safety.py::test_copy_recursive_never_merges_names_windows_rewrites",
    "copy(recursive): unsafe child-name refusal off",
)
mutant(
    "sync-accepts-unsafe-child-names",
    "utils/sync.py",
    "                if _utils.is_safe_child_name(name, windows=windows_target):\n",
    "                if True:\n",
    "tests/test_destructive_safety.py::test_sync_never_merges_names_windows_rewrites",
    "PathSyncer: unsafe child-name refusal off",
)
mutant(
    "sync-replaces-a-non-empty-target-directory",
    "utils/sync.py",
    "        if self.remove_missing or target.is_symlink() or not target.is_dir():\n",
    "        if True:\n",
    "tests/test_low_sync.py::test_dir_to_file_without_remove_missing_raises_and_keeps_tree",
    "PathSyncer: non-empty target directory replaced with remove_missing=False",
)
mutant(
    "symlink-to-force-swallows-unlink-errors-of-files",
    "path.py",
    "                if not self.is_dir():\n                    raise error\n",
    "                if False:\n                    raise error\n",
    "tests/test_path_gaps.py::test_symlink_to_force_reports_a_failed_unlink_of_a_file_and_makes_no_link",
    "symlink_to(force=True): unlink errors swallowed for non-directories too",
)
mutant(
    "move-directory-over-file-removes-the-directory",
    "path.py",
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
    "path.py",
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
    "uri/schemes/ftp.py",
    "            if tls:\n                client.prot_p()\n",
    "            if False:\n                client.prot_p()\n",
    "tests/test_transport_security.py::test_ftps_verify_false_opt_out_accepts",
    "ftps: data connections are no longer encrypted",
)
mutant(
    "ftps-certificate-verification-off",
    "uri/schemes/ftp.py",
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
    "uri/schemes/http.py",
    "    return url[:start] + hostport + url[end:], (auth if any(auth) else None)\n",
    "    return url, (auth if any(auth) else None)\n",
    "tests/test_transport_security.py::test_http_sends_userinfo_as_auth_not_in_url",
    "HTTP requests keep user:password in the request URL",
)
mutant(
    "sync-removes-extras-without-remove-missing",
    "utils/sync.py",
    "            if self.remove_missing and not target_absent:\n",
    "            if not target_absent:\n",
    "tests/test_sync_safety.py::test_root_target_symlink_is_followed",
    "PathSyncer deletes target-only entries with remove_missing=False",
)
mutant(
    "sync-leaves-its-temp-file-after-a-failed-copy",
    "utils/sync.py",
    "                                temp.unlink(missing_ok=True)\n",
    "                                pass\n",
    "tests/test_sync_safety.py::test_unlink_refused_during_the_fallback_keeps_the_old_version",
    "PathSyncer leaves its temporary file behind after a failed copy",
)
mutant(
    "windows-child-name-with-a-drive-accepted",
    "utils/__init__.py",
    '        if "\\\\" in name or ":" in name:\n',
    '        if "\\\\" in name:\n',
    "tests/test_destructive_safety.py::test_windows_only_unsafe_child_names",
    "is_safe_child_name(windows=True) accepts 'D:x'",
)
mutant(
    "child-name-with-nul-accepted",
    "utils/__init__.py",
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
    "path.py",
    "        if stat is None:\n            if not missing_ok:\n",
    "        if stat is None:\n            if True:\n",
    "tests/test_parity_io.py::test_rm_missing_ok",
    "rm(missing_ok=True) raises anyway",
)
mutant(
    "is-dir-binding-never-reports-a-binding",
    "path.py",
    "                if test():\n                    return True\n",
    "                if test():\n                    return False\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "is_dir_binding() never reports a junction/mount",
    platform="nt",
)
mutant(
    "copy-onto-itself",
    "path.py",
    "        if _same_file(src, target, identity=False):\n",
    "        if False:\n",
    "tests/test_destructive_safety.py::test_copy_onto_itself_raises_and_keeps_content",
    "copy() of a file onto itself is not refused",
)
mutant(
    "copy-into-itself",
    "path.py",
    "            if contains(src, target):\n",
    "            if False:\n",
    "tests/test_destructive_safety.py::test_mem_copy_directory_into_a_relative_spelling_of_itself_refuses",
    "copy(recursive=True) of a directory into itself is not refused",
)
mutant(
    "copy-opens-the-source-after-removing-the-target",
    "path.py",
    '        with src.open("rb") as input:\n',
    '        with (target.unlink(missing_ok=True) if target_exists else None, src.open("rb"))[1] as input:\n',
    "tests/test_destructive_safety.py::test_copy_missing_source_leaves_existing_target_intact",
    "copy(overwrite=True) removes the target before it knows the source is readable",
)
mutant(
    "copy-applies-a-placeholder-mode",
    "path.py",
    '        if getattr(stat, "mode_known", True) and stat.st_mode:\n',
    "        if stat.st_mode:\n",
    "tests/test_io_parity.py::test_copy_from_synthetic_mode_backend_leaves_target_writable",
    "copy() applies a backend's placeholder st_mode to the target",
)
mutant(
    "move-replaces-an-existing-target-by-default",
    "path.py",
    "            if target_stat is not None and not overwrite:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "            if False:\n                raise FileExistsError(target)\n            if relation == _INNER:",
    "tests/test_destructive_safety.py::test_move_directory_onto_its_ancestor_without_overwrite_raises_file_exists",
    "move() replaces an existing target without overwrite=True",
)
mutant(
    "move-onto-itself",
    "path.py",
    "        target_stat = None\n        if relation != _SAME:\n",
    "        target_stat = None\n        if True:\n",
    "tests/test_destructive_safety.py::test_move_directory_onto_itself_keeps_the_tree",
    "move() of a path onto itself removes it as an existing target",
)
mutant(
    "rm-descends-into-a-junction-or-mount",
    "path.py",
    "                        if child.is_dir_binding():\n",
    "                        if False:\n",
    "tests/test_destructive_safety.py::test_rm_recursive_does_not_descend_into_junction",
    "rm(recursive=True) empties what a junction or mount below the tree points at",
    platform="nt",
)
mutant(
    "rm-root-junction-or-mount-is-emptied",
    "path.py",
    '            binding = self.is_dir_binding()\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    '            binding = False\n            policy = _follow_policy(follow_binds, self) if binding else "follow"\n',
    "tests/test_destructive_safety.py::test_rm_recursive_on_junction_itself_removes_only_the_link",
    "rm(recursive=True) of a junction or mount empties what is behind it",
    platform="nt",
)
mutant(
    "rm-builds-a-child-from-an-unsafe-listed-name",
    "path.py",
    '                    if not _utils.is_safe_child_name(name):\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _glob._child(path, name)',
    '                    if False:\n                        raise ValueError(\n                            f"refusing unsafe child name {name!r} under {path}"\n                        )\n                    child = _glob._child(path, name)',
    "tests/test_listing_names.py::test_rm_recursive_offers_each_unsafe_name_to_ignore_error",
    "rm(recursive=True) builds a child from a listed name such as '..'",
)
mutant(
    "walk-yields-an-unsafe-listed-name",
    "path.py",
    "                if not _utils.is_safe_child_name(name):\n                    # A listing is untrusted input",
    "                if False:\n                    # A listing is untrusted input",
    "tests/test_listing_names.py::test_walk_omits_unsafe_names_and_reports_them_to_on_error",
    "walk() descends into a listed name such as '..'",
)
mutant(
    "unpack-writes-outside-the-destination",
    "utils/archive.py",
    "    if not parts or not all(_is_safe_name(p, windows, rewrites) for p in parts):\n",
    "    if not parts:\n",
    "tests/test_archive_safety.py::test_unpack_archive_never_writes_outside_dest",
    "unpack_archive() writes a member whose name has a '..' part",
)
mutant(
    "windows-child-name-with-trailing-dot-accepted",
    "utils/__init__.py",
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
    "utils/sync.py",
    "    return _relation(source, target, follow=True, identity=False) is not None\n",
    "    return _relation(source, target, follow=False, identity=False) is not None\n",
    "tests/test_sync_safety.py::test_overlap_hidden_behind_symlink_is_detected",
    "PathSyncer does not see an overlap that a symlink hides",
)
mutant(
    "glob-loop-guard-is-off",
    "utils/glob.py",
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
    "uri/schemes/archive/_base.py",
    "        if beyond or around:\n",
    "        if around:\n",
    "tests/test_archive_link_listing.py::test_nested_links_are_bounded_for_every_operation",
    "a following listing enters a link met below another link",
)
mutant(
    "archive-link-to-its-own-ancestor-is-entered",
    "uri/schemes/archive/_base.py",
    "        if beyond or around:\n",
    "        if beyond:\n",
    "tests/test_archive_link_listing.py::test_a_following_walk_lists_a_link_to_its_own_directory_and_does_not_enter_it",
    "a following listing enters a link to the directory that holds it or above",
)
mutant(
    "archive-link-chain-is-unbounded",
    "uri/schemes/archive/_base.py",
    "                if followed > MAX_LINKS:\n",
    "                if followed > MAX_LINKS + 1:\n",
    ("tests/test_archive_links.py", "tests/test_archive_link_listing.py"),
    "a lookup follows one link more than the bound",
)
mutant(
    "http-write-redirect-reads-as-success",
    "uri/schemes/http.py",
    "    if not 300 <= resp.status_code < 400:\n        return resp\n    location = _redirect_location(url, resp)",
    "    if True:\n        return resp\n    location = _redirect_location(url, resp)",
    "tests/test_http_writes.py",
    "a write answered by a redirect that is not followed reads as done",
)
mutant(
    "http-write-follows-redirects-to-another-host",
    "uri/schemes/http.py",
    '    elif _origin(_urlparse.urlsplit(location)) != _origin(_urlparse.urlsplit(url)):\n        reason = "it leaves the scheme, host and port of the request"\n',
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
    '            if "X-GitHub-Media-Type" not in resp.headers:\n                return None\n            resp.raise_for_status()',
    "            resp.raise_for_status()",
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
