import base64
import hashlib
import hmac
import json


class BadSignature(Exception):
    """The token was changed, forged, or signed with another key or digest."""


class Signer:
    def __init__(self, secret_key, digest="sha1", fallback_digests=(), fallback_keys=()):
        self.keys = [secret_key, *fallback_keys]
        self.digest = digest
        self.digests = [digest, *fallback_digests]

    def _sign(self, key, digest, payload):
        mac = hmac.new(key.encode(), payload, getattr(hashlib, digest))
        return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=")

    def dumps(self, obj):
        payload = base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=")
        return (payload + b"." + self._sign(self.keys[0], self.digest, payload)).decode()

    def loads(self, token):
        payload, _, sig = token.encode().rpartition(b".")
        for key in self.keys:
            for digest in self.digests:
                if hmac.compare_digest(sig, self._sign(key, digest, payload)):
                    padded = payload + b"=" * (-len(payload) % 4)
                    return json.loads(base64.urlsafe_b64decode(padded))
        raise BadSignature(token)
