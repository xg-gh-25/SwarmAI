"""Egress content redaction for outbound channel messages.

Closes Phase-0 gap **G1**: before this module, outbound channel text (Slack
replies, error messages, streaming tokens) reached the platform with **zero**
content redaction — only inbound filenames were sanitized (``_sanitize_filename``).
A credential or exfiltration URL produced by the agent (or echoed from an MCP
tool result) went out verbatim. That is exactly the C041 lesson (block leaks
structurally at the boundary, never rely on the model to self-censor).

Two entry points:

* :func:`redact_text` — one-shot redaction for a complete string. Wired into
  ``OutboundMessage.__post_init__`` (channels/base.py) so **every** non-streaming
  send site is covered at a single chokepoint (STEERING #1: structural, not
  per-call-site; R25: no divergence between "redacted" and "un-redacted" sends).

* :class:`StreamRedactor` — rolling-buffer redaction for the *native streaming*
  path (Slack ``append_stream`` is append-only, so each emitted chunk must be
  self-safe — there is no final overwrite to fix a leaked partial). It withholds
  the trailing not-yet-complete token so a credential split across two chunks
  (``AKIA…`` in chunk N, the rest in chunk N+1) never leaves half-redacted.
  ``flush()`` releases the withheld tail (redacted) at stream end.

Design of the patterns (pre-mortem: "redactor over-matches legit content"),
which live in ``core.credential_patterns`` (shared with the chat tool rows):
the patterns target **known credential shapes** (AWS access-key ids, PEM
private-key headers, prefixed provider tokens ghp_/xoxb/AIza/sk-, bearer tokens,
bare JWTs, ``key=secret`` assignments) and URLs carrying embedded credentials —
NOT blanket high-entropy strings. A base64 blob or a git SHA in a code block is
left alone.

⚠️ **Scope honesty (this is a denylist, not a proof of completeness).** It
catches the common, distinctive secret shapes above; it does NOT catch every
possible secret (e.g. a bare 40-char AWS *secret* key with no assignment key and
no distinctive prefix is indistinguishable from any 40-char string and is left
alone to avoid mangling legitimate content). This layer is a strong structural
backstop, not a guarantee — it is one defense among several (the file-access
sandbox already prevents a non-owner from *reading* XG's secrets in the first
place; this stops the common *echo/exfil* shapes on the way out). Add a new
shape here when a real leak vector is identified; do not widen to blanket entropy
(that regressed to mangling code blocks in the pre-mortem).
"""

from __future__ import annotations

import re
from typing import Final

from core.credential_patterns import (  # noqa: F401  (re-exported: tscc + tests)
    _AWS_ACCESS_KEY,
    _BEARER,
    _CREDENTIAL_PATTERNS,
    _JWT,
    _PEM_BLOCK,
    _PREFIXED_TOKEN,
    _REDACTED,
    _SECRET_ASSIGN,
    _URL_CREDENTIAL,
    _USER_PASS,
    USER_PASS_MAX_LEN,
    redact_credentials,
)


def redact_exfiltration_urls(text: str) -> str:
    """Redact the credentials URLs carry (a ``user:pass@`` password, any scheme, and the
    value of a credential parameter such as ``?token=…`` or ``X-Amz-Signature=…``).

    Only the credential values are replaced; the rest of the URL stays, so a reader
    sees which link it was while the link no longer works. A plain URL (docs link,
    repo link) is left intact, matching the C041 concern (exfiltration of a secret
    via a link), not link-stripping in general.
    """
    if not text:
        return text
    return _URL_CREDENTIAL.sub(_REDACTED, text)


def redact_text(text: str) -> str:
    """One-shot egress redaction: credentials + exfiltration URLs.

    The single function wired at the ``OutboundMessage`` chokepoint. Idempotent
    and safe on empty/plain text (returns it unchanged).
    """
    if not text:
        return text
    return redact_credentials(text)


# In-flight credential prefix: matches when the buffer's TAIL looks like the
# beginning (possibly incomplete) of a credential — an opener keyword whose value
# has not yet been terminated by whitespace. Anchored at end-of-string (``\Z``) so
# it fires ONLY on the trailing edge, never mid-buffer. This is what lets prose
# stream promptly (no blunt fixed margin) while a streaming secret is withheld
# from its opener until a whitespace terminator proves the value is complete.
#
# WHY not a fixed char-margin: a margin either (a) kills streaming UX for the
# common sub-margin reply (nothing emits until stream end — the "black box" this
# design fights), or (b) if small, still leaks a long value (``Bearer <600-char
# jwt>``) once the window slides past the opener. Opener-awareness withholds
# exactly the credential-in-progress and nothing else. PEM blocks are NOT here —
# they carry an explicit ``-----BEGIN``/``-----END`` pair and are guarded by the
# unclosed-block rule in ``_stable_point`` (an in-flight ``-----BEGIN … \Z`` would
# never release once ``-----END`` arrived).
_INFLIGHT_CREDENTIAL: Final[re.Pattern] = re.compile(
    r"""(?ix)
    (?:
        \bBearer\b\s*\S*                                     # Bearer [partial value]
      | \b(?:aws_secret_access_key|secret_access_key|secret_key|api_key|apikey
            |access_token|auth_token|password|passwd)\b\s*[=:]?\s*\S*   # key[: =] [partial]
      | \b(?:https?|ftp)://\S*                               # url [partial — may carry embedded cred]
      | \b(?:AKIA|ASIA|AROA|AIDA)[0-9A-Z]*                   # aws access key [partial]
      | \b(?:ghp|gho|ghu|ghs|ghr|github_pat|xoxb|xoxp|xoxa|xoxr|sk-|pk-|AIza)[-_A-Za-z0-9]*  # prefixed token [partial]
      | \beyJ[A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_\-]*){0,2}       # jwt [partial]
    )\Z
    """,
)


# In-flight ``-u user:pass`` (the flag shape of core.credential_patterns): the flag,
# then an unclosed quote, a ``user:`` followed by an opening quote, or one token.
# Bounded, so it can only end at the buffer end when it starts within
# USER_PASS_MAX_LEN of it — the stream redactor searches only that tail.
_INFLIGHT_USER_PASS: Final[re.Pattern] = re.compile(
    r"""(?<![\w-])(?:-[A-Za-z]{0,16}+(?<=[uU])|-[uU]|--(?:proxy-)?user)"""
    # spacing mirrors core.credential_patterns._FLAG_WS, plus a lone trailing ``\``
    # (a chunk that ends inside a ``\``-newline continuation)
    r"""(?:[^\S\n]{1,16}+(?:\\\n?[^\S\n]{0,16}+)?|=|\\\n?[^\S\n]{0,16}+)?"""
    r"""(?:['"][^'"\n]{0,400}+"""
    r"""|['"]?[^\s:'"]{0,128}+['"]?:(?:'[^'\n]{0,256}+|"[^"\n]{0,256}+)"""
    r"""|\S{0,400}+)\Z"""
)


class StreamRedactor:
    """Rolling-buffer redactor for the append-only native streaming path.

    Slack ``append_stream`` appends each chunk permanently — there is no final
    overwrite — so once bytes are emitted they cannot be un-sent. The redactor's
    contract is therefore: **only ever emit a prefix that no future input can
    turn into (part of) a credential.** It accumulates the raw text and advances
    a "stable emit point" that is provably OUTSIDE every credential:

    * never cut inside the trailing non-whitespace token (a single-token secret,
      e.g. a long JWT, is withheld whole until a whitespace terminator arrives);
    * withhold a trailing run that looks like an in-flight credential opener
      (``Bearer …``, ``key = …``, ``https://…``, ``AKIA…``) until its value is
      terminated — opener-aware, so prose streams promptly with no fixed margin;
    * never emit into an unclosed ``-----BEGIN … PRIVATE KEY`` block;
    * pull the emit point back before any *completed* credential that would
      straddle it (keeps ``Bearer <token>`` / ``key = value`` together).

    Because the emit point never lands inside a credential, redacting the
    just-stabilised segment independently is identical to redacting the whole
    buffer and taking that slice — so a credential split across chunks is caught
    (it is whole in the buffer by the time the emit point passes it).

    Usage::

        r = StreamRedactor()
        out = r.feed(chunk)      # safe-to-append redacted delta (may be "")
        ...
        out += r.flush()         # release + redact everything still withheld
    """

    __slots__ = ("_raw", "_emitted")

    def __init__(self) -> None:
        self._raw: str = ""      # all raw text seen so far
        self._emitted: int = 0   # raw-space index up to which we have emitted

    def _stable_point(self) -> int:
        """Largest raw index that is provably outside every credential."""
        stable = len(self._raw)

        # Never cut inside the trailing non-whitespace token — withhold the whole
        # in-flight token (covers a single-token secret still streaming).
        m = re.search(r"\S+$", self._raw)
        if m and m.start() < stable:
            stable = m.start()

        # Withhold a trailing run that looks like the START of a credential whose
        # value is not yet whitespace-terminated (Bearer …, key = …, https://…,
        # AKIA…). Opener-aware: prose is emitted promptly; only the in-flight
        # credential is held. Matches at \Z, so it fires on the tail only.
        # From the emit point: an opener that starts in already-emitted text is
        # clamped to it anyway, and scanning the whole buffer every feed is quadratic.
        mo = _INFLIGHT_CREDENTIAL.search(self._raw, self._emitted)
        if mo and mo.start() < stable:
            stable = mo.start()
        mu = _INFLIGHT_USER_PASS.search(
            self._raw, max(self._emitted, len(self._raw) - USER_PASS_MAX_LEN))
        if mu and mu.start() < stable:
            stable = mu.start()

        # Don't emit into an unclosed PEM private-key block.
        last_begin = self._raw.rfind("-----BEGIN")
        last_end = self._raw.rfind("-----END")
        if last_begin > last_end and 0 <= last_begin < stable:
            stable = last_begin

        # Pull back before any COMPLETED credential straddling the emit point, so
        # a multi-token secret (Bearer X, key = value, URL) is never split with
        # its prefix emitted and its value withheld. Scan only the un-emitted
        # region (nothing straddled the previous stable point, by induction).
        base = self._emitted
        region = self._raw[base:]
        rel_stable = stable - base
        for pat in _CREDENTIAL_PATTERNS:
            for mt in pat.finditer(region):
                if mt.start() < rel_stable < mt.end():
                    rel_stable = mt.start()
        # _USER_PASS masks a group, so it is not in _CREDENTIAL_PATTERNS; it still must
        # never be split between the flag and the password. Its match is bounded, so
        # only a window around the emit point can straddle it (a whole-region scan per
        # feed made a long withheld token quadratic).
        lo = max(0, rel_stable - USER_PASS_MAX_LEN)
        for mt in _USER_PASS.finditer(region, lo, rel_stable + USER_PASS_MAX_LEN):
            if mt.start() < rel_stable < mt.end():
                rel_stable = mt.start()
        stable = base + rel_stable
        return max(self._emitted, stable)

    def feed(self, chunk: str) -> str:
        """Accept a raw streaming chunk; return the safe-to-emit redacted delta."""
        if not chunk:
            return ""
        self._raw += chunk
        stable = self._stable_point()
        if stable <= self._emitted:
            return ""
        segment = self._raw[self._emitted:stable]
        self._emitted = stable
        return redact_text(segment)

    def flush(self) -> str:
        """Release + redact everything still withheld at stream end. Idempotent."""
        if self._emitted >= len(self._raw):
            return ""
        segment = self._raw[self._emitted:]
        self._emitted = len(self._raw)
        return redact_text(segment)
