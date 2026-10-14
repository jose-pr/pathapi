# Protocols API

The small protocols `Path` is built from. A class implements the primitives of
the protocols it lists (`stat`, `_open`, `iterdir`, `unlink`, `readlink`, ...) and
inherits everything derived from them; `supports()` and `capabilities()` say which
primitives a class has.

::: pathapi.protocols
    options:
      members:
        - supports
        - capabilities

::: pathapi.protocols.pure

::: pathapi.protocols.fs

::: pathapi.protocols.io

::: pathapi.protocols.tree

::: pathapi.protocols.links

::: pathapi.protocols.resolve

::: pathapi.protocols.transfer

::: pathapi.protocols.checksum
