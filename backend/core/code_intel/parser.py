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
}

DEFINITION_TYPES = {
    "python": ["function_definition", "class_definition"],
    "typescript": [
        "function_declaration", "class_declaration",
        "method_definition", "arrow_function",
    ],
    "java": [
        "method_declaration", "class_declaration",
        "interface_declaration", "enum_declaration",
    ],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
}

CALL_TYPES = {
    "python": ["call"],
    "typescript": ["call_expression", "new_expression"],
    "java": ["method_invocation", "object_creation_expression"],
    "go": ["call_expression"],
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


# ── Tree-sitter (optional) ─────────────────────────────────────────────

_ts_available = False
_parser_cache: dict[str, object] = {}

try:
    import tree_sitter_language_pack as tslp
    _ts_available = True
except ImportError:
    logger.info("tree-sitter-language-pack not available, using regex fallback")


def _get_cached_parser(language: str):
    """Get or create a tree-sitter parser for a language."""
    if not _ts_available:
        return None
    if language in _parser_cache:
        return _parser_cache[language]
    try:
        parser = tslp.get_parser(language)
        _parser_cache[language] = parser
        return parser
    except Exception as e:
        logger.debug(f"No tree-sitter parser for {language}: {e}")
        return None


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
_REGEX_DEF_PATTERNS = [
    # Python: def func_name(
    re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
    # Python: class ClassName
    re.compile(r'^class\s+(\w+)', re.MULTILINE),
    # JS/TS: function funcName(
    re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', re.MULTILINE),
    # JS/TS: class ClassName
    re.compile(r'^(?:export\s+)?class\s+(\w+)', re.MULTILINE),
    # Java: public/private/protected type methodName(
    re.compile(r'^\s*(?:public|private|protected|static|\s)+\s+\w+\s+(\w+)\s*\(', re.MULTILINE),
    # Go: func funcName(
    re.compile(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', re.MULTILINE),
]

_REGEX_CALL_PATTERN = re.compile(r'(\w+)\s*\(', re.MULTILINE)


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

    # Extract definitions
    for pattern in _REGEX_DEF_PATTERNS:
        for m in pattern.finditer(content):
            name = _sanitize_name(m.group(1))
            if not name or name in defined_names:
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
        # Scan next ~50 lines for calls
        start_pos = func_match.end()
        end_pos = min(start_pos + 3000, len(content))
        body = content[start_pos:end_pos]
        for call_match in _REGEX_CALL_PATTERN.finditer(body):
            call_name = call_match.group(1)
            if call_name in ("if", "for", "while", "return", "print", "raise",
                             "yield", "with", "assert", "except", "import", "from",
                             "class", "def", "func", "function", "var", "let", "const"):
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
    Full pipeline per file:
    1. _build_file_scope() → import_map + defined_names
    2. _extract_from_tree() → walk AST, emit nodes + edges (Layer 1 resolution)
    Falls back to regex if language unsupported or tree-sitter fails.
    """
    lang = LANGUAGE_MAP.get(path.suffix)
    if not lang:
        return ParseResult()

    rel_path = str(path.relative_to(repo_root))

    # Read file once — derive both bytes and text from same buffer
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return ParseResult()

    content = raw_bytes.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw_bytes).hexdigest()

    # Try tree-sitter first
    parser = _get_cached_parser(lang)
    if parser:
        try:
            tree = parser.parse(raw_bytes)
            import_map, defined_names = _build_file_scope_regex(content, lang)
            nodes, edges = _extract_from_tree(tree, rel_path, lang, import_map, defined_names)
            # Set sha256 on all nodes
            for n in nodes:
                n.sha256 = sha
            return ParseResult(nodes=nodes, edges=edges, language=lang, file_path=rel_path)
        except Exception as e:
            logger.debug(f"Tree-sitter failed on {path}: {e}, falling back to regex")

    # Regex fallback
    return _regex_fallback(path, repo_root)


def parse_repo(repo_root: Path, languages: list[str] | None = None) -> list[ParseResult]:
    """
    Walk repo, parse all files matching LANGUAGE_MAP.
    Skips: node_modules, .git, __pycache__, venv, dist, build, .tox, .venv
    Skip logic: check each path component (not fnmatch) — CRG bug #91 fix.
    Parallel: ThreadPoolExecutor (not Process — PyInstaller fork safety).
    Max 4 workers, <8 files serial.
    """
    if not repo_root.is_dir():
        return []

    lang_filter = set(languages) if languages else None
    files_to_parse = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        # Skip dirs by checking each path component
        if any(_should_skip_dir(part) for part in path.relative_to(repo_root).parts):
            continue
        suffix = path.suffix
        if suffix not in LANGUAGE_MAP:
            continue
        if lang_filter and LANGUAGE_MAP[suffix] not in lang_filter:
            continue
        files_to_parse.append(path)

    if not files_to_parse:
        return []

    if len(files_to_parse) < _SERIAL_THRESHOLD:
        return [parse_file(f, repo_root) for f in files_to_parse]

    results = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(parse_file, f, repo_root): f for f in files_to_parse}
        for future in futures:
            try:
                result = future.result(timeout=30)
                if result.nodes:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Failed to parse {futures[future]}: {e}")

    return results


# ── Layer 2: Cross-file Batch Resolution ────────────────────────────────

def resolve_bare_targets(graph_store: GraphStore) -> int:
    """
    Find all CALLS edges where target has no "::".
    Build global lookup: bare_name -> [qualified_name_1, ...].
    Disambiguate:
      - 1 candidate → resolve directly
      - N candidates → prefer the one whose file is imported by caller's file
      - 0 or ambiguous → leave bare, set confidence=0.5
    Returns count of resolved edges.
    """
    # Get all bare targets
    bare_edges = graph_store._conn.execute(
        "SELECT rowid, source_id, target_id FROM code_edges "
        "WHERE edge_type = 'calls' AND target_id NOT LIKE ?",
        (f"%{QUALIFIED_SEPARATOR}%",)
    ).fetchall()

    if not bare_edges:
        return 0

    # Build global name→qualified lookup
    all_nodes = graph_store._conn.execute(
        "SELECT id, name FROM code_nodes"
    ).fetchall()
    name_to_ids: dict[str, list[str]] = {}
    for node_id, name in all_nodes:
        name_to_ids.setdefault(name, []).append(node_id)

    # Build file→imports mapping for disambiguation
    file_imports: dict[str, set[str]] = {}
    import_edges = graph_store._conn.execute(
        "SELECT source_id, target_id FROM code_edges WHERE edge_type = 'imports'"
    ).fetchall()
    for src, tgt in import_edges:
        src_file = src.split(QUALIFIED_SEPARATOR)[0] if QUALIFIED_SEPARATOR in src else src
        tgt_file = tgt.split(QUALIFIED_SEPARATOR)[0] if QUALIFIED_SEPARATOR in tgt else tgt
        file_imports.setdefault(src_file, set()).add(tgt_file)

    resolved_count = 0
    for rowid, source_id, target_name in bare_edges:
        candidates = name_to_ids.get(target_name, [])

        if len(candidates) == 1:
            # Unique match
            graph_store._conn.execute(
                "UPDATE code_edges SET target_id = ?, confidence = 0.8 WHERE rowid = ?",
                (candidates[0], rowid)
            )
            resolved_count += 1
        elif len(candidates) > 1:
            # Disambiguate: prefer candidate whose file is imported by caller
            caller_file = source_id.split(QUALIFIED_SEPARATOR)[0]
            imported_files = file_imports.get(caller_file, set())
            matching = [c for c in candidates
                       if c.split(QUALIFIED_SEPARATOR)[0] in imported_files]
            if len(matching) == 1:
                graph_store._conn.execute(
                    "UPDATE code_edges SET target_id = ?, confidence = 0.8 WHERE rowid = ?",
                    (matching[0], rowid)
                )
                resolved_count += 1
            # else: leave bare with confidence=0.5

    graph_store._conn.commit()
    return resolved_count
