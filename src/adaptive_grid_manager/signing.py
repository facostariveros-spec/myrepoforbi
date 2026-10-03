from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import urlencode


class HmacSha256Signer:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def sign(self, parameters: Mapping[str, object]) -> dict[str, object]:
        unsigned = dict(parameters)
        query = urlencode(unsigned)
        signature = hmac.new(self._secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        return {**unsigned, "signature": signature}

