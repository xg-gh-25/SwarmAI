import pytest

from tokensign import BadSignature, Signer


def test_round_trip():
    s = Signer("k")
    assert s.loads(s.dumps({"a": 1})) == {"a": 1}


def test_changed_token_is_refused():
    s = Signer("k")
    with pytest.raises(BadSignature):
        s.loads(s.dumps({"a": 1})[:-2] + "xx")
