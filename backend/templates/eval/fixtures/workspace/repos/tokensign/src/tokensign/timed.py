import time

from .signer import BadSignature, Signer


class TimedSigner(Signer):
    def dumps(self, obj):
        return super().dumps({"t": int(time.time()), "v": obj})

    def loads(self, token, max_age=None):
        data = super().loads(token)
        age = int(time.time()) - data["t"]
        if age < 0 or (max_age is not None and age > max_age):
            raise BadSignature("expired or from the future")
        return data["v"]
