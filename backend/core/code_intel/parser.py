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
import subprocess
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
    # SQL / PL-SQL (Oracle) — regex-only front-end (Run1 MVP, run_533804f3).
    # .pks = package spec, .pkb = package body. See _REGEX_ONLY_LANGS below for
    # why SQL bypasses the tree-sitter path.
    ".sql": "sql",
    ".pks": "sql",
    ".pkb": "sql",
}

# Languages that MUST use the regex extractor even when a tree-sitter grammar is
# "live". SQL is here because PL-SQL procedure BODIES (BEGIN..EXCEPTION..END)
# parse as tree-sitter ERROR subtrees, and parse_file_with_status DROPS all edges
# for any error-tree file (the dangling-target guard, ~L1554). The regex path
# returns its own nodes+edges with status="ok", so the SQL call graph survives.
# Verified live (run_533804f3 Gate-1): tree-sitter 'sql' grammar yields
# create_procedure boundaries but invocation=0 inside bodies + edges dropped.
_REGEX_ONLY_LANGS = frozenset({"sql"})

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

# ── Value-reference edges (per-language descriptor engine) ─────────────────
#
# A `references` edge connects a reader function/method to the MODULE-SCOPE
# constant/variable it reads — closing the "change this const, break its readers"
# impact hole (calls/imports edges never captured value reads). _extract_from_tree
# runs a 2-pass: pass-1 collects distinctive module-scope const bindings (minus a
# shadow set of names also bound in a nested scope / as a parameter), pass-2 emits
# a `constant` node per surviving const + a `references` edge per reader.
#
# LANGUAGE SUPPORT is DESCRIPTOR-DRIVEN, not hardcoded. Each supported language
# has a LangValueSpec describing how its grammar expresses the pieces the 2-pass
# needs. Adding a language = add ONE spec + make it pass the per-language
# validation harness (tests/test_parser.py::TestValueRefLanguages). A language NOT
# in LANG_VALUE_SPEC simply has no value-ref edges (feature-absent, never broken).
#
# WHY A DESCRIPTOR AND NOT A FLAT {node: field} TABLE (Gate-1, run_13667da9): the
# grammars diverge on FIVE axes, each of which silently mis-handles a language if
# assumed to be "like Python":
#   1. binding node → name PATH differs (rust/swift: a `name` field directly;
#      go/ts/php/kotlin: descend one child then its field).
#   2. the READER node type of a const differs from a plain identifier
#      (ruby reads a const as a `constant` node; swift/kotlin as `simple_identifier`;
#      go/rust/ts/js/python as `identifier`). Hardcoding `identifier` would emit
#      ZERO edges for ruby/swift/kotlin — a silent no-op, verified via live AST.
#   3. the MEMBER-ACCESS (attribute) node to guard differs (python=attribute,
#      go=selector_expression, ts=member_expression, rust=field_expression,
#      ruby=scope_resolution, php=member_access_expression, swift/kotlin=
#      navigation_suffix — the member nests under the SUFFIX, not the
#      navigation_expression itself). The guard must use the language's own node types.
#   4. some languages WRAP a module binding (python=expression_statement,
#      ts=export_statement) — the collector must unwrap the language's wrappers.
#   5. the PARAMETER container node (for shadow-prune) differs per language.
#
# DEFERRED (Tier B — NOT in LANG_VALUE_SPEC, by design):
#   - java, csharp: NO module scope — a const lives only as a class field
#     (`field_declaration` inside a class body). Same-file class-member value-ref
#     is a different scope strategy with higher false-positive risk; deferred until
#     a real Java/C# repo can validate it behind the harness.
#   - c: a const is EITHER a `declaration` gated by a `const` type_qualifier OR a
#     `#define` (`preproc_def`) — the latter is a preprocessor node with no
#     field-based name path, a genuinely separate extraction path. Deferred until
#     that path + a real C repo exist. (Gate-1 finding #3.)


class LangValueSpec:
    """Per-language description of how value-ref constants are expressed.

    Fields:
      binding_specs: list of (binding_node_type, name_path). name_path is a tuple
          of steps to reach the identifier from the binding node:
          - a str F        → child_by_field_name(F)
          - ("child", T, F)→ descend to the first child of type T, then its field F
          - ("child", T)   → descend to the first child of type T, take its first
                             identifier-ish leaf
          The final node's text is the const name (only a single plain identifier
          leaf qualifies; a destructuring/pattern LHS yields None → skipped).
      lhs_type_filter: if set, the resolved LHS leaf node MUST be one of these
          types (ruby: {"constant"} — the grammar already marks a constant, so we
          trust it directly instead of the distinctive-name heuristic).
      qualifier_gate: if set, the binding node must have a descendant whose text is
          in this set (reserved for a future C `const` gate; unused by Tier A).
      reader_types: node types that count as a const READ inside a function body
          (python/go/rust/ts/js={"identifier"}; ruby={"constant"};
          swift/kotlin={"simple_identifier"}).
      member_access_types: node types of a member access (obj.CONST) — an
          identifier that is the trailing member of one of these is NOT a const
          read (false-positive guard).
      wrap_types: node types that WRAP a module-scope binding as a single child
          (python={"expression_statement"}, ts={"export_statement"}); the collector
          unwraps one level through these.
      param_container_types: node types whose direct children are function
          parameters (for shadow-prune).
      use_distinctive_name: apply the ≥3-char + uppercase/underscore heuristic.
          False only when lhs_type_filter already guarantees const-ness (ruby).
    """

    __slots__ = (
        "binding_specs", "lhs_type_filter", "qualifier_gate", "reader_types",
        "member_access_types", "receiver_guard_types", "wrap_types",
        "param_container_types", "use_distinctive_name",
        "reader_exclusion_parent_types",
    )

    def __init__(self, *, binding_specs, reader_types, member_access_types,
                 param_container_types, wrap_types=frozenset(),
                 receiver_guard_types=frozenset(),
                 reader_exclusion_parent_types=frozenset(),
                 lhs_type_filter=None, qualifier_gate=None,
                 use_distinctive_name=True):
        self.binding_specs = binding_specs
        self.reader_types = frozenset(reader_types)
        self.member_access_types = frozenset(member_access_types)
        # receiver_guard_types: node types where a leading const is a call RECEIVER
        # (e.g. ruby `Foo.new` — `Foo` is the first child of a `call`), not a value
        # read. Only set for languages whose reader node type doubles as a class ref.
        self.receiver_guard_types = frozenset(receiver_guard_types)
        self.param_container_types = frozenset(param_container_types)
        self.wrap_types = frozenset(wrap_types)
        # reader_exclusion_parent_types: if the reader identifier's DIRECT parent is
        # one of these, it is NOT a bare module-const read. Needed when a language
        # REUSES its reader node type for non-reads (php: `name` is both a const read
        # AND the inner leaf of `$variable_name` and `qualified_name` — Gate-2
        # run_d021ce39: a local `$MAX` or a namespaced `App\MAX` would false-positive).
        self.reader_exclusion_parent_types = frozenset(reader_exclusion_parent_types)
        self.lhs_type_filter = frozenset(lhs_type_filter) if lhs_type_filter else None
        self.qualifier_gate = frozenset(qualifier_gate) if qualifier_gate else None
        self.use_distinctive_name = use_distinctive_name


# Node types that name a single plain identifier leaf. A binding whose resolved LHS
# is one of these (and passes the guards) is a const; anything else (pattern_list,
# tuple_pattern, subscript, …) is skipped as an ambiguous target.
_IDENTIFIER_LEAF_TYPES = frozenset({
    "identifier", "simple_identifier", "constant",
    "name",  # PHP: a const_element's name child + a bare const read are `name` nodes
})

LANG_VALUE_SPEC: dict[str, LangValueSpec] = {
    # Python — reproduces the pre-descriptor behavior byte-identical (the reference
    # implementation). `TIMEOUT: int = 30` parses as `assignment` (typed) → caught;
    # `X += 1` is `augmented_assignment` → excluded (mutation, not a const def).
    "python": LangValueSpec(
        binding_specs=[("assignment", "left")],
        reader_types={"identifier"},
        member_access_types={"attribute"},
        wrap_types={"expression_statement"},
        param_container_types={"parameters"},
    ),
    # Go — const/var declaration wraps a spec node holding the `name` field.
    "go": LangValueSpec(
        binding_specs=[("const_declaration", ("child", "const_spec", "name")),
                       ("var_declaration", ("child", "var_spec", "name"))],
        reader_types={"identifier"},
        member_access_types={"selector_expression"},
        param_container_types={"parameter_list"},
    ),
    # Rust — const_item/static_item expose the name directly as a field.
    "rust": LangValueSpec(
        binding_specs=[("const_item", "name"), ("static_item", "name")],
        reader_types={"identifier"},
        member_access_types={"field_expression"},
        param_container_types={"parameters"},
    ),
    # TypeScript / JavaScript — a lexical_declaration holds variable_declarator(s);
    # a module const may be wrapped in export_statement.
    "typescript": LangValueSpec(
        binding_specs=[("lexical_declaration", ("child", "variable_declarator", "name"))],
        reader_types={"identifier"},
        member_access_types={"member_expression"},
        wrap_types={"export_statement"},
        param_container_types={"formal_parameters"},
    ),
    "javascript": LangValueSpec(
        binding_specs=[("lexical_declaration", ("child", "variable_declarator", "name"))],
        reader_types={"identifier"},
        member_access_types={"member_expression"},
        wrap_types={"export_statement"},
        param_container_types={"formal_parameters"},
    ),
    # Ruby — the grammar MARKS a constant: LHS node type is `constant`, and a const
    # READ is also a `constant` node. Trust the grammar (no distinctive-name guess).
    "ruby": LangValueSpec(
        binding_specs=[("assignment", "left")],
        lhs_type_filter={"constant"},
        use_distinctive_name=False,
        reader_types={"constant"},
        member_access_types={"scope_resolution"},
        # `Foo.new` — a `constant` that is the receiver of a `call` is a class
        # reference, not a value read (Gate-2: false-positive guard). Ruby uses the
        # same `constant` node for a class name and a const value, so we must guard
        # the call-receiver position specifically.
        receiver_guard_types={"call"},
        param_container_types={"method_parameters"},
    ),
    # PHP — const_declaration wraps const_element (name is a `name` node, also the
    # reader node type). Three false-positive shapes suppressed via member_access_types
    # (all verified live-AST, run_d021ce39): `$o->CONST` (member_access_expression),
    # `Foo::CONST` (class_constant_access_expression — CONST is the trailing `name`),
    # and `new CONST()` (object_creation_expression — CONST is the only identifier-ish
    # child, so guard-(a)'s "last member" catches it; note the `new` keyword makes it
    # NOT the first child, so the receiver guard would MISS it — member guard is right).
    "php": LangValueSpec(
        binding_specs=[("const_declaration", ("child", "const_element", None))],
        reader_types={"name"},
        member_access_types={"member_access_expression",
                             "class_constant_access_expression",
                             "object_creation_expression"},
        # php REUSES `name` for a const read, the inner leaf of `$variable_name`,
        # and the inner leaf of `qualified_name` (App\CONST). Exclude the latter two
        # so a local `$MAX` or a namespaced `App\MAX` is NOT a bare-const read
        # (Gate-2 run_d021ce39). swift/kotlin don't need this — their reader type
        # `simple_identifier` is distinct from variable/type nodes.
        reader_exclusion_parent_types={"variable_name", "qualified_name"},
        param_container_types={"formal_parameters"},
    ),
    # Swift — property_declaration binds a `pattern` (name is simple_identifier, also
    # the reader type). `o.CONST` nests the member under `navigation_suffix` (NOT a
    # direct child of navigation_expression), so navigation_suffix is the member type.
    # `CONST()` is a call_expression with CONST as the FIRST child → receiver guard.
    "swift": LangValueSpec(
        binding_specs=[("property_declaration", ("child", "pattern", None))],
        reader_types={"simple_identifier"},
        member_access_types={"navigation_suffix"},
        receiver_guard_types={"call_expression"},
        param_container_types={"parameter"},
    ),
    # Kotlin — property_declaration binds a `variable_declaration` (name is
    # simple_identifier). Same member/receiver shapes as swift (navigation_suffix +
    # call_expression). const val / val / var all parse as property_declaration.
    "kotlin": LangValueSpec(
        binding_specs=[("property_declaration", ("child", "variable_declaration", None))],
        reader_types={"simple_identifier"},
        member_access_types={"navigation_suffix"},
        receiver_guard_types={"call_expression"},
        param_container_types={"function_value_parameters"},
    ),
    # C — a MODULE-SCOPE const is a top-level `declaration` (declaration >
    # init_declarator > identifier) gated by a `const` type_qualifier. This is the
    # FIRST use of qualifier_gate: without it, a mutable global (`int x=3;`) and a
    # `volatile`-qualified global (volatile is ALSO a type_qualifier) would be false
    # consts — the gate is TEXT-EQUALITY on `const` (run_078cf907, RUN 1 of the
    # run_0f977b9f research). member_access_types={field_expression} guards the
    # LEADING-const shape `CFG.field` (CFG is an `identifier` reader); the trailing
    # `s->MAX` member is a `field_identifier`, already excluded by reader_types.
    # KNOWN RECALL GAPS (conservative — drop, never false-emit): names that nest
    # BELOW init_declarator are not reached by the ("child","init_declarator",None)
    # path — a pointer const `const char *NAME` (pointer_declarator), a const array
    # `const int TABLE[3]` (array_declarator), and a typedef'd const are NOT collected
    # (Gate-2 run_078cf907). To add them later, descend pointer_declarator/
    # array_declarator in the binding path. C `#define` is a SEPARATE, permanently
    # deferred path (preproc_def — scopeless textual reads, un-guardable; research
    # NO-GO). java/csharp remain deferred (class-scope value-ref = a future run).
    "c": LangValueSpec(
        binding_specs=[("declaration", ("child", "init_declarator", None))],
        reader_types={"identifier"},
        member_access_types={"field_expression"},
        param_container_types={"parameter_list"},
        qualifier_gate={"const"},
    ),
    # DEFERRED — java, csharp (no module scope — a const is a class field; class-scope
    # value-ref is a future run), and C `#define`/preproc_def (scopeless textual, NO-GO).
}


def _resolve_binding_name_nodes(node, name_path):
    """Resolve a binding node + name_path (see LangValueSpec) to the list of
    identifier leaf nodes it binds — usually one, but MULTIPLE when a single binding
    node groups several names (Gate-2 run_13667da9): go `const ( A=1; B=2 )` has
    several `const_spec` children; ts `const A=1, B=2` has several
    `variable_declarator` children. Taking only the first silently dropped edges for
    the rest (recall gap). Returns [] if the path reaches no identifier leaf.
      - str F          → child_by_field_name(F)  (single-name shapes: rust/swift)
      - ("child", T, F)→ for EVERY child of type T, its field F  (multi-name shapes)
      - ("child", T)   → for EVERY child of type T, its first identifier-ish leaf
    """
    if isinstance(name_path, str):
        n = node.child_by_field_name(name_path)
        return [n] if n is not None else []
    if isinstance(name_path, tuple) and name_path and name_path[0] == "child":
        child_type = name_path[1]
        field = name_path[2] if len(name_path) > 2 else None
        out = []
        for target in (c for c in node.children if c.type == child_type):
            if field:
                leaf = target.child_by_field_name(field)
            else:
                leaf = next((c for c in target.children
                             if c.type in _IDENTIFIER_LEAF_TYPES), None)
            if leaf is not None:
                out.append(leaf)
        return out
    return []


def _is_distinctive_const_name(name: str) -> bool:
    """A value-ref target name is 'distinctive' iff ≥3 chars AND has an uppercase
    letter or underscore. Dodges the local-shadowing false-positive trap that
    single-letter / all-lowercase names invite (`x`, `i`, `tmp` match everything).
    """
    return len(name) >= 3 and (any(c.isupper() for c in name) or "_" in name)


def _is_member_access(node, member_access_types, receiver_guard_types=frozenset()) -> bool:
    """True if this identifier is part of a member/receiver access, NOT a standalone
    value read of a module const — so a value-ref edge would be a false positive.

    TWO guarded shapes (Gate-2 run_13667da9):
      (a) TRAILING MEMBER of a member-access node (`obj.MAX_RETRIES` / `obj->CONST` /
          `Obj::CONST`): the identifier names a member OF another object. The
          member-access node type differs per language (python=attribute,
          go=selector_expression, ts=member_expression, ruby=scope_resolution, …),
          passed as member_access_types. We skip the LAST identifier-ish child (the
          member); the leading object is a genuine read, left alone.
      (b) RECEIVER of a call (`Foo.new` in ruby, where `Foo` is a `constant` node
          that is the FIRST child of a `call` node followed by `.method`): calling a
          method ON a constant is not reading the const's VALUE. Guarded when the
          identifier is the first child of a node in receiver_guard_types AND a later
          sibling is a `.`/method access. (Ruby class-reference false positive.)
    """
    parent = node.parent
    if parent is None:
        return False
    # (a) trailing member of a member-access node.
    if parent.type in member_access_types:
        members = [c for c in parent.children if c.type in _IDENTIFIER_LEAF_TYPES]
        if members and members[-1].start_byte == node.start_byte:
            return True
    # (b) receiver (leading const) of a call — `Foo.new`. Guard only when node is the
    # FIRST child and there is a trailing method access (a `.`/`::`-then-name), so a
    # bare `Foo` read (parent not a call, or Foo used as a value) is NOT guarded.
    if parent.type in receiver_guard_types and parent.children:
        if parent.children[0].start_byte == node.start_byte and len(parent.children) > 1:
            return True
    return False


# NOTE: IMPORT_TYPES not used in Phase 1 — imports extracted via regex in
# _build_file_scope_regex(). Tree-sitter import node walking deferred to Phase 2.

# Directories to skip (check each path component, not fnmatch — CRG bug #91 fix)
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "egg-info", ".next", ".nuxt",
    # `target` = Rust/Cargo (also Maven) build output — a near-universal,
    # tool-reserved build-dir name, safe as a bare component skip at any depth.
    # (Part of the run_f64f6031 fix for ~3316 build-artifact nodes polluting the
    # SwarmAI graph.) NOTE: the PyInstaller bundle dir is NOT skipped here as a bare
    # `binaries` component — see _SKIP_PATH_SUFFIXES below. `binaries` is a plausible
    # legit source-dir name in an arbitrary repo (this parser is the ai-ready-repo
    # ENGINE running on ANY repo), so a bare component skip would silently drop real
    # source — the same over-broad risk that keeps `_internal` (pydantic/_internal)
    # out of this set. General fix (honor .gitignore) is a deferred follow-up.
    "target",
}

# Path-scoped skips (matched against the repo-relative POSIX path, not a single
# component) for build/bundle dirs whose NAME is too generic to skip everywhere.
# `src-tauri/binaries` = the Tauri sidecar convention: the bundled backend binary
# (e.g. desktop/src-tauri/binaries/python-backend-*/_internal/...). Scoping to the
# `src-tauri/` parent means we skip the Tauri bundle in ANY Tauri app WITHOUT
# false-skipping a random repo's top-level `binaries/` source dir (run_f64f6031,
# Gate-2 MED). Match is substring-on-a-slash-delimited path so an intermediate
# component named exactly this pair is caught at any depth.
_SKIP_PATH_SUFFIXES = (
    "src-tauri/binaries",
)

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

# DoD-C (run_dd13fb03, §24.3): extensionless files that ARE build/config source.
# A POSITIVE allowlist by exact filename — NOT `not suffix` (which would spam the
# ledger with LICENSE/README/AUTHORS/binaries; Gate-1 finding). Matched on
# `path.name` so these are accounted as coverage holes (source-like, no AST parser)
# instead of silently dropped; every other extensionless file stays silent.
_EXTENSIONLESS_SOURCE_NAMES = frozenset({
    "Makefile", "makefile", "GNUmakefile", "Makefile.am", "Makefile.in",
    "Dockerfile", "Containerfile", "Rakefile", "Jenkinsfile", "Vagrantfile",
    "Gemfile", "Guardfile", "Procfile", "Brewfile", "Justfile", "justfile",
    "CMakeLists.txt",  # has an ext but is build-source; harmless to include
})

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
    """Check if a single path COMPONENT is a tool-reserved skip dir.

    Bare-component match only — for generic dir names that are only safe to skip
    under a known parent (e.g. `src-tauri/binaries`), see _SKIP_PATH_SUFFIXES and
    the path-scoped check in parse_repo_with_coverage. Keeping generic names OUT of
    SKIP_DIRS is deliberate: this parser runs on arbitrary repos, so a bare skip of
    `binaries`/`_internal` would silently drop real source (run_f64f6031, Gate-2).
    """
    return component in SKIP_DIRS or component.endswith(".egg-info")


def _gitignored_subset(repo_root: Path, paths: list[Path]) -> set[Path]:
    """Return the subset of `paths` that the repo's .gitignore IGNORES, via ONE
    batched `git check-ignore --stdin` (DoD2, run_fe26ed6c).

    git check-ignore semantics we rely on (documented, verified 2026-07-17):
      - Prints ONLY the paths that are ignored; exit 0 = some ignored, 1 = none
        ignored, >1 = error. A TRACKED file matching a pattern is NOT reported
        (git tracks it), so real source is never dropped — this is why we honor
        .gitignore instead of re-implementing pattern matching.
      - --stdin reads NUL/newline-separated paths; we feed newline-separated
        repo-relative POSIX paths and read newline-separated ignored ones back.

    Fail-open (returns empty set) on: git binary missing, non-git dir, timeout,
    any non-{0,1} exit, or decode error. A repo we can't check is simply treated
    as 'nothing extra ignored' — never crashes the walk, never drops source."""
    if not paths:
        return set()
    rel_to_path = {p.relative_to(repo_root).as_posix(): p for p in paths}
    stdin_blob = "\n".join(rel_to_path.keys())
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--stdin"],
            input=stdin_blob, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git check-ignore unavailable for %s: %s", repo_root, e)
        return set()
    # 0 = some ignored, 1 = none ignored (both normal). Anything else = error.
    if proc.returncode not in (0, 1):
        logger.debug("git check-ignore errored (rc=%s) for %s: %s",
                     proc.returncode, repo_root, proc.stderr[:200])
        return set()
    out: set[Path] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        p = rel_to_path.get(line)
        if p is not None:
            out.add(p)
    return out


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


# ── Inheritance / module-level edge config (dead-code FP fixes, run_8e023234) ──
#
# Two dead-code false-positive fixes share the edge-creation surface in _walk:
#   (Fix 1) `extends` edges: a subclass -> its base class(es). A base class with
#           live subclasses used to get ZERO incoming edges (the parser never
#           edged `class Sub(Base)` -> Base), so find_dead_code flagged every base
#           class as dead (AuthException/ConflictException etc.).
#   (Fix 3) module-level `calls` edges: a callee invoked ONLY at module scope had
#           no edge because the calls branch was gated on `enclosing_func`. Now a
#           module-level call sources from a MODULE SENTINEL id (file::<module>).
#
# Both feature flags default True; tests flip them to prove the edges are
# non-vacuous (mutation → edge vanishes). Deferred (NOT here): argument-reference
# edges (register(handler) -> handler) and string-literal dynamic dispatch — the
# Fix-2 family, twice-blocked (Gate-0 EXTREME FP risk + Gate-1 self-edge risk).
_EMIT_EXTENDS_EDGES = True
_EMIT_MODULE_LEVEL_CALL_EDGES = True

# The synthetic source_id for a module-level (no enclosing function) edge. By
# design this is NOT a real code_node — find_dead_code / orphan-cleanup /
# blast_radius all key on target_id, so a synthetic SOURCE is safe (verified:
# graph_store.py:1132 orphan-cleanup checks target_id only; find_callers CTE
# tolerates a source with no matching node). This is why we deliberately do NOT
# add a source_id orphan-cleanup (Gate-1 skeptic's literal Fix-3 suggestion):
# doing so would delete these intentional module-level edges. We adopt the
# skeptic's CONCERN (no dangling references) by keeping the sentinel target-safe,
# not its literal fix (O031: verify the skeptic's claim, adopt the intent).
_MODULE_SENTINEL = "<module>"

# Per-language node types that hold a class's base/parent list. Verified via live
# tree-sitter AST: python bases sit directly in an `argument_list` child of
# `class_definition`; ts/js wrap them in `class_heritage` > extends_clause/
# implements_clause. A language absent here simply emits no extends edges
# (feature-absent, never broken) — same fail-safe posture as LANG_VALUE_SPEC.
_CLASS_BASE_CONTAINER_TYPES = {
    "python": frozenset({"argument_list"}),
    "typescript": frozenset({"class_heritage", "extends_clause", "implements_clause"}),
    "javascript": frozenset({"class_heritage", "extends_clause", "implements_clause"}),
}


def _extract_base_names(class_node, language: str) -> list[str]:
    """Return the base/parent class names declared on a class definition node.

    Walks the language's base-container child (argument_list / class_heritage),
    collecting plain identifier leaves and the TRAILING name of a dotted base
    (`pkg.Other` -> `Other`, `a.b.Other` -> `Other`, TS `React.Component` ->
    `Component`, `ns.Deep.Base` -> `Base`), matching how _resolve_call_target/
    _qualify key on the bare symbol name. Keyword arguments in a python class
    arglist (metaclass=...) are skipped — only positional base identifiers count.
    """
    containers = _CLASS_BASE_CONTAINER_TYPES.get(language)
    if not containers:
        return []
    names: list[str] = []

    # A dotted base's CLASS NAME is the trailing segment: python `a.b.Other`→`Other`
    # (attribute > … > identifier), TS `ns.Deep.Base`→`Base` (member_expression > …
    # > property_identifier). The trailing name leaf differs per grammar — python
    # uses `identifier`, TS uses `property_identifier` — so accept both here (a plain
    # `identifier` is in _IDENTIFIER_LEAF_TYPES; `property_identifier`/`type_identifier`
    # are the TS member/heritage trailing-name types). Verified via live AST.
    _NAME_LEAF_TYPES = _IDENTIFIER_LEAF_TYPES | {"property_identifier", "type_identifier"}

    # A parameterized base is subscripted/generic — the CLASS NAME is the thing
    # being subscripted (the `value`), NOT a type argument: python `Dict[str,User]`
    # → `Dict` (a `subscript` node), `Generic[T]` → `Generic`. Recurse into the
    # value, never scan the arg list (Gate-2 red-team HIGH: the trailing-leaf scan
    # picked the last type ARG — `User`/`T`/`State` — a spurious base).
    _SUBSCRIPT_TYPES = {"subscript", "generic_type"}

    def _leaf_name(n):
        # A parameterized base (subscript/generic): the name is the subscripted
        # value = the FIRST named child, recurse into it (skip the type args).
        if n.type in _SUBSCRIPT_TYPES:
            for c in n.children:
                if c.is_named:
                    return _leaf_name(c)
            return None
        # plain leaf → its text; a dotted/attribute/member base → its LAST trailing
        # name leaf (the class name, not the package). The LAST direct name-leaf
        # child of a member/attribute node is the trailing segment (a.b.Other: the
        # `attribute` node's own direct children are [inner-attribute, '.', Other]
        # → last name-leaf is `Other`). Do NOT early-return into the nested member
        # (that yields the HEAD/middle segment — the run_8e023234 Gate-2 HIGH bug).
        if n.type in _NAME_LEAF_TYPES:
            return _sanitize_name(n.text.decode("utf-8", errors="replace")) if n.text else None
        last_name = None
        for c in n.children:
            if c.type in _NAME_LEAF_TYPES and c.text:
                last_name = c.text  # keep scanning → ends on the trailing segment
        if last_name is not None:
            return _sanitize_name(last_name.decode("utf-8", errors="replace"))
        return None

    # TS type-argument / type-parameter containers are NOT bases — a
    # `React.Component<Props, State>` heritage has [member_expression, type_arguments];
    # scanning type_arguments would emit a spurious base `State` (Gate-2 red-team MED).
    _SKIP_CONTAINER_TYPES = {"type_arguments", "type_parameters"}

    def _collect(node):
        for c in node.children:
            if c.type in containers:
                for b in c.children:
                    # skip punctuation, keywords, and python keyword_argument
                    # (metaclass=X, **kwargs) — only positional bases.
                    if b.type in ("keyword_argument", "comment") or b.is_named is False:
                        continue
                    if b.type in _SKIP_CONTAINER_TYPES:
                        continue
                    if b.type in ("extends_clause", "implements_clause", "class_heritage"):
                        _collect_from(b, names)
                        continue
                    nm = _leaf_name(b)
                    if nm:
                        names.append(nm)

    def _collect_from(container, out):
        for b in container.children:
            if b.type in ("extends_clause", "implements_clause"):
                _collect_from(b, out)
                continue
            if b.type in ("comment",) or b.is_named is False:
                continue
            if b.type in _SKIP_CONTAINER_TYPES:
                continue
            if b.type in ("extends", "implements", "class_heritage"):
                continue
            nm = _leaf_name(b)
            if nm:
                out.append(nm)

    _collect(class_node)
    # dedup preserving order
    seen = set()
    return [n for n in names if not (n in seen or seen.add(n))]


# ── Tree-sitter AST Extraction ──────────────────────────────────────────

def _extract_from_tree(tree, path_str: str, language: str,
                       import_map: dict, defined_names: set) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Walk tree-sitter AST and extract nodes + edges."""
    nodes = []
    edges = []
    root = tree.root_node
    def_types = set(DEFINITION_TYPES.get(language, []))
    call_types = set(CALL_TYPES.get(language, []))
    vspec = LANG_VALUE_SPEC.get(language)

    # ── Value-ref pass 1: collect module-scope const targets + shadow set ──
    # module_consts: {name: line} for distinctive, single-identifier bindings that
    # are DIRECT children of the module root (true module scope). shadowed: names
    # ALSO bound inside a nested scope — a file-scope edge would be a false positive
    # because nested readers resolve to the inner binding. 2-pass (collect ALL
    # before emitting any) makes this order-independent. Descriptor-driven: every
    # per-language node type comes from `vspec` (LangValueSpec), never hardcoded.
    module_consts: dict[str, int] = {}
    if vspec is not None:
        binding_types = {t for t, _p in vspec.binding_specs}
        name_path_by_type = {t: p for t, p in vspec.binding_specs}
        shadowed: set[str] = set()

        def _binding_names(node):
            """Return ALL plain-identifier binding names on this binding node (skip
            tuple/subscript/pattern targets — ambiguous). Usually one, but several
            when a binding groups multiple names (go grouped const, ts `A=1,B=2`).
            Uses the language's name_path + optional lhs_type_filter (ruby trusts the
            grammar `constant` node)."""
            path = name_path_by_type.get(node.type)
            if path is None:
                return []
            out = []
            for leaf in _resolve_binding_name_nodes(node, path):
                if leaf is None or not leaf.text:
                    continue
                if leaf.type not in _IDENTIFIER_LEAF_TYPES:
                    continue
                if vspec.lhs_type_filter and leaf.type not in vspec.lhs_type_filter:
                    continue
                out.append(_sanitize_name(leaf.text.decode("utf-8", errors="replace")))
            return out

        def _passes_name_guard(nm):
            # Ruby-style grammar-marked consts skip the distinctive heuristic.
            return (not vspec.use_distinctive_name) or _is_distinctive_const_name(nm)

        def _passes_qualifier_gate(binding_node) -> bool:
            """When a language sets qualifier_gate, a binding is a const ONLY if it
            carries a matching qualifier keyword as a DIRECT child. C: a top-level
            `declaration` is a const only with a `type_qualifier` whose text is
            `const` — WITHOUT this gate every mutable global (`int x=3;`) and every
            other qualifier (`volatile int x` — volatile is ALSO a `type_qualifier`,
            text `volatile`) would be a false const (verified live, run_078cf907).
            Text-equality on the keyword, NOT mere node-type presence, is required.
            No-op when qualifier_gate is None (the 9 module-scope langs)."""
            if vspec.qualifier_gate is None:
                return True
            return any(
                c.type == "type_qualifier"
                and c.text is not None
                and c.text.decode("utf-8", errors="replace") in vspec.qualifier_gate
                for c in binding_node.children
            )

        # Module-scope bindings = binding nodes that are direct children of root
        # (after unwrapping the language's wrapper nodes, e.g. python
        # expression_statement / ts export_statement). Track their identity
        # (start_byte) so the shadow scan can tell a module binding apart from a
        # same-named inner rebinding.
        module_binding_bytes: set[int] = set()
        for child in root.children:
            inner = child
            # Unwrap one wrapper level if this child wraps a single binding.
            if inner.type in vspec.wrap_types and inner.child_count >= 1:
                inner = next((c for c in inner.children if c.type in binding_types),
                             inner)
            # Python's bare `X = 3` is module-child → expression_statement → assignment;
            # the wrap unwrap above covers it. Also handle a lone single-child wrapper
            # not explicitly typed (defensive, matches prior behavior).
            elif inner.type not in binding_types and inner.child_count == 1:
                inner = inner.children[0]
            if inner.type in binding_types and _passes_qualifier_gate(inner):
                names = [n for n in _binding_names(inner) if _passes_name_guard(n)]
                if names:
                    for nm in names:
                        module_consts.setdefault(nm, inner.start_point[0] + 1)
                    module_binding_bytes.add(inner.start_byte)

        def _param_name(node):
            """Extract the identifier name of a parameter node (or None). A bare
            identifier param is itself the name; a typed/default param has the name
            as its first identifier-ish child.

            php nests the name one level deeper: `simple_parameter > variable_name >
            name` — and a TYPED param (`Foo $x`) has the TYPE name (`named_type > name`)
            as an EARLIER child, so a naive first-identifier scan would grab the type.
            So: if a direct child is `variable_name`, descend it (that is the param
            name, unambiguously) BEFORE the generic first-identifier scan (Gate-2
            run_d021ce39, F2 — php param shadow-prune)."""
            if node.type in _IDENTIFIER_LEAF_TYPES:
                return _sanitize_name(node.text.decode("utf-8", errors="replace")) if node.text else None
            for c in node.children:
                if c.type == "variable_name":
                    for gc in c.children:
                        if gc.type in _IDENTIFIER_LEAF_TYPES and gc.text:
                            return _sanitize_name(gc.text.decode("utf-8", errors="replace"))
            for c in node.children:
                if c.type in _IDENTIFIER_LEAF_TYPES and c.text:
                    return _sanitize_name(c.text.decode("utf-8", errors="replace"))
            return None

        # Shadow set: a binding of a tracked const name whose node is NOT the
        # module-scope binding itself → bound in a nested scope (a nested binding OR
        # a function parameter), so a file-scope edge would be a false positive.
        # Bindings compared by start_byte (node identity), NOT by depth — a module
        # binding and a same-typed nested binding can't be told apart by depth alone.
        def _scan_shadow(node):
            if node.type in binding_types and node.start_byte not in module_binding_bytes:
                for nm in _binding_names(node):
                    if nm in module_consts:
                        shadowed.add(nm)
            # Parameter bindings: identifiers directly under the language's parameter
            # container node (a function signature), not every identifier.
            elif node.type in vspec.param_container_types:
                for pc in node.children:
                    nm = _param_name(pc)
                    if nm and nm in module_consts:
                        shadowed.add(nm)
            for c in node.children:
                _scan_shadow(c)
        _scan_shadow(root)

        for nm in shadowed:
            module_consts.pop(nm, None)

        # Emit a constant node per surviving module-scope const. is_export=0 keeps
        # find_dead_code (is_export=1 filter) clean — a module const is not an
        # "export" in the unreferenced-symbol sense.
        for nm, line in module_consts.items():
            nodes.append(CodeNode(
                id=_qualify(nm, path_str, None), file_path=path_str,
                node_type="constant", name=nm, line_start=line, line_end=line,
                language=language, is_export=False, is_entry_point=False,
            ))

    # (reader_id, const_name) already emitted — dedup per (reader, target).
    _emitted_refs: set[tuple[str, str]] = set()
    _reader_types = vspec.reader_types if vspec is not None else frozenset()
    _member_types = vspec.member_access_types if vspec is not None else frozenset()
    _receiver_types = vspec.receiver_guard_types if vspec is not None else frozenset()
    _reader_excl_parents = (vspec.reader_exclusion_parent_types
                            if vspec is not None else frozenset())

    def _walk(node, enclosing_func=None, enclosing_class=None):
        ntype = node.type

        # Value-ref: a reader function/method body references a module-scope const.
        # The reader node type is per-language (python/go/rust/ts=identifier;
        # ruby=constant; swift/kotlin=simple_identifier — Gate-1: hardcoding
        # 'identifier' silently no-ops ruby/swift/kotlin). line_number=None (a value
        # read, not a call site) — which is why the NULL-line idempotency fix must
        # dedup NULL-line edges.
        if (module_consts and enclosing_func and ntype in _reader_types
                and node.text is not None
                and not (node.parent is not None
                         and node.parent.type in _reader_excl_parents)
                and not _is_member_access(node, _member_types, _receiver_types)):
            nm = _sanitize_name(node.text.decode("utf-8", errors="replace"))
            if nm in module_consts and (enclosing_func, nm) not in _emitted_refs:
                _emitted_refs.add((enclosing_func, nm))
                edges.append(CodeEdge(
                    source_id=enclosing_func,
                    target_id=_qualify(nm, path_str, None),
                    edge_type="references",
                    confidence=1.0,
                    line_number=None,
                ))

        # Definitions
        if ntype in def_types:
            name = _get_name(node, language)
            if name:
                name = _sanitize_name(name)
                # "struct" covers C/C++ struct_specifier (a record TYPE, not a
                # function — run_88512360: a C struct was mislabeled node_type=function
                # because struct_specifier matched none of the type keywords).
                is_class = ("class" in ntype or "interface" in ntype
                            or "enum" in ntype or "struct" in ntype)
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

                # Fix 1: inheritance `extends` edges (dead-code FP #1). MUST be
                # emitted HERE — inside the `if name:` block, before the recursion
                # + early `return` below (Gate-1: code after the return is dead).
                if is_class and _EMIT_EXTENDS_EDGES:
                    for base_name in _extract_base_names(node, language):
                        if base_name == name:
                            continue  # a class cannot extend itself (self-edge guard)
                        target = _resolve_call_target(
                            base_name, path_str, import_map, defined_names,
                            enclosing_class=None,
                        )
                        edges.append(CodeEdge(
                            source_id=qn, target_id=target,
                            edge_type="extends",
                            confidence=1.0 if QUALIFIED_SEPARATOR in target else 0.5,
                            line_number=node.start_point[0] + 1,
                        ))

                new_class = name if is_class else enclosing_class
                new_func = qn if not is_class else enclosing_func
                for child in node.children:
                    _walk(child, enclosing_func=new_func, enclosing_class=new_class)
                return

        # Calls. Inside a function/method → source is the enclosing func. At MODULE
        # scope (no enclosing_func) → Fix 3: source from the module sentinel so the
        # callee still gets an incoming edge (dead-code FP #3). Before, module-level
        # calls produced NO edge, so a module-level-only callee looked dead.
        if ntype in call_types and (enclosing_func or _EMIT_MODULE_LEVEL_CALL_EDGES):
            call_name = _get_call_name(node, language)
            if call_name:
                call_name = _sanitize_name(call_name)
                target = _resolve_call_target(call_name, path_str, import_map,
                                             defined_names, enclosing_class)
                source = enclosing_func or _qualify(_MODULE_SENTINEL, path_str, None)
                edges.append(CodeEdge(
                    source_id=source, target_id=target,
                    edge_type="calls",
                    confidence=1.0 if QUALIFIED_SEPARATOR in target else 0.5,
                    line_number=node.start_point[0] + 1,
                ))

        for child in node.children:
            _walk(child, enclosing_func, enclosing_class)

    _walk(root)
    return nodes, edges


# The node type(s) a definition's NAME child carries, per language. The default
# (identifier/property_identifier/type_identifier) covers python/go/rust/ts/js/java.
# Some grammars name a definition differently and were SILENTLY DROPPED before this
# map existed (verified live-AST, run_d021ce39): php function/class/method names are
# `name` nodes; swift/kotlin `function_declaration` names are `simple_identifier`
# (their `class_declaration` name is `type_identifier`, already covered). The name is
# always the FIRST such child of the definition node (params live inside a separate
# parameter-container node), so returning the first match is correct. Keyed per
# language so widening for php/swift/kotlin CANNOT add spurious names to the others.
_DEFAULT_NAME_NODE_TYPES = ("identifier", "property_identifier", "type_identifier")
NAME_NODE_TYPES: dict[str, tuple[str, ...]] = {
    "php": _DEFAULT_NAME_NODE_TYPES + ("name",),
    "swift": _DEFAULT_NAME_NODE_TYPES + ("simple_identifier",),
    "kotlin": _DEFAULT_NAME_NODE_TYPES + ("simple_identifier",),
}


# C-family languages whose function/method NAME is nested inside a `declarator`
# field (not a direct child) — the codegraph pattern (colbymchenry/codegraph
# src/extraction/languages/c-cpp.ts). `_get_name`'s flat direct-child scan returns
# None for these (verified run_88512360: `int add(...)` → function_definition >
# function_declarator > identifier; `char* dup(...)` adds a pointer_declarator
# wrapper; a C++ method name is a `field_identifier`; an out-of-line def is a
# `qualified_identifier`). SCOPED to c/cpp: python/php also use function_definition
# but resolve their name as a DIRECT child and have no `declarator` field, so they
# must stay on the flat scan (M3 cross-lang finding).
_C_FAMILY_LANGS = frozenset({"c", "cpp"})
# Name-bearing leaf/wrapper nodes reachable via the declarator BFS. operator_name
# (`operator+`) and destructor_name (`~Foo`) are WRAPPERS whose full text IS the
# name — they must be matched ON DEQUEUE before descending, else destructor_name's
# inner `identifier` wins and drops the `~` (Gate-1 run_88512360, verified live).
_C_DECLARATOR_NAME_TYPES = frozenset({
    "identifier", "field_identifier", "qualified_identifier",
    "operator_name", "destructor_name",
    # operator_cast = a C++ user-defined conversion operator (`operator int`); its
    # node text is `operator int()` (trailing param list included) so the name is
    # rebuilt from its non-parameter children (Gate-2 LOW, run_88512360). Without it
    # a conversion operator resolves to None and is silently dropped.
    "operator_cast",
})
# Declarator-subtree children NOT descended into — a parameter's own type/name
# (`const std::string& x`) or a trailing return type must never be mistaken for the
# function name (codegraph's documented `std::string TableFileName(...)`→`string` bug).
_C_DECLARATOR_SKIP = frozenset({"parameter_list", "parameters", "trailing_return_type"})


def _c_family_declarator_name(node) -> str | None:
    """Resolve a c/cpp definition's name by descending its `declarator` field
    (BFS, skipping parameter/return-type subtrees). Returns the name text, or None
    if the node has no declarator field (e.g. struct_specifier/class_specifier —
    those fall back to the flat scan, which reads their direct `type_identifier`)."""
    dec = node.child_by_field_name("declarator")
    if dec is None:
        return None
    from collections import deque
    queue = deque([dec])
    while queue:
        cur = queue.popleft()
        # Match wrapper/leaf name nodes on dequeue BEFORE enqueuing children, so a
        # destructor_name (~Foo) returns its full text instead of the inner `Foo`.
        if cur.type in _C_DECLARATOR_NAME_TYPES:
            if cur.type == "qualified_identifier":
                # out-of-line def `Ret Foo::bar()` → take the last :: segment
                parts = [p for p in cur.text.decode("utf-8", errors="replace").strip().split("::") if p]
                return parts[-1] if parts else None
            if cur.type == "operator_cast":
                # node text is `operator int()` — rebuild from non-parameter children
                # (`operator` keyword + the target type) so the name is `operator int`.
                bits = [c.text.decode("utf-8", errors="replace")
                        for c in cur.children if c.type not in _C_DECLARATOR_SKIP
                        and c.type != "abstract_function_declarator"]
                return " ".join(b for b in bits if b) or None
            return cur.text.decode("utf-8", errors="replace")
        for c in cur.children:
            if c.type not in _C_DECLARATOR_SKIP:
                queue.append(c)
    return None


def _get_name(node, language: str) -> str | None:
    """Extract the name identifier from a definition node (per-language name types).

    c/cpp function/method names are NESTED in a `declarator` field, not a direct
    child — those go through declarator descent first, then fall back to the flat
    scan (which handles struct_specifier/class_specifier, whose name IS a direct
    type_identifier)."""
    if language in _C_FAMILY_LANGS:
        nested = _c_family_declarator_name(node)
        if nested is not None:
            return nested
        # fall through: struct_specifier/class_specifier name is a direct child
    name_types = NAME_NODE_TYPES.get(language, _DEFAULT_NAME_NODE_TYPES)
    for child in node.children:
        if child.type in name_types:
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
    # SQL / PL-SQL (Oracle). Matches BOTH forms:
    #   • standalone:      CREATE [OR REPLACE] PROCEDURE|FUNCTION <name>   (.sql)
    #   • in-package body:            PROCEDURE|FUNCTION <name>            (.pkb)
    # The CREATE prefix is OPTIONAL because inside a CREATE PACKAGE BODY the member
    # procedures have NO CREATE — they are bare `PROCEDURE name(...) IS` (verified on
    # real HLR body.sql: 0 CREATE-form, 33 bare-form). Optionally schema-qualified
    # (schema.name → captures the LAST segment). Case-insensitive (Oracle identifiers
    # are). Line-anchored so a mid-line PROCEDURE keyword in a string/comment, or an
    # `END PROCEDURE`, does not match.
    # NOTE: the SQL def pattern is applied by _regex_fallback over COMMENT/STRING-
    # STRIPPED content (see the lang=="sql" pre-strip there) and requires an IS/AS
    # body-introducer, so a forward declaration (`PROCEDURE foo(...);`, no body — a
    # .pks spec) is NOT counted as a definition. Kept in sync with _SQL_DEF_HEADER.
    "sql": [
        re.compile(
            r'^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?)?'
            r'(?:PROCEDURE|FUNCTION)\s+'
            r'(?:"?\w+"?\.)?'   # optional (possibly "quoted") schema qualifier
            r'"?(\w+)"?'        # name, optionally "double-quoted" (Oracle allows both)
            r'(?:\s*\([^;]*?\))?'
            r'(?:\s+RETURN\s+[^\s;]+)?'
            r'\s*(?:IS|AS)\b',  # \s* not \s+: Oracle allows glued `)IS`
            re.MULTILINE | re.IGNORECASE | re.DOTALL),
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


# SQL built-in functions / keywords that a naive `NAME(` scan would mistake for a
# procedure call. Denoise list for the SQL call-edge extractor. Upper-cased; the
# scan compares case-insensitively (Oracle identifiers are case-insensitive).
_SQL_BUILTINS = frozenset({
    "NVL", "NVL2", "DECODE", "COALESCE", "NULLIF", "COUNT", "SUM", "AVG", "MIN",
    "MAX", "ROUND", "TRUNC", "TO_CHAR", "TO_NUMBER", "TO_DATE", "SUBSTR",
    "INSTR", "LENGTH", "TRIM", "LTRIM", "RTRIM", "UPPER", "LOWER", "REPLACE",
    "LPAD", "RPAD", "CONCAT", "SYSDATE", "SYSTIMESTAMP", "CAST", "ABS", "MOD",
    "GREATEST", "LEAST", "ROW_NUMBER", "RANK", "LISTAGG", "EXTRACT", "SIGN",
    "CEIL", "FLOOR", "POWER", "SQRT", "ADD_MONTHS", "MONTHS_BETWEEN", "LAST_DAY",
    "USER", "RAISE_APPLICATION_ERROR",
    # PL/SQL statement keywords a `WORD(` regex catches before the paren
    "IF", "ELSIF", "LOOP", "WHILE", "FOR", "CASE", "WHEN", "VALUES", "INTO",
    "FORALL", "OPEN", "FETCH", "CLOSE", "EXCEPTION", "RETURN", "COMMIT",
    "ROLLBACK", "TABLE", "IN", "OUT", "AND", "OR", "NOT", "EXISTS",
})

# A PL/SQL call site: an identifier (optionally schema/package-qualified) followed
# by an opening paren. Captures the OPTIONAL qualifier and the bare name separately
# so a package call `PKG.proc(` yields name='proc' with qualifier='PKG'.
_SQL_CALL_PATTERN = re.compile(r'(?:"?\w+"?\.)?"?(\w+)"?\s*\(', re.IGNORECASE)

# PL/SQL comment + string-literal spans. Stripped (replaced by equal-length spaces
# to preserve byte offsets → correct line numbers) BEFORE scanning for call sites,
# so a call token inside `-- comment`, `/* block */`, or a `'...'` string literal
# (e.g. dynamic SQL) does NOT fabricate a false edge (Gate-2 HIGH, run_533804f3).
_SQL_STRIP_PATTERN = re.compile(
    r"--[^\n]*"          # line comment to EOL
    r"|/\*.*?\*/"        # block comment (non-greedy, spans newlines)
    r"|'(?:''|[^'])*'",  # single-quoted string ('' is an escaped quote)
    re.DOTALL)

# A PL/SQL procedure/function DEFINITION header. Distinguishes a definition from a
# forward DECLARATION (`.pks` spec: `PROCEDURE foo(...);` — ends in `;`, no body) by
# requiring an `IS`/`AS` body-introducer after the (optional, possibly multi-line)
# parameter list and before any `;` (Gate-2 HIGH, run_533804f3). CREATE is optional
# for `.pkb` package members (bare `PROCEDURE name ... IS`). The `(?:\([^;]*?\))?`
# tolerates a param list that must not contain a `;` (PL/SQL params never do).
# MUST mirror the def-extraction pattern (they segment/count the same procedures).
_SQL_DEF_HEADER = re.compile(
    r'^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?)?'
    r'(?:PROCEDURE|FUNCTION)\s+'
    r'(?:"?\w+"?\.)?'             # optional (possibly "quoted") schema qualifier
    r'"?(\w+)"?'                  # name, optionally "double-quoted"
    r'(?:\s*\([^;]*?\))?'          # optional param list (no ';' inside)
    r'(?:\s+RETURN\s+[^\s;]+)?'    # FUNCTION return type
    r'\s*(?:IS|AS)\b',             # \s* not \s+: Oracle allows glued `)IS`; body-introducer → DEFINITION not decl
    re.MULTILINE | re.IGNORECASE | re.DOTALL)


def _sql_strip_comments_strings(text: str) -> str:
    """Blank out PL/SQL comments + string literals, preserving length/offsets."""
    return _SQL_STRIP_PATTERN.sub(lambda m: " " * (m.end() - m.start()), text)


def _sql_strip_comments_only(text: str) -> str:
    """Blank out PL/SQL COMMENTS but PRESERVE string literals, offsets preserved.

    Uses the SAME single-pass alternation as _SQL_STRIP_PATTERN so a `--` INSIDE a
    string literal is consumed as part of the string span (never mistaken for a
    comment) and a `'` inside a `-- comment` is consumed as part of the comment —
    the mutual-recursion boundary problem a naive `--[^\n]*` gets wrong (Gate-1 H1,
    run_4056325a). The callback blanks ONLY the two comment alternatives; a string
    match is returned UNCHANGED so the dynamic-SQL scanner can read the RHS literal.
    """
    def _repl(m):
        s = m.group(0)
        if s.startswith("'"):   # a string literal → keep it (we scan it)
            return s
        return " " * (m.end() - m.start())  # a comment → blank, preserve offsets
    return _SQL_STRIP_PATTERN.sub(_repl, text)


# A dynamic-SQL string assignment: `<var> := '<VERB> [schema.]<table> ...`. Captures
# (var, operation, table). VERB set = the write/DDL operations whose FIRST object is
# the target table. Case-insensitive. Only the write-TARGET is captured (not FROM
# read-sources) — a deliberate v1 scope named by the `dynamic_sql_write:` edge_type
# (Gate-1 #3, run_4056325a). The RHS opening quote anchors it to a real assignment.
_SQL_DYNAMIC_ASSIGN = re.compile(
    r"""(\w+)\s*:=\s*'\s*
        (CREATE\s+TABLE|INSERT\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE
         |TRUNCATE\s+TABLE|DROP\s+TABLE|MERGE\s+INTO)
        \s+(?:"?\w+"?\.)?"?(\w+)"?""",
    re.IGNORECASE | re.VERBOSE)

# Map the matched multi-word verb → a compact operation token for the edge_type.
_SQL_OP_TOKEN = {
    "CREATE": "CREATE", "INSERT": "INSERT", "UPDATE": "UPDATE", "DELETE": "DELETE",
    "ALTER": "ALTER", "TRUNCATE": "TRUNCATE", "DROP": "DROP", "MERGE": "MERGE",
}


def _sql_dynamic_sql_edges(content, rel_path, headers):
    """Surface dynamic-SQL write targets that static analysis cannot see.

    PL/SQL builds SQL as string literals (`SQL_TXT := 'CREATE TABLE FOO AS ...'`)
    then executes them (EXECUTE IMMEDIATE) or passes them to a helper — so the table
    a procedure actually touches is invisible to the call graph. This scans the
    assignment RHS literals for a write verb + target table and emits, per hit:
      • a `data_object` CodeNode for the table (REQUIRED — else graph_store's orphan
        cleanup deletes the edge because its target isn't a node; Gate-1 C1), with a
        `table:`-namespaced id so it never collides with a same-named procedure
        (Gate-1 C2);
      • a `dynamic_sql_write:<OP>` CodeEdge proc→table, confidence 0.4 (this is an
        ASSIGNMENT, weaker evidence than an executed call — Gate-1 H2).

    `headers` is the list of _SQL_DEF_HEADER matches (procedure segmentation), reused
    from the caller so a hit's line is attributed to its enclosing procedure; an
    assignment before the first header (package init) is attributed to a module
    sentinel rather than dropped (Gate-1 #4).
    """
    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []
    # Comments blanked, STRING LITERALS PRESERVED (we must read the RHS literal).
    scan = _sql_strip_comments_only(content)
    header_starts = [(h.start(), _sanitize_name(h.group(1))) for h in headers]

    def _enclosing_proc(pos: int) -> str:
        name = None
        for hs, hname in header_starts:
            if hs <= pos:
                name = hname
            else:
                break
        return _qualify(name, rel_path) if name else _qualify(_MODULE_SENTINEL, rel_path)

    seen_tables: set[str] = set()
    for m in _SQL_DYNAMIC_ASSIGN.finditer(scan):
        op_word = m.group(2).split()[0].upper()
        op = _SQL_OP_TOKEN.get(op_word, op_word)
        table = _sanitize_name(m.group(3))
        if not table:
            continue
        line = scan[:m.start()].count("\n") + 1
        table_id = _qualify("table:" + table, rel_path)  # namespaced → no proc collision
        if table not in seen_tables:
            seen_tables.add(table)
            nodes.append(CodeNode(
                id=table_id, file_path=rel_path, node_type="data_object",
                name=table, line_start=line, line_end=line, language="sql",
                is_export=False, is_entry_point=False,
            ))
        edges.append(CodeEdge(
            source_id=_enclosing_proc(m.start()),
            target_id=table_id,
            edge_type="dynamic_sql_write:" + op,
            confidence=0.4,
            line_number=line,
        ))
    return nodes, edges


def _sql_call_edges(content: str, rel_path: str, import_map: dict,
                    defined_names: set) -> list["CodeEdge"]:
    """Extract SQL call edges, denoised via the DEFINED-NAMES WHITELIST.

    PL/SQL bodies are not cleanly AST-parseable, so we segment the file by
    procedure headers and scan each body's text for `NAME(` call sites. To keep
    the graph honest (Gate-1 / THINK decision: miss > false-connect), an edge is
    emitted ONLY when the callee is a procedure DEFINED IN THIS FILE
    (`defined_names`). External/package calls (e.g. `UTILS_INTERFACES.export_csv`)
    and SQL builtins (`NVL`, `DECODE`) are intentionally NOT connected as local
    edges — they are out-of-file and would be false-positive local links.

    Bodies span header→next-header (NOT a fixed line window): real HLR procedures
    run 700+ lines, so a 50-line window would truncate the call scan.
    """
    # Oracle identifiers are CASE-INSENSITIVE: a def `Log_Run` and a call `log_run`
    # are the same procedure. Fold the whitelist to a {casefold: canonical_name} map
    # so the string compare below matches regardless of source casing (Gate-2
    # CRITICAL, run_533804f3 — a case-sensitive `set` compare dropped every edge on
    # mixed-case PL/SQL). The canonical (defined) name is used for the edge target id.
    canon = {n.casefold(): n for n in defined_names}

    # Strip comments + string literals ONCE so a call token inside a comment or a
    # dynamic-SQL string cannot fabricate an edge (Gate-2 HIGH). Offsets preserved.
    scan = _sql_strip_comments_strings(content)

    edges: list[CodeEdge] = []
    headers = list(_SQL_DEF_HEADER.finditer(scan))
    for i, hmatch in enumerate(headers):
        proc_name = _sanitize_name(hmatch.group(1))
        proc_qn = _qualify(proc_name, rel_path)
        body_start = hmatch.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(scan)
        body = scan[body_start:body_end]
        seen: set[str] = set()
        for cm in _SQL_CALL_PATTERN.finditer(body):
            call_name = _sanitize_name(cm.group(1))
            if not call_name or call_name.upper() in _SQL_BUILTINS:
                continue
            key = call_name.casefold()
            # Whitelist denoise (THINK decision: miss > false-connect): connect ONLY
            # to procedures DEFINED IN THIS FILE. Membership IS the whitelist — this
            # correctly handles a call qualified with the package's OWN name (real HLR
            # calls its own members as `RECONCILIATION_INTERFACES.prov_recon_services(
            # ...)`, a same-package self-reference that IS local). A call whose bare
            # name is NOT defined in-file (e.g. `UTILS_INTERFACES.export_csv`) stays
            # external — skipped.
            if key not in canon:
                continue
            if key == proc_name.casefold() or key in seen:
                continue  # self-recursion / dedup per (caller, callee)
            seen.add(key)
            line = scan[:body_start + cm.start()].count("\n") + 1
            edges.append(CodeEdge(
                source_id=proc_qn,
                target_id=_qualify(canon[key], rel_path),
                edge_type="calls", confidence=0.6,
                line_number=line,
            ))
    return edges


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

    # For SQL, run def-extraction over comment/string-STRIPPED text so a
    # `-- PROCEDURE foo IS` in a comment (or inside a dynamic-SQL string) does not
    # create a phantom procedure node (Gate-2, run_533804f3). Other languages keep
    # scanning raw content (byte-identical prior behavior).
    def_scan = _sql_strip_comments_strings(content) if lang == "sql" else content

    # Extract definitions
    for pattern in patterns:
        for m in pattern.finditer(def_scan):
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

    # Find function bodies and extract calls within them. SQL uses a distinct
    # body model (see _sql_call_edges); every other language keeps the original
    # def|function|func window scan BYTE-IDENTICALLY (AC7 — no behavior change).
    if lang == "sql":
        edges.extend(_sql_call_edges(content, rel_path, import_map, defined_names))
        # Run2: dynamic-SQL write targets (invisible to the static call graph).
        # Reuse the SAME procedure segmentation the def-extraction used.
        _dyn_headers = list(_SQL_DEF_HEADER.finditer(def_scan))
        _dyn_nodes, _dyn_edges = _sql_dynamic_sql_edges(content, rel_path, _dyn_headers)
        nodes.extend(_dyn_nodes)
        edges.extend(_dyn_edges)
    else:
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
    # _REGEX_ONLY_LANGS (e.g. sql) skip the tree-sitter branch even when a grammar
    # is live — their edges would be dropped by the error-tree guard below.
    parser = (_get_cached_parser(lang)
              if (lang not in _REGEX_ONLY_LANGS and _tree_sitter_live(lang))
              else None)
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
                # DoD-B (run_dd13fb03, §24.4 graphify ERROR-node fallback): tree-sitter
                # error-recovery still parsed the well-formed top-level symbols into
                # `nodes`. SALVAGE them (a partially-broken file keeps its parseable
                # symbols) instead of discarding the whole file — BUT still record it
                # as a coverage hole so this is NOT a silent-ok (status stays partial).
                # Edges are DROPPED: a broken tail can leave an edge pointing at a node
                # that was never created (dangling target), and get_module_edges guards
                # such bare targets out anyway — nodes carry the durable value.
                if nodes:
                    logger.debug(f"Tree-sitter partial-error on {path} → degraded (salvaged {len(nodes)} symbols)")
                    return ParseResult(nodes=nodes, edges=[], language=lang, file_path=rel_path), "degraded"
                # nothing salvageable → genuine failure (unchanged)
                logger.debug(f"Tree-sitter parse has errors on {path}, no salvageable symbols → failed")
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
        rel = path.relative_to(repo_root)
        # Skip dirs by checking each path component (out of scope, not a hole)
        if any(_should_skip_dir(part) for part in rel.parts):
            continue
        # Path-scoped skips (generic dir names only safe under a known parent, e.g.
        # `src-tauri/binaries` — the Tauri sidecar bundle). Match on the POSIX path
        # so an intermediate `.../src-tauri/binaries/...` is caught at any depth.
        rel_posix = rel.as_posix()
        if any(f"{sfx}/" in f"{rel_posix}/" for sfx in _SKIP_PATH_SUFFIXES):
            continue
        suffix = path.suffix
        if suffix in LANGUAGE_MAP:
            if lang_filter and LANGUAGE_MAP[suffix] not in lang_filter:
                continue
            files_to_parse.append(path)
        elif path.name in _EXTENSIONLESS_SOURCE_NAMES:
            # DoD-C (run_dd13fb03, §24.3): a source-like build/config file with no
            # AST parser (Makefile/Dockerfile/…). Account it as a hole — never a
            # silent drop — but bounded (one row per name, like unknown-ext).
            rel = str(path.relative_to(repo_root))
            unknown_ext_counts[path.name] = unknown_ext_counts.get(path.name, 0) + 1
            ex = unknown_ext_examples.setdefault(path.name, [])
            if len(ex) < _MAX_UNKNOWN_EXT_EXAMPLES:
                ex.append(rel)
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

    # ── DoD2 (run_fe26ed6c): honor the target repo's .gitignore ──
    # The hardcoded SKIP_DIRS/suffix checks above are a fast-path (and the ONLY
    # skip mechanism for non-git repos). For a git repo we ALSO drop files the
    # repo itself ignores (build output under a non-standard name — out/, _build/,
    # bazel-*, etc.) that the hardcoded list can't know. Done as ONE batched
    # `git check-ignore --stdin` AFTER the walk (never a per-file subprocess —
    # that would fork thousands of times). git check-ignore correctly does NOT
    # report a TRACKED file even if it matches a pattern, so real source is never
    # dropped. Fail-open: no .git / git missing / git error → keep the SKIP_DIRS
    # result unchanged. O030: ignored files are RECORDED as coverage holes
    # (kind='gitignored'), bounded — never silently invisible.
    if files_to_parse and (repo_root / ".git").exists():
        ignored = _gitignored_subset(repo_root, files_to_parse)
        if ignored:
            files_to_parse = [p for p in files_to_parse if p not in ignored]
            status = "partial"
            _ignored_rels = sorted(str(p.relative_to(repo_root)) for p in ignored)
            for ex in _ignored_rels[:_MAX_UNKNOWN_EXT_EXAMPLES]:
                coverage_holes.append({
                    "ref": ex, "kind": "gitignored",
                    "reason": (f"ignored by the repo's .gitignore ({len(ignored)} "
                               f"file(s) total; showing up to "
                               f"{_MAX_UNKNOWN_EXT_EXAMPLES}). Out of scope by the "
                               f"repo's own rules — recorded, never silently dropped."),
                })

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
        elif st == "degraded":
            # DoD-B (run_dd13fb03): partial error-tree — KEEP the salvaged symbols
            # AND record the hole (understood-partially, NOT silent-ok). Both, not
            # either: the nodes carry value, the hole keeps the coverage honest.
            status = "partial"
            if res.nodes:
                results.append(res)
            coverage_holes.append({
                "ref": rel, "kind": "file",
                "reason": (f"parse degraded (tree-sitter error-tree; {len(res.nodes)} "
                           "top-level symbol(s) salvaged, call edges dropped, broken "
                           "region opaque) — understood PARTIALLY, not fully"),
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

    Resolves calls/extends/references targets that match no node id (bare, OR
    qualified-but-dangling from the import-map/node-id mismatch) by bare name:
      - 1 candidate -> resolve directly (confidence 0.8)
      - N candidates -> prefer the one whose file is imported by caller's file
      - 0 or ambiguous -> leave unresolved (orphan-cleanup drops it)
    Returns count of resolved edges.
    """
    return graph_store.resolve_bare_targets(QUALIFIED_SEPARATOR)
