# `http:` and `dav:`

Both run on the standard library, through a transport that can be replaced
(`HTTPTransport` by default, `RequestsTransport` with the `http` extra).

::: pathlib_next.uri.schemes.http
    options:
      members:
        - DEFAULT_TIMEOUT
        - MAX_LISTING_BYTES
        - HttpBackend
        - HTTPResponse
        - HTTPTransport
        - RequestsTransport
        - HttpPath
        - HttpWriteStream
        - HttpAppendStream
      show_if_no_docstring: true
      filters: ["!^_"]

::: pathlib_next.uri.schemes.dav
    options:
      members:
        - DavPath
