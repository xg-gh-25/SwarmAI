from tokensign import Signer


def test_new_digest_with_fallback_still_reads_old_tokens():
    old = Signer("k").dumps({"a": 1})            # the default digest of earlier releases
    s = Signer("k", digest="sha256", fallback_digests=("sha1",))
    assert s.loads(old) == {"a": 1}
