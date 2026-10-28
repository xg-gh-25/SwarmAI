"""Find a repo's documents, rank them in four tiers, summarize its API contracts.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.3. Deterministic and
explainable: every document records the signals that placed it, the same repo gives the
same order, and no document is ever cut short — a document is either inlined whole or
listed for the agent to read whole.

| tier | rule |
|---|---|
| 1 must read | root README; ``ARCHITECTURE*`` / ``DESIGN*``; docs the root README links to; each monorepo package's README |
| 2 design records | anything under a ``design`` / ``architecture`` / ``adr`` / ``rfc`` / ``decisions`` directory; ``NNNN-*.md`` ADRs (superseded / rejected / deprecated ones drop to tier 4) |
| 3 rules and conventions | CONTRIBUTING, SECURITY, CODEOWNERS, CHANGELOG (and CHANGES / HISTORY / NEWS); docs linked from two or more other docs |
| 4 the rest | by newest commit first |

Within tiers 1–3: more signals first, then path. A document matching several rules
keeps all its signals and takes the best tier.

API contracts (OpenAPI / Swagger / AsyncAPI, ``.proto``, GraphQL) are not documents to
read: each becomes a one-line-per-endpoint summary.
"""
from __future__ import annotations

import logging
import fnmatch
import json
import posixpath
import re
from pathlib import Path

from .repo_read import safe_read_text

logger = logging.getLogger(__name__)

DOC_EXTS = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})
_TXT_DOC_STEMS = frozenset({"readme", "changes", "changelog", "history", "news",
                            "contributing", "security", "authors", "notice"})
_DOC_DIRS = frozenset({"doc", "docs", "documentation"})
_NOT_DOC_DIRS = frozenset({"fixtures", "__fixtures__", "testdata", "test_data",
                           "__snapshots__", "snapshots"})
_DESIGN_DIRS = frozenset({"design", "designs", "architecture", "adr", "adrs", "rfc", "rfcs",
                          "decisions", "decision-records"})
_ADR_NAME_RE = re.compile(r"^\d{3,4}-.+\.(md|markdown|rst|adoc)$", re.IGNORECASE)
_ADR_DEAD_RE = re.compile(r"(?im)^\W*status\W*[:|]?\W*(superseded|rejected|deprecated)\b")
_T3_STEMS = {"contributing": "contributing", "security": "security",
             "changelog": "changelog", "changes": "changelog", "history": "changelog",
             "news": "changelog"}
_CODEOWNERS = frozenset({"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
                         ".gitlab/CODEOWNERS"})
_API_PATTERNS = (("openapi*.yaml", "openapi"), ("openapi*.yml", "openapi"),
                 ("openapi*.json", "openapi"), ("swagger*.yaml", "openapi"),
                 ("swagger*.yml", "openapi"), ("swagger*.json", "openapi"),
                 ("asyncapi*.yaml", "asyncapi"), ("asyncapi*.yml", "asyncapi"),
                 ("asyncapi*.json", "asyncapi"), ("*.proto", "proto"),
                 ("*.graphql", "graphql"), ("*.graphqls", "graphql"), ("*.gql", "graphql"))
T1_BUDGET_CHARS = 160_000   # ≈40K tokens inlined; a reading-size guard, not a quality test
_DOC_CAP = 5000             # documents considered per repo


def _stem(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return name.split(".", 1)[0].lower()


def _dirs(rel: str) -> list[str]:
    return [p.lower() for p in rel.split("/")[:-1]]


def _hidden_ok(rel: str) -> bool:
    """Dot-directories hold tooling, not docs — except ``.github`` (CONTRIBUTING,
    SECURITY, templates live there)."""
    return all(not p.startswith(".") or p.lower() == ".github" for p in rel.split("/")[:-1])


def is_doc(rel: str) -> bool:
    """A document this survey ranks (by extension; ``.txt`` only in a docs directory or
    under a well-known name, so ``requirements.txt`` is not a document)."""
    if not _hidden_ok(rel) or any(d in _NOT_DOC_DIRS for d in _dirs(rel)):
        return False
    ext = posixpath.splitext(rel)[1].lower()
    if ext not in DOC_EXTS:
        return rel in _CODEOWNERS
    if ext == ".txt":
        return bool(_DOC_DIRS & set(_dirs(rel))) or _stem(rel) in _TXT_DOC_STEMS
    return True


def api_format(rel: str) -> str | None:
    """The contract format of *rel* ("openapi", "asyncapi", "proto", "graphql") or None."""
    if not _hidden_ok(rel) or any(d in _NOT_DOC_DIRS for d in _dirs(rel)):
        return None
    name = rel.rsplit("/", 1)[-1].lower()
    for pat, fmt in _API_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return fmt
    return None


# ── links ────────────────────────────────────────────────────────────────────

_MD_TARGET_RE = re.compile(r"[ \t]*<?([^)\s>]{1,2000})")
_LINK_TEXT_MAX = 500   # how far back from "](" its "[" may be


def _md_link_targets(text: str) -> list[str]:
    """Targets of ``[text](target)`` links, in linear time.

    Scans for each ``](``, checks a ``[`` opens it within ``_LINK_TEXT_MAX`` characters
    on the same line, then reads the target. (A regex starting at every ``[`` is
    quadratic on a document with many unclosed brackets — untrusted input.)
    """
    out: list[str] = []
    start = 0
    while True:
        i = text.find("](", start)
        if i < 0:
            return out
        start = i + 2
        lo = max(0, i - _LINK_TEXT_MAX)
        nl = text.rfind("\n", lo, i)
        if text.rfind("[", nl + 1 if nl >= 0 else lo, i) < 0:
            continue
        m = _MD_TARGET_RE.match(text, start)
        if m:
            out.append(m.group(1))
_HREF_RE = re.compile(r"""href[ \t]*=[ \t]*["']([^"'\n]{1,2000})["']""", re.IGNORECASE)
_RST_LINK_RE = re.compile(r"`[^`<\n]{0,300}<([^>`\n]{1,2000})>`_")
_RST_DOC_RE = re.compile(r":doc:`(?:[^`<\n]{0,300}<)?([^>`\n]{1,2000})>?`")
_RST_INCLUDE_RE = re.compile(r"^\.\.\s+(?:include|literalinclude)::\s*(\S+)", re.MULTILINE)


def links(rel: str, text: str, docs: set[str]) -> set[str]:
    """The in-repo documents *text* (the document at *rel*) links to."""
    raw = (_md_link_targets(text) + _HREF_RE.findall(text) + _RST_LINK_RE.findall(text)
           + _RST_INCLUDE_RE.findall(text))
    doc_targets = _RST_DOC_RE.findall(text)
    here = posixpath.dirname(rel)
    out: set[str] = set()
    for target, is_doc_role in [(t, False) for t in raw] + [(t, True) for t in doc_targets]:
        target = target.strip()
        if not target or target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0]
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                  else posixpath.join(here, target))
        if path.startswith("..") or path == ".":
            continue
        candidates = [path]
        if is_doc_role or not posixpath.splitext(path)[1]:
            candidates += [path + ext for ext in (".rst", ".md")]
        for c in candidates:
            if c in docs and c != rel:
                out.add(c)
                break
    return out


# ── ranking ──────────────────────────────────────────────────────────────────

def _signals(rel: str, dead: bool, readme_links: set[str], inbound: int,
             package_readmes: set[str]) -> dict[int, list[str]]:
    """{tier: [signal, …]} for one document."""
    sig: dict[int, list[str]] = {1: [], 2: [], 3: []}
    stem = _stem(rel)
    if "/" not in rel and stem == "readme":
        sig[1].append("root-readme")
    if stem.startswith(("architecture", "design")):
        sig[1].append("architecture-doc")
    if rel in readme_links:
        sig[1].append("linked-from-readme")
    if rel in package_readmes:
        sig[1].append("package-readme")
    if not dead:
        if _DESIGN_DIRS & set(_dirs(rel)):
            sig[2].append("design-record-dir")
        if _ADR_NAME_RE.match(rel.rsplit("/", 1)[-1]):
            sig[2].append("numbered-adr")
    if stem in _T3_STEMS:
        sig[3].append(_T3_STEMS[stem])
    if rel in _CODEOWNERS:
        sig[3].append("codeowners")
    if inbound >= 2:
        sig[3].append(f"linked-by-{inbound}")
    if dead:
        sig.setdefault(4, []).append("superseded-or-rejected")
    return sig


def rank(root: Path, files: list[str], *, head: str | None = None,
         packages: list[dict] | None = None, commit_dates: dict[str, str] | None = None,
         budget: int = T1_BUDGET_CHARS) -> tuple[list[dict], list[dict]]:
    """``(docs, api_contracts)`` for a repo whose files are *files*.

    Each doc: ``{path, ref, kind, tier, signals, bytes, last_commit, included, reason}``
    plus ``text`` when inlined (tier 1 within *budget*, whole documents only).
    ``included`` means "inlined here"; every listed document can be read and recalled.
    """
    commit_dates = commit_dates or {}
    doc_paths = [f for f in files if is_doc(f)][:_DOC_CAP]
    doc_set = set(doc_paths)
    # One document's text in memory at a time: keep only what ranking needs
    # (size, links, the dead-ADR flag); tier-1 texts are read again for inlining.
    sizes: dict[str, int | None] = {}
    dead: dict[str, bool] = {}
    out_links: dict[str, set[str]] = {}
    for rel in doc_paths:
        text = safe_read_text(root, rel)
        sizes[rel] = len(text.encode("utf-8")) if text is not None else None
        dead[rel] = bool(text and _ADR_DEAD_RE.search(text[:4000]))
        if text:
            out_links[rel] = links(rel, text, doc_set)
    inbound: dict[str, int] = {}
    for src, targets in out_links.items():
        for t in targets:
            inbound[t] = inbound.get(t, 0) + 1
    readme = next((r for r in doc_paths if "/" not in r and _stem(r) == "readme"), None)
    readme_links = out_links.get(readme, set()) if readme else set()
    pkg_readmes = {r for r in doc_paths
                   for p in (packages or []) if p.get("root") not in (None, ".", "")
                   and posixpath.dirname(r) == p["root"] and _stem(r) == "readme"}
    short = (head or "")[:12]
    docs: list[dict] = []
    for rel in doc_paths:
        sig = _signals(rel, dead[rel], readme_links, inbound.get(rel, 0), pkg_readmes)
        tier = next((t for t in (1, 2, 3) if sig.get(t)), 4)
        signals = [s for t in (1, 2, 3, 4) for s in sig.get(t, [])]
        docs.append({
            "path": rel, "ref": f"{rel}@{short}" if short else rel,
            "kind": "codeowners" if rel in _CODEOWNERS else "doc",
            "tier": tier, "signals": signals,
            "bytes": sizes[rel],
            "last_commit": commit_dates.get(rel),
            "included": False,
            "reason": ("not UTF-8 text, a secret file, or larger than 2 MB"
                       if sizes[rel] is None else "listed: read it in full when writing the "
                       "section that needs it"),
        })

    ranked = sorted((d for d in docs if d["tier"] < 4),
                    key=lambda d: (d["tier"], -len(d["signals"]), d["path"]))
    rest = sorted((d for d in docs if d["tier"] == 4), key=lambda d: d["path"])
    rest.sort(key=lambda d: d["last_commit"] or "", reverse=True)  # stable: path breaks ties
    docs = ranked + rest
    _inline_tier1(docs, lambda rel: safe_read_text(root, rel), budget)
    apis: list[dict] = []
    for f in files:
        fmt = api_format(f)
        summary = _api_summary(root, f, fmt, short) if fmt else None
        if summary:
            apis.append(summary)
    return docs, apis


def _inline_tier1(docs: list[dict], read, budget: int) -> None:
    """Inline whole tier-1 documents in rank order while they fit *budget*.

    Each is scanned on its own (MemoryGuard): secrets are redacted in the inlined copy;
    a document that reads like a prompt injection is not inlined, only flagged — the
    rest of the survey is unaffected. A document that cannot fit the remaining budget
    is not scanned at all.
    """
    from core.memory_guard import MemoryGuard
    guard = MemoryGuard()
    used = 0
    for d in docs:
        if d["tier"] != 1 or d["bytes"] is None:
            continue
        text = read(d["path"])
        if text is None:
            continue
        if used + len(text) > budget:
            d["reason"] = "not inlined: over the tier-1 reading budget; read it in full"
            continue
        scan = guard.scan(text)
        flags = sorted({f"{f.category}:{f.pattern_name}" for f in scan.findings
                        if f.category != "invisible_chars"})
        if flags:
            d["flags"] = flags
        if scan.rejected:
            d["reason"] = "not inlined: flagged as possible prompt injection; treat it as data"
            continue
        body = scan.sanitized_content
        if used + len(body) > budget:
            d["reason"] = "not inlined: over the tier-1 reading budget; read it in full"
            continue
        used += len(body)
        d["included"] = True
        d["reason"] = "inlined whole"
        d["text"] = body


# ── CHANGELOG ────────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?")
_MD_HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")
_RST_UNDER_RE = re.compile(r"^([=\-~^*+#\"'`.:_])\1{2,}\s*$")


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(line index, level, text)] for Markdown and reStructuredText headings."""
    out: list[tuple[int, int, str]] = []
    rst_levels: list[str] = []
    for i, line in enumerate(lines):
        m = _MD_HEAD_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).rstrip().rstrip("#").rstrip()))
            continue
        if i + 1 < len(lines) and line.strip() and not _RST_UNDER_RE.match(line):
            u = _RST_UNDER_RE.match(lines[i + 1])
            if u and len(lines[i + 1].strip()) >= len(line.strip()):
                ch = u.group(1)
                if ch not in rst_levels:
                    rst_levels.append(ch)
                out.append((i, rst_levels.index(ch) + 1, line.strip()))
    return out


def changelog_sections(text: str, n: int = 3) -> list[dict]:
    """The newest *n* release sections, whole: ``[{heading, text}]``. A release is a
    heading that carries a version number; "Unreleased" is not a release."""
    lines = text.splitlines()
    heads = _headings(lines)
    out: list[dict] = []
    for k, (i, level, title) in enumerate(heads):
        if not _VERSION_RE.search(title) or "unreleased" in title.lower():
            continue
        end = next((j for j, lvl, _ in heads[k + 1:] if lvl <= level), len(lines))
        out.append({"heading": title, "text": "\n".join(lines[i:end]).strip()})
        if len(out) >= n:
            break
    return out


# ── API contracts ────────────────────────────────────────────────────────────

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
_PROTO_SERVICE_RE = re.compile(r"\bservice\s+(\w+)\s*\{")
_PROTO_RPC_RE = re.compile(r"\brpc\s+(\w+)\s*\(\s*(stream\s+)?([\w.]+)\s*\)\s*returns\s*"
                           r"\(\s*(stream\s+)?([\w.]+)\s*\)")
_GQL_ROOT_RE = re.compile(r"\b(?:extend\s+)?type\s+(Query|Mutation|Subscription)\b[^{]{0,500}\{([^}]{0,200000})\}")
_GQL_FIELD_RE = re.compile(r"^\s*(\w+)\s*[(:]", re.MULTILINE)


def _load_struct(rel: str, text: str):
    if rel.lower().endswith(".json"):
        return json.loads(text)
    from .repo_read import load_yaml
    return load_yaml(text)


def _api_summary(root: Path, rel: str, fmt: str, short: str) -> dict | None:
    text = safe_read_text(root, rel)
    if text is None:
        return None
    items: list[str] = []
    try:
        if fmt in ("openapi", "asyncapi"):
            data = _load_struct(rel, text)
            if not isinstance(data, dict):
                return None
            if fmt == "openapi":
                for path, ops in (data.get("paths") or {}).items():
                    for method in _HTTP_METHODS:
                        op = ops.get(method) if isinstance(ops, dict) else None
                        if isinstance(op, dict):
                            summary = op.get("summary") or op.get("operationId")
                            summary = " ".join(summary.split())[:200] if isinstance(summary, str) else ""
                            items.append(f"{method.upper()} {str(path)[:300]}"
                                         + (f" — {summary}" if summary else ""))
            else:
                channels = data.get("channels")
                items = [f"channel {str(name)[:300]}" for name in (channels if isinstance(channels, dict) else {})]
        elif fmt == "proto":
            spans = [(m.start(), m.group(1)) for m in _PROTO_SERVICE_RE.finditer(text)]
            for m in _PROTO_RPC_RE.finditer(text):
                svc = next((s for pos, s in reversed(spans) if pos < m.start()), "")
                items.append(f"{svc + '.' if svc else ''}{m.group(1)}"
                             f"({(m.group(2) or '')}{m.group(3)}) returns "
                             f"({(m.group(4) or '')}{m.group(5)})")
        elif fmt == "graphql":
            for m in _GQL_ROOT_RE.finditer(text):
                items += [f"{m.group(1)}.{f}" for f in _GQL_FIELD_RE.findall(m.group(2))]
    except Exception as exc:  # noqa: BLE001 — a malformed contract is listed with no items
        logger.debug("survey: contract %s unparsable, listed with no items: %s", rel, exc)
        items = []
    return {"path": rel, "ref": f"{rel}@{short}" if short else rel, "format": fmt,
            "items": items}
