# Changelog

## 2.1.0

- Added `fallback_digests` so a service can move to a new digest without invalidating
  the tokens it already issued.

## 2.0.0

- **Breaking:** `loads()` now raises `BadSignature` for a changed or forged token; 1.x
  returned `None`. Code written for 1.x that checks `if signer.loads(t) is None` and
  catches nothing gets an exception instead of a `None` and must catch `BadSignature`.

## 1.4.2

- Fixed: `TimedSigner` accepted a token whose timestamp was in the future.
