# Protocols API

The small protocols `Path` is built from. A class implements the primitives of
the protocols it lists (`stat`, `_open`, `iterdir`, `unlink`, `readlink`, ...) and
inherits everything derived from them; `supports()` and `capabilities()` say which
primitives a class has.

::: pathlib_next.protocols
    options:
      members:
        - supports
        - capabilities

::: pathlib_next.protocols.pure

::: pathlib_next.protocols.fs

::: pathlib_next.protocols.io

::: pathlib_next.protocols.tree

::: pathlib_next.protocols.links

::: pathlib_next.protocols.resolve

::: pathlib_next.protocols.transfer

::: pathlib_next.protocols.checksum
