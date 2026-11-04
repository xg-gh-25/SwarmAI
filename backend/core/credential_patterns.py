"""Credential value shapes: the one authority shared by every display and egress path.

Dependency-free on purpose (the standard library only): ``core.tool_summarizer``
redacts tool rows with these patterns, and importing ``channels.egress_redactor``
from core would run ``channels/__init__`` and load the gateway, DB and session
stack. ``channels.egress_redactor`` re-exports everything here, so channel
egress (one-shot and streaming) and the chat tool rows match the same shapes.
The TSCC security scan reads ``_CREDENTIAL_PATTERNS`` + ``_SECRET_ASSIGN`` only
(not the ``-u user:pass`` shape). ``URL_CREDENTIAL_PARAMS`` / ``PRESIGN_PARAMS`` are
the one definition of the URL parameters that carry a credential (the ingestion
contracts import them).

- ``redact_credentials``  — replace every known shape with ``[REDACTED]``
- ``_CREDENTIAL_PATTERNS`` — whole-match shapes (AWS key id, PEM block, prefixed
  provider token, Bearer value, bare JWT), and the credential-bearing URL
  (``_URL_CREDENTIAL``: a URL is found and split in linear time; only its credential
  values are replaced, the rest of the URL stays readable)
- ``_SECRET_ASSIGN``      — ``key = value`` where the key names a secret; only the
  value (group 1) is masked
- ``_USER_PASS``          — basic-auth flag ``-u``/``--user user:pass``; only the
  password is masked (``_mask_user_pass``); a digits-only password is not a match,
  so ``docker -u 1000:1000`` is never touched

Targeted shapes, not blanket entropy: a base64 blob or a git SHA is left alone.
Add a shape here when a real leak vector is found; every consumer picks it up.
"""

from __future__ import annotations

import re
import sys
from typing import Final, Union
from urllib.parse import unquote_plus

# --- Credential shape patterns (targeted, not blanket-entropy) ---------------
# Each pattern matches a KNOWN secret shape. Ordered longest/most-specific first
# so a broad rule never eats a chunk a specific rule would have labeled better.

_REDACTED: Final[str] = "[REDACTED]"

# AWS access key id: AKIA/ASIA/AROA/AIDA + 16 uppercase-alnum.
_AWS_ACCESS_KEY: Final[re.Pattern] = re.compile(r"\b(?:AKIA|ASIA|AROA|AIDA)[0-9A-Z]{16}\b")

# PEM private-key block (multi-line).
_PEM_BLOCK: Final[re.Pattern] = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    re.DOTALL,
)

# GitHub / common provider tokens with distinctive prefixes. GitHub tokens always
# carry the ``_`` after ``gh[pousr]`` — without it ``gho`` matched path segments
# like ``ghost-blog-theme-starter``.
_PREFIXED_TOKEN: Final[re.Pattern] = re.compile(
    r"\b(?:gh[pousr]_|github_pat|xoxb|xoxp|xoxa|xoxr|sk-|pk-|AIza)[-_A-Za-z0-9]{16,}\b"
)

# Bearer / Authorization header value.
_BEARER: Final[re.Pattern] = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}")

# Bare JWT (header.payload.signature) — matched even without a Bearer prefix,
# since JWTs are routinely echoed/logged standalone. Anchored on the ``eyJ``
# base64url header opener + two more dot-separated base64url segments.
_JWT: Final[re.Pattern] = re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b")

# AWS secret access key: aws_secret_access_key = <40 base64ish>. Only when the
# assignment key names a secret — avoids matching arbitrary 40-char strings.
_SECRET_ASSIGN: Final[re.Pattern] = re.compile(
    r"(?i)\b(?:aws_secret_access_key|secret_access_key|secret_key|api_key|apikey|access_token|auth_token|password|passwd)\b\s*[=:]\s*['\"]?([A-Za-z0-9/+=_\-]{12,})['\"]?"
)

# --- URL credentials ----------------------------------------------------------
# The one definition of the URL parameters that carry a credential, read by every
# display and egress path here and by the ingestion contracts
# (``core.ingestion.contracts_v2`` imports both sets).
PRESIGN_PARAMS: Final[frozenset[str]] = frozenset(
    {"x-amz-signature", "x-amz-credential", "x-amz-security-token"})
URL_CREDENTIAL_PARAMS: Final[frozenset[str]] = frozenset({
    "token", "access_token", "access_key", "sig", "signature", "key", "api_key", "apikey",
    "auth", "password", "secret",
}) | PRESIGN_PARAMS

#: name shapes that mark a parameter as a credential beyond the exact names above
#: (``refresh_token``, ``client_secret``, ``private_token``, ``x-goog-signature``,
#: ``db_password``, ``stripe_key``): the redactors replace these values too, because the
#: rule they replaced redacted every URL carrying one listed name whole
_CREDENTIAL_NAME_WORDS: Final[tuple[str, ...]] = (
    "passw", "pwd", "signature", "credential")
#: words that mark a credential as a whole word of the name (split at ``_ - .`` and at
#: camelCase boundaries) or as its end: ``refresh_token``, ``accessToken``, ``clientSecret``
#: and ``csrftoken`` are; ``max_tokens``, ``maxTokens`` and ``tokenizer`` are not
_CREDENTIAL_WHOLE_WORDS: Final[frozenset[str]] = frozenset({"token", "secret"})
#: split points: ``_ - .``, a lower-case letter or digit before a capital (``accessToken``),
#: the last capital of an acronym before a word (``IDTokenHint``, ``APITokenValue``) and a
#: letter before a digit (``authToken2``)
_NAME_WORD_SPLIT: Final[re.Pattern] = re.compile(
    r"[_\-.]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])")
_CREDENTIAL_NAME_ENDS: Final[tuple[str, ...]] = (
    "_key", "-key", ".key", "apikey", "accesskey", "privatekey")


def is_url_credential_param(name: str) -> bool:
    """Whether a URL parameter named *name* (as written: percent-encoded, any case)
    carries a credential: a name in ``URL_CREDENTIAL_PARAMS``; one holding a word of
    ``_CREDENTIAL_NAME_WORDS`` or ending in ``_CREDENTIAL_NAME_ENDS``; or one where a word of
    ``_CREDENTIAL_WHOLE_WORDS`` (``token``, ``secret``) is a whole word of the name (split at
    ``_NAME_WORD_SPLIT``) or its end (``csrftoken``). A plural (``access_tokens``) is not
    matched: that keeps ``max_tokens`` and ``num_tokens`` readable."""
    raw = unquote_plus(name).strip()
    n = raw.lower()
    words = {w.lower() for w in _NAME_WORD_SPLIT.split(raw)}
    return (n in URL_CREDENTIAL_PARAMS or any(w in n for w in _CREDENTIAL_NAME_WORDS)
            or not _CREDENTIAL_WHOLE_WORDS.isdisjoint(words)
            or n.endswith(tuple(_CREDENTIAL_WHOLE_WORDS)) or n.endswith(_CREDENTIAL_NAME_ENDS))


# A URL: a scheme (ASCII, at most 32 characters) and ``://``, then the run of
# non-whitespace after it. Possessive and bounded where it can fail, so a failed start
# costs at most 34 steps and a match consumes its run: linear in the text (the regex
# this replaced was quadratic on one long URL: 32 KB took 2.4 s). No look-behind: a
# match never depends on the text before it, so a stream segment finds the same URLs
# as the whole text.
_URL_RUN: Final[re.Pattern] = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]{0,31}+://\S*+")
# ``name=value`` after any of ``? & ; #`` (query, fragment, ``;`` parameters, or a
# ``&name=`` glued into the authority or path); the value (group 2) stops at the next
# such separator, so a chain of parameters is read once. From the first credential
# value on, the rest of the URL is redacted (``_url_credential_spans``).
_URL_PARAM: Final[re.Pattern] = re.compile(r"[?&;#]([^?&;#=\"<>`]*+)=([^?&;#\"<>`]*+)")
#: a character no URL holds unencoded: the URL ended before it
_URL_ENDED: Final[re.Pattern] = re.compile(r"[\"<>`]")
#: a value that starts with one of these is empty: nothing to hide
_VALUE_END: Final[re.Pattern] = re.compile(r"[&#\"<>`]")
# what may follow a URL in prose: kept out of a value that ends the run
_TRAILING_PUNCT: Final[str] = ".,:;!?"


def _userinfo_spans(url: str, start: int) -> list[tuple[int, int]]:
    """The password of each ``user:password@`` in *url* after *start*: in the authority,
    in a URL nested in the query (``?next=https://u:p@h``) or in a path segment. Read per
    ``/``-separated segment, failing closed: from the segment's first ``:`` to its last
    ``@``, so every ``user:password@`` the rule this replaced matched (a user without
    ``/ @ :``, a password without ``/ @``) is inside the span, and a password holding
    ``# ? @ "`` is too; what sits between them (``pw@host?ref=me@x``) goes with it.
    One pass over the segments: linear."""
    spans: list[tuple[int, int]] = []
    pos, n = start, len(url)
    while pos < n:
        end = url.find("/", pos)
        end = n if end < 0 else end
        at = url.rfind("@", pos, end)
        if at > pos:
            colon = url.find(":", pos, at)
            if 0 <= colon < at - 1:
                spans.append((colon + 1, at))
        pos = end + 1
    return spans


def _url_credential_spans(url: str) -> list[tuple[int, int]]:
    """Where the credential values sit in *url* (one ``_URL_RUN`` match), sorted and
    disjoint: every userinfo password (``_userinfo_spans``), and everything from the value
    of the first parameter ``is_url_credential_param`` names to the end of the URL (less
    trailing prose punctuation). The split is done by hand rather than
    with ``urllib.parse.urlsplit``: its authority ends at the first ``?`` or ``#``, so a
    password holding one (``https://u:p#ss@h``) would not be found."""
    start = url.index("://") + 3
    spans = _userinfo_spans(url, start)
    for m in _URL_PARAM.finditer(url, start):
        if not is_url_credential_param(m.group(1)) or m.end(2) == m.start(2) == len(url):
            continue
        if m.start(2) < len(url) and not _VALUE_END.match(url, m.start(2)):
            # failing closed from the first credential value to the end of the URL: a
            # session id or an OAuth code in a parameter no name rule knows, next to it,
            # goes too (the rule this replaced redacted the whole rest of such a URL)
            # ... up to a character no URL may hold unencoded (RFC 3986: ``" < >`` and
            # the backtick), where the URL ended: a JSON string or a markup tag around it
            # keeps its closing quote
            end = _URL_ENDED.search(url, m.start(2))
            spans.append((m.start(2), end.start() if end else len(url)))
            break
    if not spans:
        return spans
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    a, b = merged[-1]
    if b == len(url):
        # keep sentence punctuation and a ``)`` the URL did not open (``[x](https://…)``)
        unopened = max(0, url.count(")") - url.count("("))
        while b - 1 > a and (url[b - 1] in _TRAILING_PUNCT or (url[b - 1] == ")" and unopened)):
            unopened -= url[b - 1] == ")"
            b -= 1
        merged[-1] = (a, b)
    return merged


class _UrlCredentialShape:
    """The credential-bearing URL shape, with the methods its consumers use on a
    ``re.Pattern`` (``sub``, ``finditer``, ``findall``, ``search``). A match is a whole
    URL that carries a credential; ``sub(repl, text)`` replaces each span of
    ``_url_credential_spans`` with *repl* (a string): the scheme, host, path and the
    parameters before the first credential stay, so a reader still sees which link it
    was while the link no longer works."""

    def finditer(self, text: str, pos: int = 0, endpos: int = sys.maxsize):
        for m in _URL_RUN.finditer(text, pos, endpos):
            if _url_credential_spans(m.group(0)):
                yield m

    def search(self, text: str, pos: int = 0, endpos: int = sys.maxsize):
        return next(self.finditer(text, pos, endpos), None)

    def findall(self, text: str, pos: int = 0, endpos: int = sys.maxsize) -> list[str]:
        return [m.group(0) for m in self.finditer(text, pos, endpos)]

    def sub(self, repl: str, text: str) -> str:
        out: list[str] = []
        last = 0
        for m in _URL_RUN.finditer(text):
            url = m.group(0)
            spans = _url_credential_spans(url)
            if not spans:
                continue
            out.append(text[last:m.start()])
            cur = 0
            for a, b in sorted(spans):
                out.append(url[cur:a] + repl)
                cur = b
            out.append(url[cur:])
            last = m.end()
        if not out:
            return text
        out.append(text[last:])
        return "".join(out)


# URL carrying an embedded credential (``user:pass@`` for any scheme, or a parameter
# named in URL_CREDENTIAL_PARAMS): the password, and the URL from its first credential
# value on, are replaced; scheme, host, path and earlier parameters stay readable.
_URL_CREDENTIAL: Final[_UrlCredentialShape] = _UrlCredentialShape()

# HTTP basic-auth flag: ``-u user:pass``, ``-uuser:pass``, ``-su user:pass``,
# ``--user=user:pass``, ``--proxy-user user:pass``, ``--user "user:pass word"``,
# ``-u user:'pass'``. Anchored on the FLAG, not on a ``curl`` word: a command-word
# anchor sits arbitrarily far from the password (``$(curl …)``, ``ssh h "curl …"``,
# ``/usr/bin/curl``, line continuations), and connecting the two means parsing
# shell — every version of that leaked a new form. The flag shape is local and
# linear (bounded classes, no gap), and needs no streaming special case beyond the
# in-flight opener every shape gets. ``-U`` is curl's ``--proxy-user``. Not a match:
# a digits:digits pair (``docker -u 1000:1000``) and a value starting with ``/`` or
# ``~`` (``rsync -avu host:/srv/app/``); ``docker -u app:app`` shows
# ``app:[REDACTED]``, a harmless display false positive. Masked by
# ``_mask_user_pass``: the password only.
# Every run is BOUNDED and POSSESSIVE (``{1,128}+``, Python 3.11+). Unbounded, a
# flag-shaped start in a long colon-free run (``--user=`` repeated) reads the whole
# run at EVERY start — quadratic on the synchronous egress path (280 KB took 27 s).
# Bounded, each start costs a constant: a user name over 128 or a password over 256
# characters is not matched (no real basic-auth value is that long).
_FLAG_WS: Final[str] = r"(?:[^\S\n]{1,16}+(?:\\\n[^\S\n]{0,16}+)?|\\\n[^\S\n]{0,16}+)"
# Upper bound of a _USER_PASS match (flag + spacing + quoted user + value), used by
# the stream redactor to scan only a window around its emit point.
USER_PASS_MAX_LEN: Final[int] = 512
_USER_PASS: Final[re.Pattern] = re.compile(
    r"""(?<![\w-])((?:-[A-Za-z]{0,16}+(?<=[uU])""" + _FLAG_WS + r"""?"""
    r"""|-[uU](?=[^\s:'"()$]{1,128}+:)"""  # attached: -uadmin:pass
    r"""|--(?:proxy-)?user(?:""" + _FLAG_WS + r"""|=)))"""
    # uid:gid (both all digits) is not a credential: docker -u 1000:1000
    r"""(?!['"]?\d{1,12}+:\d{1,12}+(?:[\s'";|&)]|$))"""
    # the user part is never a ``$(...)`` / ``$VAR`` expression (docker -u $(id -u):$(id -g))
    # or a git revision (git log -u HEAD~1:file); a value is never a `command` substitution
    r"""(?:(['"])([^\s:'"()$~^]{1,128}+:)([^'"\n]{1,256}+)\2"""
    # a value starting with / or ~ is a remote path (rsync -avu host:/srv/app/), one
    # starting with $ is a variable reference; an unquoted value ends at a separator
    r"""|(['"]?[^\s:'"()$~^]{1,128}+['"]?:)(?![/~$`])('[^'\n]{0,256}+'|"[^"\n]{0,256}+"|[^\s'";|&)`]{1,256}+))"""
)


def _mask_user_pass(m: re.Match) -> str:
    """``-u user:pass`` → ``-u user:[REDACTED]`` (keeps any quotes)."""
    if m.group(2):  # the whole "user:pass" is quoted
        return m.group(1) + m.group(2) + m.group(3) + _REDACTED + m.group(2)
    return m.group(1) + m.group(5) + _REDACTED


_CREDENTIAL_PATTERNS: Final[tuple[Union[re.Pattern, _UrlCredentialShape], ...]] = (
    _PEM_BLOCK,
    _AWS_ACCESS_KEY,
    _PREFIXED_TOKEN,
    _BEARER,
    _JWT,
    _URL_CREDENTIAL,
)


def redact_credentials(text: str) -> str:
    """Redact known credential shapes from *text*.

    Targets specific secret formats (AWS keys, PEM blocks, prefixed provider
    tokens, bearer tokens, secret assignments, the credential values inside URLs,
    ``-u user:pass``) — NOT
    generic high-entropy strings, so legitimate base64/SHA content survives.
    """
    if not text:
        return text
    for pat in _CREDENTIAL_PATTERNS:
        text = pat.sub(_REDACTED, text)

    # Secret assignments: keep the key name, redact only the value (group 1).
    def _mask_assign(m: re.Match) -> str:
        whole = m.group(0)
        val = m.group(1)
        return whole.replace(val, _REDACTED, 1)

    text = _SECRET_ASSIGN.sub(_mask_assign, text)
    # Basic auth on a curl / wget line: keep ``-u user:``, mask the password.
    text = _USER_PASS.sub(_mask_user_pass, text)
    return text
