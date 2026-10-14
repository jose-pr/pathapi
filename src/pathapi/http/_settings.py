"""The module-level setting of the HTTP client. Code reads it as an attribute
of this module, so assigning it applies at once."""

from __future__ import annotations

MAX_REPLY_BYTES = 8 * 1024 * 1024
"""Most bytes of a reply the library reads whole, after any content decoding:
what `HTTPClient.request(stream=False)` returns, and a reply read for what it
says (`read_limited()`, `json_body()`). A longer body raises `OSError(EFBIG)`
naming the request and closes the connection. The body of a reply nobody looks
at is read up to this many bytes and dropped (`drop_body()`). Raise it for a
document that is legitimately larger. Read at the time of each use."""
