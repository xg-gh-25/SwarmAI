"""Guards as tests (design chat-transcript-single-source S4): mistakes that bit once
and are cheap to catch by reading the source.

- No ``input=`` pipe to a child that writes while it reads (``--stdin`` /
  ``--batch`` git plumbing): such a child and ``communicate()`` deadlock under
  memory pressure and the ``timeout=`` never fires (utils/stdin_file explains;
  2026-10-22 a code-intel reindex hung 300 s, a watcher 25 min). Use
  ``utils.stdin_file.run_with_stdin_file``. ``git hash-object --stdin`` reads to
  EOF before it writes, so it is allowed by name.
- CORS contract with the desktop webview (a cross-origin client): every request
  header ``desktop/src`` sets is in ``allow_headers`` (else the preflight is
  rejected: 2026-10-22, 134 failed conditional GETs on If-None-Match), and every
  response header it reads is in ``expose_headers`` (else the browser hides it and
  the read silently returns nothing).

Methodology: AST scan of the backend, regex scan of the frontend sources (tests
excluded), the CORS lists read from the ``CORSMiddleware`` call in main.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DESKTOP_SRC = BACKEND.parent / "desktop" / "src"

#: children that read all of stdin before writing anything: a pipe is safe
READ_ALL_FIRST = {"hash-object"}
#: header names a browser sends without a preflight entry (Fetch spec safelist)
SAFELISTED = {"accept", "accept-language", "content-language"}
#: response headers a browser always exposes (CORS-safelisted response headers)
EXPOSED_BY_DEFAULT = {"cache-control", "content-language", "content-length", "content-type",
                      "expires", "last-modified", "pragma"}


def _backend_files():
    skip = {".venv", "tests", "node_modules", "build", "dist", "__pycache__"}
    for p in BACKEND.rglob("*.py"):
        if not skip.intersection(p.relative_to(BACKEND).parts):
            yield p


def test_no_input_pipe_to_a_child_that_writes_while_it_reads():
    bad = []
    for path in _backend_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not any(k.arg == "input" for k in node.keywords):
                continue
            strs = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            streaming = any(s == "--stdin" or s.startswith("--batch") for s in strs)
            if streaming and not READ_ALL_FIRST & strs:
                bad.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert not bad, f"input= pipe to a --stdin/--batch child (use utils.stdin_file): {bad}"


def _cors_lists() -> tuple[set[str], set[str]]:
    tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "CORSMiddleware"):
            kw = {k.arg: k.value for k in node.keywords}

            def names(key):
                return {e.value.lower() for e in kw[key].elts if isinstance(e, ast.Constant)}
            return names("allow_headers"), names("expose_headers")
    raise AssertionError("CORSMiddleware not found in main.py")


def _frontend_sources():
    for p in DESKTOP_SRC.rglob("*"):
        if p.suffix in (".ts", ".tsx") and "__tests__" not in p.parts and ".test." not in p.name:
            yield p, p.read_text(encoding="utf-8")


SENT = [re.compile(r"""headers\[['"]([A-Za-z][\w-]*)['"]\]\s*=(?!=)"""),
        re.compile(r"""headers:\s*\{([^}]*)\}""")]
READ = re.compile(r"""\.headers\??\.?\[['"]([A-Za-z][\w-]*)['"]\]|\.headers\.get\(['"]([A-Za-z][\w-]*)['"]\)""")


def test_every_header_the_desktop_sends_is_allowed_and_every_one_it_reads_is_exposed():
    allow, expose = _cors_lists()
    sent: dict[str, str] = {}
    read: dict[str, str] = {}
    for path, text in _frontend_sources():
        rel = str(path.relative_to(DESKTOP_SRC))
        for m in SENT[0].finditer(text):
            sent[m.group(1).lower()] = rel
        for m in SENT[1].finditer(text):
            for key in re.findall(r"""['"]([A-Za-z][\w-]*)['"]\s*:""", m.group(1)):
                sent[key.lower()] = rel
        for m in READ.finditer(text):
            read[(m.group(1) or m.group(2)).lower()] = rel
    assert sent and read, "the scan found nothing: the patterns are stale"
    missing_allow = {h: f for h, f in sent.items() if h not in allow and h not in SAFELISTED}
    missing_expose = {h: f for h, f in read.items() if h not in expose and h not in EXPOSED_BY_DEFAULT}
    assert not missing_allow, f"request headers not in CORS allow_headers: {missing_allow}"
    assert not missing_expose, f"response headers not in CORS expose_headers: {missing_expose}"
