# Design

## Digest

Signatures are HMAC. The default digest is SHA-1, kept for compatibility with tokens
issued by 1.x.

Changing `digest` (for example to SHA-256) makes every token signed before the change
fail verification. To migrate, pass the old digest in `fallback_digests`: new tokens are
signed with `digest`, old ones still verify, and the fallback can be removed once the
longest-lived old token has expired.

## Timestamps

`TimedSigner` adds the signing time, in whole seconds, taken from the signing host's
clock. `loads(max_age=...)` compares it with the verifying host's clock. When the hosts'
clocks disagree, a fresh token can be rejected as expired, or an old one accepted past
its age. Keep every host that signs or verifies on NTP.

## Keys

The secret key is the only protection: anyone who has it can sign anything. To rotate,
put the old key in `fallback_keys` until the tokens signed with it have expired.
