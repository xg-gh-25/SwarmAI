# tokensign

tokensign signs data so that a value sent through an untrusted channel (a cookie, a
query string, a link in an email) can be checked when it comes back: if anyone changed
it, `loads()` refuses it.

tokensign does not encrypt. A signed value is readable by anyone who holds it, so never
put a secret in one.

## Usage

```python
from tokensign import Signer

signer = Signer(secret_key="change-me")
token = signer.dumps({"user": 42})
data = signer.loads(token)          # raises BadSignature if the token was changed
```

## How it compares

JWT libraries put the algorithm in the token header, so a forged token can ask to be
verified with `none` or a weaker algorithm. A tokensign token carries no algorithm
field: the server alone decides the digest, so that attack does not exist.
