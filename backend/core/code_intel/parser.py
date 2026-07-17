"""
Tree-sitter AST parser with 3-layer name resolution.

Adapted from code-review-graph (14.9K stars).
Falls back to regex when tree-sitter is unavailable.

Layer 1: Per-file resolution (import_map + defined_names)
Layer 2: Cross-file batch resolution (resolve bare targets via global lookup)
Layer 3: Not in Phase 1 (future: Jedi-based for Python)
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# ── Language Configuration ──────────────────────────────────────────────

LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".php": "php",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
}

DEFINITION_TYPES = {
    "python": ["function_definition", "class_definition"],
    "typescript": [
        "function_declaration", "class_declaration",
        "method_definition", "arrow_function",
    ],
    # JavaScript shares TypeScript's tree-sitter node types (verified against the
    # live 'javascript' grammar, run_2e46f2af). LANGUAGE_MAP routes .js/.jsx here;
    # without this key _extract_from_tree got [] for every .js/.jsx file once the
    # AST path went live — a silent regression vs the regex fallback, which DOES
    # extract JS defs. Mirror the typescript set.
    "javascript": [
        "function_declaration", "class_declaration",
        "method_definition", "arrow_function",
    ],
    "java": [
        "method_declaration", "class_declaration",
        "interface_declaration", "enum_declaration",
    ],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
    "rust": ["function_item", "struct_item", "impl_item", "trait_item"],
    "ruby": ["method", "class", "module"],
    "csharp": ["method_declaration", "class_declaration", "interface_declaration"],
    "kotlin": ["function_declaration", "class_declaration", "object_declaration"],
    "php": ["function_definition", "class_declaration", "method_declaration"],
    "swift": ["function_declaration", "class_declaration", "protocol_declaration"],
    "c": ["function_definition", "struct_specifier"],
    "cpp": ["function_definition", "class_specifier", "struct_specifier"],
}

CALL_TYPES = {
    "python": ["call"],
    "typescript": ["call_expression", "new_expression"],
    "javascript": ["call_expression", "new_expression"],
    "java": ["method_invocation", "object_creation_expression"],
    "go": ["call_expression"],
    "rust": ["call_expression"],
    "ruby": ["call"],
    "csharp": ["invocation_expression", "object_creation_expression"],
    "kotlin": ["call_expression"],
    "php": ["function_call_expression", "method_call_expression"],
    "swift": ["call_expression"],
    "c": ["call_expression"],
    "cpp": ["call_expression"],
}

# NOTE: IMPORT_TYPES not used in Phase 1 — imports extracted via regex in
# _build_file_scope_regex(). Tree-sitter import node walking deferred to Phase 2.

# Directories to skip (check each path component, not fnmatch — CRG bug #91 fix)
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "egg-info", ".next", ".nuxt",
}

QUALIFIED_SEPARATOR = "::"
_MAX_WORKERS = 4
_SERIAL_THRESHOLD = 8

# Coverage-correctness (Run AB): a repo with more source-like files than this is
# parsed only up to the cap and the run is flagged `partial` with an explicit
# repo-level coverage-hole — NEVER silently truncated to look `complete`. This is
# a disaster-recovery ceiling for pathological repos, not a normal-path control
# (O030): the honest signal is "we did not read all of it", never a silent []/subset.
_MAX_REPO_FILES = 50_000

# Extensions we deliberately do NOT treat as source (docs/data/assets). A file
# with one of these extensions is NOT a coverage hole — it is legitimately
# out-of-scope. Anything NOT in LANGUAGE_MAP and NOT here is an UNKNOWN extension
# → recorded as a coverage-hole so an unsupported language (COBOL .cbl, etc.) can
# never be silently invisible on a banking legacy codebase (Gate-1 Check-1/A1).
_NON_SOURCE_EXTENSIONS = {
    ".md", ".markdown", ".rst", ".txt", ".text",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".lock", ".sum", ".mod",
    ".csv", ".tsv", ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".o", ".a", ".class", ".jar",
    ".env", ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",  # shell — no AST support
    ".sql", ".graphql", ".proto", ".thrift",
    ".log", ".map", ".min", ".d.ts",
}

# Cap on how many distinct UNKNOWN-extension holes to emit PER extension, so a
# repo with 10k COBOL files produces a bounded, readable ledger (one aggregate +
# a few examples) rather than 10k rows that bury the real signal (Gate-1 Check-3
# anti-noise). The aggregate row always states the TRUE total count.
_MAX_UNKNOWN_EXT_EXAMPLES = 5

# ── Data Types ──────────────────────────────────────────────────────────


@dataclass
class CodeNode:
    id: str
    file_path: str
    node_type: str
    name: str
    line_start: int
    line_end: int
    language: str
    is_export: bool = True
    is_entry_point: bool = False
    sha256: str | None = None


@dataclass
class CodeEdge:
    source_id: str
    target_id: str
    edge_type: str
    confidence: float = 1.0
    line_number: int | None = None


@dataclass
class ParseResult:
    nodes: list[CodeNode] = field(default_factory=list)
    edges: list[CodeEdge] = field(default_factory=list)
    language: str = "unknown"
    file_path: str = ""


@dataclass
class ParseRepoResult:
    """Coverage-correctness return (Run AB) for ``parse_repo_with_coverage``.

    - ``results``        — the list[ParseResult] (identical to what the legacy
                           ``parse_repo`` returns, so callers can use it the same way)
    - ``coverage_holes`` — every file/repo that was SEEN but NOT turned into nodes,
                           each ``{ref, kind, reason}`` where kind ∈ {file, repo}.
                           This is the "never silently under-report" ledger: an
                           unknown-extension source file, an unreadable/parse-failed
                           file, or a repo-level fact (missing/empty/oversized).
    - ``status``         — "complete" (every in-scope source file was parsed) or
                           "partial" (repo missing/empty, oversized-capped, or any
                           parse FAILURE occurred). A banking coverage guarantee is
                           only honest when a partial run says so.

    Design (Gate-1 review): a file is a hole ONLY when it was in-scope-and-failed
    or is an unknown source-like extension — NEVER when a clean parse legitimately
    yields 0 nodes (an ``__init__.py`` / comment-only file is correctly empty, not
    a hole — flagging those is noise that buries the real signal).
    """
    results: list[ParseResult] = field(default_factory=list)
    coverage_holes: list[dict] = field(default_factory=list)
    status: str = "complete"


# ── Tree-sitter (optional) ─────────────────────────────────────────────

_ts_available = False
# Parser cache is THREAD-LOCAL: tree-sitter native Parser objects are NOT
# thread-safe (PyO3 marks them `unsendable` — sharing one across worker threads
# hard-PANICS the interpreter, per IMPROVEMENT.md "one parser per worker"). The
# parallel parse path (ThreadPoolExecutor) therefore must never share a cached
# parser across threads. A panicking parse is SILENT coverage loss (the file
# vanishes with no hole recorded) — so thread-safety here is part of the
# coverage-correctness guarantee, not just a perf detail. (Run AB fix.)
_parser_cache_tls = threading.local()

try:
    import tree_sitter
    import tree_sitter_language_pack as tslp
    _ts_available = True
except ImportError:
    logger.info("tree-sitter/tree-sitter-language-pack not available, using regex fallback")


def _get_cached_parser(language: str):
    """Get or create a tree-sitter parser for a language (per-thread instance)."""
    if not _ts_available:
        return None
    cache = getattr(_parser_cache_tls, "cache", None)
    if cache is None:
        cache = {}
        _parser_cache_tls.cache = cache
    if language in cache:
        return cache[language]
    try:
        # Standard tree-sitter 0.25 API: construct a Parser from a Language.
        # NOT tslp.get_parser() — that returns a bundled OLD-ABI builtins.Parser
        # whose .parse() rejects bytes ('bytes object is not an instance of str'),
        # which silently forced the whole indexing path onto the regex fallback
        # (run_2e46f2af). tree_sitter.Parser(get_language(...)) parses bytes and
        # exposes root_node as a property, as _tree_sitter_live / _extract_from_tree
        # expect. Constructed per-thread here (the TLS cache above) because the
        # native Parser is PyO3-`unsendable` — see the cache header comment.
        parser = tree_sitter.Parser(tslp.get_language(language))
        cache[language] = parser
        return parser
    except Exception as e:
        logger.debug(f"No tree-sitter parser for {language}: {e}")
        return None


# Gate-2 F4 (run AB): the AST path is only trustworthy if the installed binding
# actually parses. Some environments/binding versions reject parser.parse()'s
# argument for every file → a silent universal regex fallback. We probe ONCE per
# language (cheap, cached) and gate the tree-sitter branch on it, so a dead binding
# is surfaced as a repo-level fidelity hole instead of masquerading as full coverage.
_ts_live_cache: dict[str, bool] = {}
_ts_live_lock = threading.Lock()

_TS_PROBE_SRC = {
    "python": b"def _p():\n    return 1\n",
}
_TS_DEFAULT_PROBE = b"def _p(){}\n"


def _tree_has_error(tree) -> bool:
    """True if the parsed tree's root node reports a syntax error. Tolerant of
    binding differences (has_error may be attr or absent) — returns False when it
    can't tell (never invent a failure)."""
    try:
        root = tree.root_node
        return bool(getattr(root, "has_error", False))
    except Exception:
        return False


def _tree_sitter_live(language: str) -> bool:
    """Probe (once per language, cached) whether tree-sitter can actually parse a
    trivial snippet AND expose a usable root node. False → the AST path is dead in
    this environment; callers fall back to regex and the repo-level fidelity hole
    is emitted. Fail-safe: any probe error → not live (regex, honest signal)."""
    with _ts_live_lock:
        if language in _ts_live_cache:
            return _ts_live_cache[language]
    live = False
    try:
        parser = _get_cached_parser(language)
        if parser is not None:
            src = _TS_PROBE_SRC.get(language, _TS_DEFAULT_PROBE)
            tree = parser.parse(src)
            root = tree.root_node          # must not raise / must be a node
            live = getattr(root, "type", None) is not None and root.child_count >= 0
    except Exception as e:
        logger.debug(f"tree-sitter liveness probe failed for {language}: {e}")
        live = False
    with _ts_live_lock:
        _ts_live_cache[language] = live
    return live


# ── Utility ─────────────────────────────────────────────────────────────

def _sanitize_name(s: str, max_len: int = 256) -> str:
    """Strip control characters, cap length. Prevents adversarial identifiers."""
    cleaned = "".join(ch for ch in s if ch in ("\t", "\n") or ord(ch) >= 0x20)
    return cleaned[:max_len]


def _qualify(name: str, file_path: str, enclosing_class: str | None = None) -> str:
    """CRG pattern: 'relative/path.py::ClassName.method_name'"""
    if enclosing_class:
        return f"{file_path}{QUALIFIED_SEPARATOR}{enclosing_class}.{name}"
    return f"{file_path}{QUALIFIED_SEPARATOR}{name}"


def _file_hash(path: Path) -> str:
    """SHA-256 of file content for incremental skip."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _should_skip_dir(component: str) -> bool:
    """Check if a directory component should be skipped."""
    return component in SKIP_DIRS or component.endswith(".egg-info")


def _is_entry_point(name: str, file_path: str, language: str) -> bool:
    """Detect entry points per language."""
    fname = Path(file_path).name

    if language == "python":
        if name.startswith("test_") or fname.startswith("test_"):
            return True
        if fname == "conftest.py" or fname == "__main__.py":
            return True
        if name in ("main", "cli", "app"):
            return True
    elif language == "typescript":
        if ".test." in fname or ".spec." in fname:
            return True
    elif language == "java":
        if name == "main":
            return True
    elif language == "go":
        if name in ("main", "init") or name.startswith("Test") or name.startswith("Benchmark"):
            return True
    return False


def _is_exported(name: str, language: str, node_text: str = "") -> bool:
    """Detect if a symbol is exported (public)."""
    if language == "python":
        return not name.startswith("_")
    elif language in ("typescript", "javascript"):
        return "export" in node_text
    elif language == "java":
        return "public" in node_text or "protected" in node_text
    elif language == "go":
        return name[0].isupper() if name else False
    return True


# ── Layer 1: Per-file Resolution ────────────────────────────────────────

def _build_file_scope_regex(content: str, language: str) -> tuple[dict[str, str], set[str]]:
    """
    Pre-scan file content with regex.
    Returns: (import_map, defined_names)
    """
    import_map: dict[str, str] = {}
    defined_names: set[str] = set()

    if language == "python":
        # from X.Y import A, B
        for m in re.finditer(r'from\s+([\w.]+)\s+import\s+(.+?)(?:\n|$)', content):
            module = m.group(1).replace(".", "/")
            for name in re.split(r'\s*,\s*', m.group(2)):
                name = name.strip().split(" as ")[-1].strip()
                if name and name.isidentifier():
                    import_map[name] = module
        # import X
        for m in re.finditer(r'^import\s+([\w.]+)', content, re.MULTILINE):
            parts = m.group(1).split(".")
            import_map[parts[-1]] = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]

        # Definitions
        for m in re.finditer(r'^(?:def|class)\s+(\w+)', content, re.MULTILINE):
            defined_names.add(m.group(1))

    elif language in ("typescript", "javascript"):
        for m in re.finditer(r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
            module = m.group(2)
            for name in re.split(r'\s*,\s*', m.group(1)):
                name = name.strip().split(" as ")[-1].strip()
                if name and name.isidentifier():
                    import_map[name] = module
        for m in re.finditer(r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", content):
            import_map[m.group(1)] = m.group(2)
        for m in re.finditer(r'(?:function|class|const|let|var)\s+(\w+)', content):
            defined_names.add(m.group(1))

    elif language == "java":
        for m in re.finditer(r'import\s+([\w.]+);', content):
            parts = m.group(1).split(".")
            import_map[parts[-1]] = "/".join(parts[:-1])
        for m in re.finditer(r'(?:class|interface|enum)\s+(\w+)', content):
            defined_names.add(m.group(1))
        for m in re.finditer(
            r'(?:public|private|protected|static|\s)+\s+\w+\s+(\w+)\s*\(', content
        ):
            defined_names.add(m.group(1))

    elif language == "go":
        for m in re.finditer(r'import\s+"([^"]+)"', content):
            pkg = m.group(1).split("/")[-1]
            import_map[pkg] = m.group(1)
        for m in re.finditer(r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)', content):
            defined_names.add(m.group(1))
        for m in re.finditer(r'type\s+(\w+)', content):
            defined_names.add(m.group(1))

    return import_map, defined_names


def _resolve_call_target(
    call_name: str, file_path: str, import_map: dict, defined_names: set,
    enclosing_class: str | None = None,
) -> str:
    """Priority: local definition > imported symbol > leave bare."""
    if call_name in defined_names:
        return _qualify(call_name, file_path, enclosing_class=None)
    if call_name in import_map:
        source = import_map[call_name]
        return f"{source}{QUALIFIED_SEPARATOR}{call_name}"
    return call_name  # bare — Layer 2 will try


# ── Tree-sitter AST Extraction ──────────────────────────────────────────

def _extract_from_tree(tree, path_str: str, language: str,
                       import_map: dict, defined_names: set) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Walk tree-sitter AST and extract nodes + edges."""
    nodes = []
    edges = []
    root = tree.root_node
    def_types = set(DEFINITION_TYPES.get(language, []))
    call_types = set(CALL_TYPES.get(language, []))

    def _walk(node, enclosing_func=None, enclosing_class=None):
        ntype = node.type

        # Definitions
        if ntype in def_types:
            name = _get_name(node, language)
            if name:
                name = _sanitize_name(name)
                is_class = "class" in ntype or "interface" in ntype or "enum" in ntype
                node_type = "class" if is_class else "method" if enclosing_class else "function"
                qn = _qualify(name, path_str, enclosing_class)
                code_node = CodeNode(
                    id=qn, file_path=path_str, node_type=node_type,
                    name=name, line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1, language=language,
                    is_export=_is_exported(name, language, node.text.decode("utf-8", errors="replace")[:200] if node.text else ""),
                    is_entry_point=_is_entry_point(name, path_str, language),
                )
                nodes.append(code_node)

                new_class = name if is_class else enclosing_class
                new_func = qn if not is_class else enclosing_func
                for child in node.children:
                    _walk(child, enclosing_func=new_func, enclosing_class=new_class)
                return

        # Calls (only inside functions/methods → module-level calls produce no edges)
        if ntype in call_types and enclosing_func:
            call_name = _get_call_name(node, language)
            if call_name:
                call_name = _sanitize_name(call_name)
                target = _resolve_call_target(call_name, path_str, import_map,
                                             defined_names, enclosing_class)
                edges.append(CodeEdge(
                    source_id=enclosing_func, target_id=target,
                    edge_type="calls",
                    confidence=1.0 if QUALIFIED_SEPARATOR in target else 0.5,
                    line_number=node.start_point[0] + 1,
                ))

        for child in node.children:
            _walk(child, enclosing_func, enclosing_class)

    _walk(root)
    return nodes, edges


def _get_name(node, language: str) -> str | None:
    """Extract the name identifier from a definition node."""
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return None


def _get_call_name(node, language: str) -> str | None:
    """Extract the function name from a call node."""
    func_node = node.children[0] if node.children else None
    if not func_node:
        return None

    # self.method() → extract "method"
    if func_node.type in ("attribute", "member_expression"):
        parts = []
        for child in func_node.children:
            if child.type in ("identifier", "property_identifier"):
                parts.append(child.text.decode("utf-8", errors="replace"))
        if len(parts) >= 2 and parts[0] in ("self", "this"):
            return parts[-1]
        return parts[-1] if parts else None

    if func_node.type in ("identifier", "property_identifier"):
        return func_node.text.decode("utf-8", errors="replace")

    return None


# ── Regex Fallback ──────────────────────────────────────────────────────

# Patterns for extracting definitions via regex (language-agnostic)
# Language-specific regex patterns — keyed by language to avoid cross-contamination
# (e.g., Java method pattern matching Python builtins like isinstance/len/bool)
_REGEX_DEF_PATTERNS_BY_LANG: dict[str, list[re.Pattern]] = {
    "python": [
        re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
        re.compile(r'^class\s+(\w+)', re.MULTILINE),
    ],
    "typescript": [
        re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', re.MULTILINE),
        re.compile(r'^(?:export\s+)?class\s+(\w+)', re.MULTILINE),
    ],
    "javascript": [
        re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', re.MULTILINE),
        re.compile(r'^(?:export\s+)?class\s+(\w+)', re.MULTILINE),
    ],
    "java": [
        re.compile(r'^\s*(?:public|private|protected)\s+(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(', re.MULTILINE),
        re.compile(r'^(?:public\s+)?(?:abstract\s+)?class\s+(\w+)', re.MULTILINE),
        re.compile(r'^(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
    ],
    "go": [
        re.compile(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', re.MULTILINE),
        re.compile(r'^type\s+(\w+)\s+(?:struct|interface)', re.MULTILINE),
    ],
}

# Fallback for unknown languages — conservative, avoids Java-style broad match
_REGEX_DEF_PATTERNS_GENERIC = [
    re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
    re.compile(r'^class\s+(\w+)', re.MULTILINE),
    re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', re.MULTILINE),
]

_REGEX_CALL_PATTERN = re.compile(r'(\w+)\s*\(', re.MULTILINE)

# Python/JS builtins that regex might mistake for definitions
_BUILTIN_NAMES = frozenset({
    "isinstance", "len", "bool", "int", "str", "float", "list", "dict", "set",
    "tuple", "type", "print", "range", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "hasattr", "getattr", "setattr", "delattr",
    "super", "property", "staticmethod", "classmethod", "abstractmethod",
    "open", "close", "read", "write", "append", "extend", "update", "pop",
    "get", "keys", "values", "items", "format", "join", "split", "strip",
    "replace", "startswith", "endswith", "lower", "upper", "encode", "decode",
    "any", "all", "min", "max", "sum", "abs", "round", "id", "hash", "repr",
    "callable", "next", "iter", "input", "exit", "quit",
    # JS/TS builtins
    "require", "console", "setTimeout", "setInterval", "clearTimeout",
    "clearInterval", "parseInt", "parseFloat", "isNaN", "Array", "Object",
    "String", "Number", "Boolean", "Promise", "Symbol", "Error",
})


def _regex_fallback(path: Path, repo_root: Path) -> ParseResult:
    """Language-agnostic extraction via regex. Confidence: 0.6."""
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return ParseResult()

    content = raw_bytes.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw_bytes).hexdigest()  # compute once, not per-node

    rel_path = str(path.relative_to(repo_root))
    lang = LANGUAGE_MAP.get(path.suffix, "unknown")
    nodes = []
    edges = []
    defined_names = set()

    # Use language-specific patterns to avoid cross-contamination
    patterns = _REGEX_DEF_PATTERNS_BY_LANG.get(lang, _REGEX_DEF_PATTERNS_GENERIC)

    # Extract definitions
    for pattern in patterns:
        for m in pattern.finditer(content):
            name = _sanitize_name(m.group(1))
            if not name or name in defined_names or name in _BUILTIN_NAMES:
                continue
            defined_names.add(name)

            line = content[:m.start()].count("\n") + 1
            is_class = "class" in pattern.pattern
            node_type = "class" if is_class else "function"
            qn = _qualify(name, rel_path)

            nodes.append(CodeNode(
                id=qn, file_path=rel_path, node_type=node_type,
                name=name, line_start=line, line_end=line + 5,  # regex can't know real end
                language=lang,
                is_export=_is_exported(name, lang),
                is_entry_point=_is_entry_point(name, rel_path, lang),
                sha256=sha,
            ))

    # Extract calls (with lower confidence)
    import_map, _ = _build_file_scope_regex(content, lang)

    # Find function bodies and extract calls within them
    func_bodies = list(re.finditer(r'^(?:async\s+)?(?:def|function|func)\s+(\w+)\s*\([^)]*\)\s*[:{]', content, re.MULTILINE))
    for func_match in func_bodies:
        func_name = func_match.group(1)
        func_qn = _qualify(func_name, rel_path)
        # Scan next ~50 lines for calls (P1-5: use actual line count, not char offset)
        start_pos = func_match.end()
        lines_after = content[start_pos:].split('\n', 51)  # 50 lines + remainder
        body = '\n'.join(lines_after[:50])
        for call_match in _REGEX_CALL_PATTERN.finditer(body):
            call_name = call_match.group(1)
            if (call_name in _BUILTIN_NAMES
                    or call_name in ("if", "for", "while", "return", "raise",
                                     "yield", "with", "assert", "except", "import", "from",
                                     "class", "def", "func", "function", "var", "let", "const")):
                continue
            target = _resolve_call_target(call_name, rel_path, import_map, defined_names)
            line = content[:start_pos + call_match.start()].count("\n") + 1
            edges.append(CodeEdge(
                source_id=func_qn, target_id=target,
                edge_type="calls", confidence=0.6,
                line_number=line,
            ))

    return ParseResult(nodes=nodes, edges=edges, language=lang, file_path=rel_path)


# ── Main API ────────────────────────────────────────────────────────────

def parse_file(path: Path, repo_root: Path) -> ParseResult:
    """
    Full pipeline per file (back-compat: returns bare ParseResult).

    1. _build_file_scope() → import_map + defined_names
    2. _extract_from_tree() → walk AST, emit nodes + edges (Layer 1 resolution)
    Falls back to regex if language unsupported or tree-sitter fails.

    NOTE: this is the legacy entry point kept UNCHANGED for existing callers. The
    coverage-aware variant ``parse_file_with_status`` carries the failure signal
    used by ``parse_repo_with_coverage``; this wrapper discards it.
    """
    result, _status = parse_file_with_status(path, repo_root)
    return result


def parse_file_with_status(path: Path, repo_root: Path) -> tuple[ParseResult, str]:
    """Parse one file and report an EXPLICIT status (Run AB coverage-correctness).

    Returns ``(ParseResult, status)`` where status ∈:
      - "ok"          — parsed cleanly (may have 0 nodes — a comment-only / re-export
                        file is legitimately empty, NOT a failure).
      - "unreadable"  — the file could not be read (OSError) → a coverage HOLE.
      - "failed"      — read OK but the language parse threw AND regex fallback also
                        produced nothing usable → a coverage HOLE (parse FAILURE).

    The distinction is the whole point of Gate-1 Check-3: a hole is a parse
    FAILURE, never a clean-but-empty result.
    """
    lang = LANGUAGE_MAP.get(path.suffix)
    if not lang:
        # Caller (parse_repo_with_coverage) decides unknown-ext handling; the
        # legacy path returns an empty result. Not reachable via parse_repo_with_coverage
        # (it filters unknown extensions before calling), kept for parse_file back-compat.
        return ParseResult(), "ok"

    rel_path = str(path.relative_to(repo_root))

    # Read file once — derive both bytes and text from same buffer
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        logger.debug(f"Unreadable file {path}: {e}")
        return ParseResult(file_path=rel_path, language=lang), "unreadable"

    content = raw_bytes.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw_bytes).hexdigest()

    # Try tree-sitter first — but ONLY when the AST path is actually LIVE. Gate-2 F4
    # (run AB adversarial, CRITICAL): in some environments the tree-sitter binding
    # rejects parser.parse()'s argument for EVERY file → a silent universal fallback
    # to the low-fidelity regex path. If we treated that as a normal parse we'd report
    # "complete" coverage while running at fallback fidelity for the whole repo — a
    # false-confidence the banking guarantee forbids. So: only enter the tree-sitter
    # branch when a liveness probe confirms the AST path works; the repo-level
    # fidelity signal is emitted once by parse_repo_with_coverage (not per file).
    parser = _get_cached_parser(lang) if _tree_sitter_live(lang) else None
    if parser:
        try:
            tree = parser.parse(raw_bytes)
            import_map, defined_names = _build_file_scope_regex(content, lang)
            nodes, edges = _extract_from_tree(tree, rel_path, lang, import_map, defined_names)
            for n in nodes:
                n.sha256 = sha
            # Gate-2 F3: "failed" is now REACHABLE. A LIVE tree-sitter that parses this
            # file into an error-riddled tree (root has_error) on a non-empty file =
            # a genuine parse failure → coverage hole, not silent "ok".
            if _tree_has_error(tree) and content.strip():
                logger.debug(f"Tree-sitter parse has errors on {path} → failed")
                return ParseResult(file_path=rel_path, language=lang), "failed"
            # Clean parse — "ok" EVEN IF 0 nodes (comment-only/__init__.py is
            # legitimately empty; Gate-1 Check-3: absence of nodes ≠ failure).
            return ParseResult(nodes=nodes, edges=edges, language=lang, file_path=rel_path), "ok"
        except Exception as e:
            # A LIVE tree-sitter that throws on THIS file (not the dead-binding case,
            # which _tree_sitter_live already filtered) is a genuine per-file failure.
            logger.debug(f"Tree-sitter threw on {path}: {e} → failed")
            fallback = _regex_fallback(path, repo_root)
            if not fallback.nodes and not fallback.edges and content.strip():
                return ParseResult(file_path=rel_path, language=lang), "failed"
            return fallback, "ok"

    # Regex fallback (best-effort) — reached when tree-sitter is not live for this
    # language. Absence of nodes here is NOT a per-file failure (regex legitimately
    # finds nothing in comment-only/config files); the DEGRADED-FIDELITY fact is
    # surfaced once at the repo level by parse_repo_with_coverage's liveness hole.
    return _regex_fallback(path, repo_root), "ok"


def parse_repo(repo_root: Path, languages: list[str] | None = None) -> list[ParseResult]:
    """
    Walk repo, parse all files matching LANGUAGE_MAP. Returns list[ParseResult].

    BACK-COMPAT: this is the legacy list-returning API that 3 callers
    (routers/code_intel.py, core/ddd_bindings.py, jobs/handlers/code_intel_reindex.py)
    and test_parser.py depend on (they destructure the return as a bare list). It is
    a thin wrapper over ``parse_repo_with_coverage`` that discards the coverage
    ledger. Any caller that needs the "never silently under-report" coverage-holes
    (the reindex path that writes code-intel.json) must call
    ``parse_repo_with_coverage`` directly. DO NOT change this signature.
    """
    return parse_repo_with_coverage(repo_root, languages).results


def parse_repo_with_coverage(
    repo_root: Path, languages: list[str] | None = None
) -> ParseRepoResult:
    """
    Walk repo, parse all source files, and account for EVERY file seen (Run AB).

    Skips (out of scope, not holes): node_modules, .git, __pycache__, venv, etc.
    Skips (out of scope, not holes): known non-source extensions (docs/data/assets,
      ``_NON_SOURCE_EXTENSIONS``).
    Coverage HOLES (seen but not understood — recorded, never silently dropped):
      - A1: an UNKNOWN extension (not in LANGUAGE_MAP, not a known non-source ext) —
            e.g. COBOL .cbl on a legacy banking repo. Bounded per-extension so a
            10k-file repo yields a readable ledger, not 10k rows.
      - A1: an in-scope source file that is UNREADABLE (OSError).
      - A3: an in-scope source file whose parse FAILED (tree-sitter threw AND regex
            found nothing in a non-empty file). A clean parse yielding 0 nodes
            (comment-only, re-export __init__.py) is NOT a hole.
    Repo-level signals (A2): non-dir / empty / oversized → explicit repo-kind hole
      + status="partial". Never a silent [].

    Parallel: ThreadPoolExecutor (PyInstaller fork safety). Max 4 workers, <8 serial.
    """
    coverage_holes: list[dict] = []
    status = "complete"

    # ── A2: repo-level sentinels (explicit signal, never silent []) ──
    if not repo_root.is_dir():
        coverage_holes.append({
            "ref": str(repo_root), "kind": "repo",
            "reason": "repo path does not exist or is not a directory — nothing parsed",
        })
        return ParseRepoResult(results=[], coverage_holes=coverage_holes, status="partial")

    lang_filter = set(languages) if languages else None
    files_to_parse: list[Path] = []
    unknown_ext_counts: dict[str, int] = {}
    unknown_ext_examples: dict[str, list[str]] = {}

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        # Skip dirs by checking each path component (out of scope, not a hole)
        if any(_should_skip_dir(part) for part in path.relative_to(repo_root).parts):
            continue
        suffix = path.suffix
        if suffix in LANGUAGE_MAP:
            if lang_filter and LANGUAGE_MAP[suffix] not in lang_filter:
                continue
            files_to_parse.append(path)
        elif suffix in _NON_SOURCE_EXTENSIONS or not suffix:
            # Known non-source (docs/data/assets) or extensionless — out of scope,
            # legitimately not a hole.
            continue
        else:
            # A1: UNKNOWN extension — a source-like file in a language we can't
            # parse. Never silently invisible; record bounded per-extension.
            rel = str(path.relative_to(repo_root))
            unknown_ext_counts[suffix] = unknown_ext_counts.get(suffix, 0) + 1
            ex = unknown_ext_examples.setdefault(suffix, [])
            if len(ex) < _MAX_UNKNOWN_EXT_EXAMPLES:
                ex.append(rel)

    # Emit bounded unknown-extension holes (one row per example, capped; the reason
    # on every row carries the TRUE total for that extension so the count is honest).
    for suffix, total in sorted(unknown_ext_counts.items()):
        status = "partial"  # an unparsed source-like language = incomplete coverage
        examples = unknown_ext_examples[suffix]
        for ex in examples:
            coverage_holes.append({
                "ref": ex, "kind": "file",
                "reason": (f"unsupported extension '{suffix}' — no AST parser "
                           f"({total} file(s) with this extension; showing up to "
                           f"{_MAX_UNKNOWN_EXT_EXAMPLES}). Language not covered by the graph."),
            })

    # ── A2: empty repo (no in-scope source) → explicit signal ──
    if not files_to_parse:
        if not any(h["kind"] == "repo" for h in coverage_holes):
            coverage_holes.append({
                "ref": str(repo_root), "kind": "repo",
                "reason": "no in-scope source files found (empty repo or all files "
                          "out of scope) — nothing to parse",
            })
        return ParseRepoResult(results=[], coverage_holes=coverage_holes, status="partial")

    # ── Gate-2 F4: fidelity signal. If tree-sitter is NOT live for the languages
    # present, the whole repo is parsed by the low-fidelity regex fallback. That is
    # honest only if SAID — a "complete" stamp over regex-only fidelity is the
    # false-confidence the banking guarantee forbids. Emit ONE repo-level hole per
    # dead language (not per file) and mark partial. ──
    langs_present = {LANGUAGE_MAP[p.suffix] for p in files_to_parse if p.suffix in LANGUAGE_MAP}
    dead_langs = sorted(l for l in langs_present if not _tree_sitter_live(l))
    if dead_langs:
        status = "partial"
        coverage_holes.append({
            "ref": str(repo_root), "kind": "repo",
            "reason": (f"tree-sitter AST parser is NOT functional for {dead_langs} in "
                       f"this environment — those files were parsed by the low-fidelity "
                       f"REGEX fallback (approximate symbols/edges, no precise line spans). "
                       f"Coverage is degraded-fidelity, not full AST; treat accounted "
                       f"symbols as approximate until the AST path is restored."),
        })

    # ── A2: oversized repo → parse up to the cap, flag partial (never silent truncation) ──
    if len(files_to_parse) > _MAX_REPO_FILES:
        coverage_holes.append({
            "ref": str(repo_root), "kind": "repo",
            "reason": (f"repo has {len(files_to_parse)} source files, exceeding the "
                       f"{_MAX_REPO_FILES} cap — parsed the first {_MAX_REPO_FILES}; "
                       f"coverage is PARTIAL, not complete"),
        })
        files_to_parse = files_to_parse[:_MAX_REPO_FILES]
        status = "partial"

    results: list[ParseResult] = []

    def _record(res: ParseResult, st: str, path: Path) -> None:
        nonlocal status
        rel = str(path.relative_to(repo_root))
        if st == "ok":
            # Clean parse. Keep results with content; a clean-empty file (0 nodes)
            # is legitimately empty — NOT a hole (Gate-1 Check-3).
            if res.nodes:
                results.append(res)
        elif st == "unreadable":
            status = "partial"
            coverage_holes.append({
                "ref": rel, "kind": "file",
                "reason": "file could not be read (OSError) — content not parsed",
            })
        elif st == "failed":
            status = "partial"
            coverage_holes.append({
                "ref": rel, "kind": "file",
                "reason": "parse failed (tree-sitter error + regex fallback empty on "
                          "non-empty file) — content not understood",
            })

    # Serial and parallel paths MUST behave identically (Gate-1 Check-3): both use
    # parse_file_with_status and route through _record — no path-dependent drops.
    if len(files_to_parse) < _SERIAL_THRESHOLD:
        for f in files_to_parse:
            res, st = parse_file_with_status(f, repo_root)
            _record(res, st, f)
    else:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(parse_file_with_status, f, repo_root): f
                       for f in files_to_parse}
            for future in futures:
                f = futures[future]
                try:
                    res, st = future.result(timeout=30)
                    _record(res, st, f)
                except Exception as e:
                    # An exception escaping parse_file_with_status (e.g. timeout) is a
                    # parse FAILURE → a hole, never a silent drop.
                    status = "partial"
                    logger.warning(f"Failed to parse {f}: {e}")
                    coverage_holes.append({
                        "ref": str(f.relative_to(repo_root)), "kind": "file",
                        "reason": f"parse raised {type(e).__name__}: {e} — content not parsed",
                    })

    return ParseRepoResult(results=results, coverage_holes=coverage_holes, status=status)


# ── Layer 2: Cross-file Batch Resolution ────────────────────────────────

def resolve_bare_targets(graph_store: GraphStore) -> int:
    """
    Delegate to GraphStore.resolve_bare_targets() — all SQL stays in the store.

    Find all CALLS edges where target has no "::".
    Build global lookup: bare_name -> [qualified_name_1, ...].
    Disambiguate:
      - 1 candidate -> resolve directly
      - N candidates -> prefer the one whose file is imported by caller's file
      - 0 or ambiguous -> leave bare, set confidence=0.5
    Returns count of resolved edges.
    """
    return graph_store.resolve_bare_targets(QUALIFIED_SEPARATOR)
