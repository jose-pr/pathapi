"""Send HTTP requests with `pathapi.http`: redirects followed under the
rule that `Authorization` never reaches another origin, a failed status turned
into the `OSError` that `pathlib` raises. Requires network access --
guarded under `if __name__ == "__main__"` so importing this module is always
safe, and skipped (prints setup instructions, exit 0, no request) unless
HTTP_EXAMPLE_URL is set.

Run directly:

    HTTP_EXAMPLE_URL=https://example.com/ python examples/http_request.py
"""

import os
import sys

from pathapi.http import HTTPClient, raise_for_status, translate_errors


def fetch(url: str) -> bytes:
    # A status of 400 or more is a reply, not an error; translate_errors()
    # makes the one raised for it the exception pathlib raises for the same
    # condition (a 404 is a FileNotFoundError).
    with HTTPClient() as client, translate_errors(url):
        reply = client.request("GET", url)
        raise_for_status(reply)
        body = reply.read()
    print(f"{url}: {reply.status} {reply.reason}, {len(body)} bytes")
    return body


if __name__ == "__main__":
    url = os.environ.get("HTTP_EXAMPLE_URL")
    if not url:
        print(
            "HTTP_EXAMPLE_URL is not set -- skipping. See this file's "
            "module docstring for how to run it.",
            file=sys.stderr,
        )
        raise SystemExit(0)

    try:
        fetch(url)
    except OSError as error:
        print(f"Could not fetch {url} ({error}); skipping.", file=sys.stderr)
