"""The members of a zip and a tar file as paths: ZipPath and TarPath. Runs
fully offline (no external network or credentials needed).

Run directly:

    python examples/archive_members.py
"""

import io
import tarfile
import tempfile
import zipfile

from pathlib_next import Path, TarPath, ZipPath


def zip_archive_example(tmpdir_path: Path):
    print("--- zip Archive Example ---")
    # Create a local zip file
    zip_file = tmpdir_path / "archive.zip"
    with zipfile.ZipFile(str(zip_file), "w") as zf:
        zf.writestr("hello.txt", "Hello from ZIP!")
        zf.writestr("sub/nested.txt", "Nested content.")

    # The members are the segments of the path; the archive is any Path.
    zip_root = ZipPath(archive=zip_file)

    print("ZIP listing:", sorted(p.name for p in zip_root.iterdir()))

    hello_file = zip_root / "hello.txt"
    print("hello.txt inside ZIP:", hello_file.read_text())

    nested_file = zip_root / "sub" / "nested.txt"
    print("sub/nested.txt inside ZIP:", nested_file.read_text())

    # A zip that is a local file is written in place.
    new_member = zip_root / "new_file.txt"
    new_member.write_text("Dynamically written to ZIP!")

    print("ZIP listing after write:", sorted(p.name for p in zip_root.iterdir()))
    print("new_file.txt content:", new_member.read_text())
    print()


def tar_archive_example(tmpdir_path: Path):
    print("--- tar Archive Example ---")
    tar_file = tmpdir_path / "archive.tar"
    with tarfile.open(str(tar_file), "w") as tf:
        info1 = tarfile.TarInfo(name="readme.txt")
        content1 = b"Welcome to the TAR file."
        info1.size = len(content1)
        tf.addfile(info1, io.BytesIO(content1))

        info2 = tarfile.TarInfo(name="docs/index.html")
        content2 = b"<h1>Index</h1>"
        info2.size = len(content2)
        tf.addfile(info2, io.BytesIO(content2))

    # A tar is read-only.
    tar_root = TarPath(archive=tar_file)

    print("TAR listing:", sorted(p.name for p in tar_root.iterdir()))

    readme = tar_root / "readme.txt"
    print("readme.txt inside TAR:", readme.read_text())

    docs_dir = tar_root / "docs"
    print("docs/ listing:", sorted(p.name for p in docs_dir.iterdir()))
    print()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_archive_example(tmp_path)
        tar_archive_example(tmp_path)
