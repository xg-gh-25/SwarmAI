"""URL credentials: one definition, every exit, linear time (design
docs/design/2026-11-04-kirocrew-borrow.md §3 A2).

Questions: are the values of presigned-URL parameters (X-Amz-Signature,
X-Amz-Security-Token, ...) replaced while the host and path stay readable, and the
password of a ``user:password@`` userinfo for any scheme; does a long credential-free
URL take linear time, and does the stream redactor's chunked output still equal the
one-shot result; do the ingestion contracts read the same parameter set object, with
``sanitize_source_url`` giving the results it gave before.
"""
from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core import credential_patterns as cp
from core.credential_patterns import redact_credentials

SIG = "0f" * 32
PRESIGNED = (
    "https://my-bucket.s3.us-east-1.amazonaws.com/reports/q3.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=EXAMPLEID%2F20261104%2Fus-east-1%2Fs3%2Faws4_request"
    "&X-Amz-Date=20261104T000000Z&X-Amz-Expires=3600"
    "&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEXAMPLE%2Ftoken%3D"
    f"&X-Amz-SignedHeaders=host&X-Amz-Signature={SIG}"
)
PRESIGNED_REDACTED = (
    "https://my-bucket.s3.us-east-1.amazonaws.com/reports/q3.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=[REDACTED]"
)


def test_presigned_values_redacted():
    out = redact_credentials(f"download it from {PRESIGNED} before noon")
    assert out == f"download it from {PRESIGNED_REDACTED} before noon"
    assert SIG not in out and "IQoJb3JpZ2lu" not in out
    # from the first credential value on the URL goes, failing closed; what comes
    # before it stays readable
    assert redact_credentials("https://api.example.com/v1?q=books&api_key=abc123&page=2") == \
        "https://api.example.com/v1?q=books&api_key=[REDACTED]"
    assert redact_credentials("https://x.example/cb#access_token=zzz111&state=s") == \
        "https://x.example/cb#access_token=[REDACTED]"
    assert redact_credentials("https://x.example/?access_key=abcdef") == \
        "https://x.example/?access_key=[REDACTED]"
    # a markdown link keeps its closing parenthesis
    assert redact_credentials("[report](https://h.example/p?token=abc)") == \
        "[report](https://h.example/p?token=[REDACTED])"
    # userinfo password, any scheme
    for raw, want in [
        ("postgres://admin:s3cr3tpw@db.internal:5432/app", "postgres://admin:[REDACTED]@db.internal:5432/app"),  # pragma: allowlist secret  (intentional fake fixture)
        ("redis://:hunter2pass@cache:6379/0", "redis://:[REDACTED]@cache:6379/0"),  # pragma: allowlist secret  (intentional fake fixture)
        ("mongodb+srv://u:pw9@c.example.net/db?retryWrites=true",  # pragma: allowlist secret  (intentional fake fixture)
         "mongodb+srv://u:[REDACTED]@c.example.net/db?retryWrites=true"),
        ("https://deploy:tok3n@git.example.com/o/r.git", "https://deploy:[REDACTED]@git.example.com/o/r.git"),  # pragma: allowlist secret  (intentional fake fixture)
    ]:
        assert redact_credentials(f"url={raw} ok") == f"url={want} ok", raw
    # what the whole-URL rule this replaced also covered: secrets under other names,
    # passwords holding ``#`` / ``?`` / ``>``, nested URLs, a path userinfo
    for raw, want in [
        ("https://idp.example.com/cb?token=t1&refresh_token=RTSECRET&client_secret=CSSECRET",
         "https://idp.example.com/cb?token=[REDACTED]"),
        ("https://h.example/x?key=k&private_token=GLPATSECRET",
         "https://h.example/x?key=[REDACTED]"),
        ("https://admin:p#ssW0rd@host.example/", "https://admin:[REDACTED]@host.example/"),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://admin:p?ssW0rd@host.example/", "https://admin:[REDACTED]@host.example/"),  # pragma: allowlist secret  (intentional fake fixture)
        ("mysql://root:pa>ss@h.example/db", "mysql://root:[REDACTED]@h.example/db"),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://h.example/login?next=https://admin:HUNTER2@db.internal/",  # pragma: allowlist secret  (intentional fake fixture)
         "https://h.example/login?next=https://admin:[REDACTED]@db.internal/"),
        ("https://proxy.example/get?url=postgres://admin:hunter2@db/x",  # pragma: allowlist secret  (intentional fake fixture)
         "https://proxy.example/get?url=postgres://admin:[REDACTED]@db/x"),
        ("https://h.example/x/u:pw@y", "https://h.example/x/u:[REDACTED]@y"),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://host.example&token=SECRETTOK", "https://host.example&token=[REDACTED]"),
        ("https://h.example/?token=abc;def#frag", "https://h.example/?token=[REDACTED]"),
        ("https://h.example/?a=1;token=xyz&b=2", "https://h.example/?a=1;token=[REDACTED]"),
        ("https://h.example/?next=/in?token=abc", "https://h.example/?next=/in?token=[REDACTED]"),
        ("https://h.example/?token=ab'cdSECRET", "https://h.example/?token=[REDACTED]"),
            # a later ``@`` (an email next to a DSN) never moves the password out of the span
        ('{"dsn":"postgres://u:SECRETPW@h","email":"a@b.com"}', '{"dsn":"postgres://u:[REDACTED]@b.com"}'),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://u:SECRETPW@host?ref=me@x.com", "https://u:[REDACTED]@x.com"),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://user:SECRET@PW@host/", "https://user:[REDACTED]@host/"),  # pragma: allowlist secret  (intentional fake fixture)
        ("redis://default:SECRETPW@cache:6379?x=a:b@c", "redis://default:[REDACTED]@c"),  # pragma: allowlist secret  (intentional fake fixture)
        ("https://h.example/?token=;SECRETPW", "https://h.example/?token=[REDACTED]"),
    # what follows the URL stays: punctuation, a closing quote
        ("see https://h.example/?token=abc. Next, https://h.example/?token=abc, and",
         "see https://h.example/?token=[REDACTED]. Next, https://h.example/?token=[REDACTED], and"),
        ('{"u":"https://h.example/?token=abc","n":1}', '{"u":"https://h.example/?token=[REDACTED]","n":1}'),
    ]:
        assert redact_credentials(raw) == want, raw
    # the tool rows (display path) mask the same userinfo shapes
    from core.tool_summarizer import redact_display
    for raw in ("postgres://admin:p#ssW0rd@db:5432/x", "redis://:p?ss@h:6379",  # pragma: allowlist secret  (intentional fake fixture)
                "git clone https://x.example/r?next=https://bob:tok3n@git.example/r"):  # pragma: allowlist secret  (intentional fake fixture)
        out = redact_display(raw, 500)
        assert "[REDACTED]@" in out and not any(p in out for p in ("p#ssW0rd", "p?ss", "tok3n")), out
    # a URL carrying no credential is left as it is
    for plain in ("https://github.com/acme/lib/pull/3", "https://example.com/search?q=token&key2=1",
                  "ssh://git@github.com/acme/lib.git"):
        assert redact_credentials(plain) == plain


def _stream(text: str, size: int) -> str:
    from channels.egress_redactor import StreamRedactor
    r = StreamRedactor()
    out = "".join(r.feed(text[i:i + size]) for i in range(0, len(text), size))
    return out + r.flush()


def test_long_url_linear_and_stream_equal():
    from channels.egress_redactor import redact_text
    url = "http://" + "a" * (32_000 - len("http://"))
    assert len(url) == 32_000
    for fn in (redact_credentials, redact_text):
        t0 = time.perf_counter()
        assert fn(url) == url
        assert time.perf_counter() - t0 < 0.1, fn.__name__
    text = (
        f"Here is the file: {PRESIGNED}\n"
        "and the db is postgres://admin:s3cr3tpw@db.internal:5432/app, "  # pragma: allowlist secret  (intentional fake fixture)
        "call https://api.example.com/v1?q=books&api_key=abc123&page=2 with "
        "Bearer abcdefghijklmnopqrstuvwxyz0123 and password = hunter2hunter2 "
        "(see https://github.com/acme/lib/pull/3).\n"
    ) * 3
    whole = redact_text(text)
    assert whole == redact_credentials(text) and SIG not in whole
    for size in (1, 3, 7, 64, len(text)):
        assert _stream(text, size) == whole, size


def test_single_param_set_shared():
    from core.ingestion import contracts_v2 as c2
    assert c2.URL_CREDENTIAL_PARAMS is cp.URL_CREDENTIAL_PARAMS
    assert c2.PRESIGN_PARAMS is cp.PRESIGN_PARAMS
    assert cp.PRESIGN_PARAMS <= cp.URL_CREDENTIAL_PARAMS
    # the redactor reads that set: every name in it has its value replaced
    for name in sorted(cp.URL_CREDENTIAL_PARAMS):
        assert redact_credentials(f"https://h.example/p?{name.upper()}=v1&n=2") == \
            f"https://h.example/p?{name.upper()}=[REDACTED]", name
    # sanitize_source_url gives the results it gave before the move
    assert c2.sanitize_source_url("https://Example.COM/a/b/?utm_source=x&q=1&token=abc#frag") == \
        "https://example.com/a/b?q=1"
    assert c2.sanitize_source_url("https://example.com:8443/docs/?Key=1&page=2") == \
        "https://example.com:8443/docs?page=2"
    assert c2.sanitize_source_url("https://example.com/x?sig=1&expires=2&id=3") == \
        "https://example.com/x?expires=2&id=3"
    assert c2.sanitize_source_url("https://example.com/") == "https://example.com"
    for bad, why in [("https://example.com/p?X-Amz-Signature=abc", "credentialed_url"),
                     ("http://example.com/", "must be https"),
                     ("https://u:p@example.com/", "no credentials")]:
        with pytest.raises(ValueError, match=why):
            c2.sanitize_source_url(bad)
    # one change, on purpose: access_key joined the set (the redaction rule it replaced
    # covered it), so ingestion drops and rejects it as well
    assert c2.sanitize_source_url("https://example.com/x?access_key=1&id=3") == "https://example.com/x?id=3"
    assert c2.semantic_urls_have_credentials("see https://a.com/x?access_key=1")
    assert c2.semantic_urls_have_credentials("see https://a.com/x?apikey=1")
    assert not c2.semantic_urls_have_credentials("see https://a.com/x?q=1")


_USER = st.text(alphabet="abcxyz09._-+~!$%", min_size=1, max_size=6)
_AROUND = st.text(alphabet="ab:/?#&=;.@'\"<>()x9", max_size=14)
_PW = st.text(alphabet="abcXYZ09:?#&=;.'\"<>()!$%^*+,", max_size=8)


@given(prefix=_AROUND, user=_USER, pw=_PW, suffix=_AROUND)
@settings(max_examples=400, deadline=None)
def test_userinfo_never_less_than_before(prefix, user, pw, suffix):
    """Every ``user:password@`` the rule this replaced redacted (a user without
    ``/ @ :``, a password without ``/ @`` or whitespace, anywhere in the URL) still has
    its password replaced, whatever comes before or after it, one-shot and streamed."""
    marked = "S3CRET" + pw
    text = f"see https://{prefix}{user}:{marked}@{suffix} now"
    for out in (redact_credentials(text), _stream(text, 5)):
        assert "S3CRET" not in out, (text, out)


def test_secrets_next_to_a_credential_do_not_get_out():
    """A secret in a parameter no name rule knows, after a credential parameter, goes with
    it (a session id, an OAuth code), whatever separates them."""
    for text in ("https://h.example/?key=1&sid=SESSIONSECRET&code=OAUTHCODE",
                 "https://h.example/?token=abc#sid=SESSIONSECRET;code=OAUTHCODE"):
        for out in (redact_credentials(text), _stream(text, 5)):
            assert "SESSIONSECRET" not in out and "OAUTHCODE" not in out, (text, out)
            assert out.startswith("https://h.example/?"), out


def test_token_and_secret_are_whole_words_of_a_name():
    """``refresh_token`` and ``client_secret`` carry a credential; ``max_tokens`` and
    ``tokenizer`` do not, so ordinary links keep their values."""
    assert cp.is_url_credential_param("refresh_token")
    assert cp.is_url_credential_param("client_secret")
    assert cp.is_url_credential_param("X-Goog-Signature")
    # camelCase and joined names, which the rule before matched as substrings
    for name in ("accessToken", "refreshToken", "idToken", "sessionToken", "clientSecret",
                 "authtoken", "csrftoken", "apitoken", "accessTokenValue", "idTokenHint",
                 "IDTokenHint", "APITokenValue", "XMLTokenValue", "authToken2"):
        assert cp.is_url_credential_param(name), name
        assert "LIVESECRET" not in redact_credentials(f"https://h.example/cb?{name}=LIVESECRET&sid=S2")
    for name in ("max_tokens", "maxTokens", "tokenizer", "num_tokens"):
        assert not cp.is_url_credential_param(name), name
    assert redact_credentials("https://api.example.com/v1?max_tokens=100&tokenizer=bert") == \
        "https://api.example.com/v1?max_tokens=100&tokenizer=bert"
