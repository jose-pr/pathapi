# pathlib-next

**This project moved: it is now [`pathapi`](https://github.com/jose-pr/pathapi).**

`pathlib-next` 0.10.1 is the last release under this name. It contains no code of
its own: it depends on `pathapi` and `import pathlib_next` re-exports `pathapi`
(every name and every submodule, as the same objects) after a `DeprecationWarning`.
`pathapi` 0.1.0 is `pathlib-next` 0.10.0 plus the protocols and the changes in its
[changelog](https://github.com/jose-pr/pathapi/blob/main/CHANGELOG.md).

## Migrate

| Before | After |
| --- | --- |
| `pip install pathlib-next` | `pip install pathapi` |
| `import pathlib_next`, `pathlib_next.<module>` | `import pathapi`, `pathapi.<module>` |
| entry-point group `pathlib_next.http_transports` | `pathapi.http_transports` |
| `pathlib-next-requests` | `pathapi-requests` |
| `PATHLIB_NEXT_HTTP_TRANSPORT` | `PATHAPI_HTTP_TRANSPORT` (the old one is copied when the new one is unset) |

Documentation: <https://jose-pr.github.io/pathapi/>.
