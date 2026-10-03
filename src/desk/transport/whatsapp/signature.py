"""Webhook authentication (S81): HMAC-SHA256 of the raw request body with the app secret,
sent by Meta as `X-Hub-Signature-256: sha256=<hex>`. Compared in constant time.

The GET verify token only proves subscription setup; it never authenticates a POST.
"""

import hashlib
import hmac

PREFIX = "sha256="


def expected_signature(app_secret: str, raw_body: bytes) -> str:
    return PREFIX + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def signature_valid(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith(PREFIX):
        return False
    return hmac.compare_digest(expected_signature(app_secret, raw_body), header)


def verify_token_valid(configured: str, offered: str | None) -> bool:
    if not configured or offered is None:
        return False
    return hmac.compare_digest(configured.encode(), offered.encode())
