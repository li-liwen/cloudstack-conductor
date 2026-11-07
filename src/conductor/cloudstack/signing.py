"""CloudStack API signature (version 1).

CloudStack canonicalises the request by sorting parameter names, URL-encoding
values the way Java's ``URLEncoder`` does, lower-casing the *entire* signing
string (including parameter names), then HMAC-SHA1 with the user's secret.

This is generic logic; no endpoint, key, or secret is embedded here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from collections.abc import Mapping


def java_urlencode(value: object) -> str:
    """Match ``java.net.URLEncoder.encode(String.valueOf(value), "UTF-8")``.

    CloudStack additionally replaces ``+`` with ``%20`` before lower-casing,
    which is what the reference client does and what the server accepts.
    """

    encoded = urllib.parse.quote_plus(str(value), safe="*")
    return encoded.replace("+", "%20").replace("~", "%7E")


def signing_string(params: Mapping[str, object]) -> str:
    return "&".join(
        f"{key}={java_urlencode(value)}" for key, value in sorted(params.items()) if value is not None
    ).lower()


def sign(params: Mapping[str, object], secret: str) -> str:
    """Return the base64 HMAC-SHA1 signature for ``params``."""

    payload = signing_string(params)
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")
