"""Checks that a webhook request really came from Twilio (X-Twilio-Signature header)."""

import base64
import hashlib
import hmac


def expected(auth_token, url, params):
    data = url + "".join(k + str(params[k]) for k in sorted(params))
    return base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()


def valid(auth_token, url, params, signature):
    return hmac.compare_digest(expected(auth_token, url, params), signature or "")
