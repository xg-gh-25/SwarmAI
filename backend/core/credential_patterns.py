"""Credential value shapes: the one authority shared by every display and egress path.

Dependency-free on purpose (only ``re`` / ``typing``): ``core.tool_summarizer``
redacts tool rows with these patterns, and importing ``channels.egress_redactor``
from core would run ``channels/__init__`` and load the gateway, DB and session
stack. ``channels.egress_redactor`` re-exports everything here, so channel
egress (one-shot and streaming) and the chat tool rows match the same shapes.
The TSCC security scan reads ``_CREDENTIAL_PATTERNS`` + ``_SECRET_ASSIGN`` only
(not the ``-u user:pass`` shape).

- ``redact_credentials``  — replace every known shape with ``[REDACTED]``
- ``_CREDENTIAL_PATTERNS`` — whole-match shapes (AWS key id, PEM block, prefixed
  provider token, Bearer value, bare JWT, credential-bearing URL)
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
from typing import Final

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

# URL carrying an embedded credential (user:pass@host or ?token=/?key=/?password=).
# Written with non-overlapping char classes (no nested unbounded quantifiers) to
# avoid catastrophic backtracking: one contiguous non-whitespace URL run, then
# the two credential shapes are distinguished inside it.
_URL_CREDENTIAL: Final[re.Pattern] = re.compile(
    r"(?i)\b(?:https?|ftp)://\S*?(?:[^\s/@:]+:[^\s/@]+@|[?&](?:token|key|secret|password|access_key|api_key)=)\S+"
)

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


_CREDENTIAL_PATTERNS: Final[tuple[re.Pattern, ...]] = (
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
    tokens, bearer tokens, secret assignments, credential-bearing URLs,
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
