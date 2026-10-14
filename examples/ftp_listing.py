"""List files on a remote FTP server using FTPPath.
Requires credentials -- guarded under `if __name__ == "__main__"` so importing
this module is always safe, and skipped (prints setup instructions, exit 0) unless
FTP_EXAMPLE_HOST is set.

Run directly:

    export FTP_EXAMPLE_HOST=ftp.debian.org
    export FTP_EXAMPLE_USER=anonymous       # optional
    export FTP_EXAMPLE_PASSWORD=guest       # optional
    export FTP_EXAMPLE_REMOTE_PATH=/debian  # optional, default shown below
    python examples/ftp_listing.py
"""

import os
import sys

from pathlib_next import FTPPath


def list_ftp(remote: FTPPath):
    # repr() of an FTP path names the server and never shows the password.
    print(f"Listing FTP directory: {remote!r}")
    for child in remote.iterdir():
        kind = "dir " if child.is_dir() else "file"
        size = "" if child.is_dir() else f" ({child.stat().st_size} bytes)"
        print(f"  [{kind}] {child.name}{size}")


if __name__ == "__main__":
    host = os.environ.get("FTP_EXAMPLE_HOST")
    if not host:
        print(
            "FTP_EXAMPLE_HOST is not set -- skipping. See this file's "
            "module docstring for the required environment variables.",
            file=sys.stderr,
        )
        raise SystemExit(0)

    user = os.environ.get("FTP_EXAMPLE_USER")
    password = os.environ.get("FTP_EXAMPLE_PASSWORD")
    remote_path = os.environ.get("FTP_EXAMPLE_REMOTE_PATH", "/")

    remote = FTPPath(remote_path, host=host, user=user, password=password)

    try:
        list_ftp(remote)
    except Exception as error:
        print(f"Could not connect to {remote!r} ({error}); skipping.", file=sys.stderr)
