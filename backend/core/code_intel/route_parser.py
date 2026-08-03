"""
Route extraction for web frameworks: FastAPI, Express, and Next.js.

Detects HTTP route definitions via regex patterns and produces CodeRoute
dataclass instances for storage in the graph database.

Supported frameworks:
- FastAPI: @app.get/post/put/delete/patch, @router.get(...), APIRouter(prefix=...)
- Express: app.get/post/put/delete, router.get(...)
- Next.js: file-based routing in app/ directory with exported HTTP method functions
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Data Types ──────────────────────────────────────────────────────────


@dataclass
class CodeRoute:
    """Represents an HTTP route extracted from source code."""
    id: str
    method: str
    path: str
    handler_node_id: str
    framework: str
    file_path: str
    line_number: int | None = None
    middleware: list[str] | None = None
    confidence: float = 0.8


# ── Framework Detection ─────────────────────────────────────────────────

def detect_framework(file_path: str, content: str) -> str | None:
    """Detect the web framework used in a file.

    Returns "fastapi", "express", "nextjs", or None.
    """
    # FastAPI detection — import-based
    if re.search(r'from\s+fastapi\s+import|import\s+fastapi', content):
        return "fastapi"

    # Express detection — require or import
    if re.search(r'require\s*\(\s*["\']express["\']\s*\)|import\s+\w+\s+from\s+["\']express["\']', content):
        return "express"

    # Next.js detection — file-based routing in app/ directory
    if _is_nextjs_route_file(file_path, content):
        return "nextjs"

    return None


def _is_nextjs_route_file(file_path: str, content: str) -> bool:
    """Check if this is a Next.js route file (app/**/route.ts|js)."""
    # Must be in an app/ directory and named route.ts/js
    parts = file_path.replace("\\", "/").split("/")
    if "app" not in parts:
        return False
    filename = parts[-1] if parts else ""
    if not re.match(r'^route\.(ts|js|tsx|jsx)$', filename):
        return False
    # Detect via exported HTTP method functions OR next/server import
    if re.search(r'export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)', content):
        return True
    if re.search(r'from\s+["\']next/server["\']', content):
        return True
    return False


# ── Route Extraction ────────────────────────────────────────────────────

# FastAPI patterns: @app.get("/path") or @router.post("/path")
_FASTAPI_ROUTE_RE = re.compile(
    r'^@(?:\w+)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE | re.IGNORECASE,
)

# FastAPI handler: the function defined immediately after the decorator
_FASTAPI_HANDLER_RE = re.compile(
    r'^@(?:\w+)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\'].*?\n'
    r'(?:async\s+)?def\s+(\w+)',
    re.MULTILINE | re.IGNORECASE,
)

# Express patterns: app.get("/path", handler) or router.post("/path", handler)
_EXPRESS_ROUTE_RE = re.compile(
    r'^(?:\w+)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE | re.IGNORECASE,
)

# Next.js exported HTTP method functions
_NEXTJS_EXPORT_RE = re.compile(
    r'^export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)',
    re.MULTILINE,
)


def extract_routes(file_path: str, content: str, language: str) -> list[CodeRoute]:
    """Extract HTTP routes from a source file.

    Args:
        file_path: Relative path to the file.
        content: File content as string.
        language: Language identifier (python, javascript, typescript, etc.)

    Returns:
        List of CodeRoute instances. Returns empty list (never raises) if
        no framework is detected.
    """
    # Skip test files — they define routes for testing but aren't real endpoints
    if "/tests/" in file_path or "/test/" in file_path or file_path.split("/")[-1].startswith("test_"):
        return []

    framework = detect_framework(file_path, content)
    if not framework:
        return []

    try:
        if framework == "fastapi":
            return _extract_fastapi_routes(file_path, content)
        elif framework == "express":
            return _extract_express_routes(file_path, content)
        elif framework == "nextjs":
            return _extract_nextjs_routes(file_path, content)
    except Exception as e:
        logger.debug(f"Route extraction failed for {file_path}: {e}")

    return []


# APIRouter(prefix="/api/jobs") — prefix in constructor
_APIROUTER_PREFIX_RE = re.compile(
    r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']',
)


def _extract_fastapi_routes(file_path: str, content: str) -> list[CodeRoute]:
    """Extract routes from FastAPI code."""
    routes: list[CodeRoute] = []

    # Detect inline prefix from APIRouter(prefix="/api/jobs")
    inline_prefix = ""
    prefix_match = _APIROUTER_PREFIX_RE.search(content)
    if prefix_match:
        inline_prefix = prefix_match.group(1).rstrip("/")

    for match in _FASTAPI_HANDLER_RE.finditer(content):
        method = match.group(1).upper()
        path = match.group(2) or "/"  # Normalize empty string to /
        handler_name = match.group(3)
        line_number = content[:match.start()].count("\n") + 1

        # Apply inline prefix if route doesn't already include it
        if inline_prefix and not path.startswith(inline_prefix):
            path = inline_prefix + "/" + path.lstrip("/") if path != "/" else inline_prefix

        route_id = _make_route_id(file_path, method, path)
        handler_node_id = f"{file_path}::{handler_name}"

        routes.append(CodeRoute(
            id=route_id,
            method=method,
            path=path,
            handler_node_id=handler_node_id,
            framework="fastapi",
            file_path=file_path,
            line_number=line_number,
            confidence=0.8,
        ))

    return routes


def _extract_express_routes(file_path: str, content: str) -> list[CodeRoute]:
    """Extract routes from Express.js code."""
    routes: list[CodeRoute] = []

    for match in _EXPRESS_ROUTE_RE.finditer(content):
        method = match.group(1).upper()
        path = match.group(2)
        line_number = content[:match.start()].count("\n") + 1

        route_id = _make_route_id(file_path, method, path)
        # Express handler is typically inline or named — use a generic ID
        handler_node_id = f"{file_path}::{method.lower()}_{path.replace('/', '_').strip('_')}"

        routes.append(CodeRoute(
            id=route_id,
            method=method,
            path=path,
            handler_node_id=handler_node_id,
            framework="express",
            file_path=file_path,
            line_number=line_number,
            confidence=0.8,
        ))

    return routes


def _extract_nextjs_routes(file_path: str, content: str) -> list[CodeRoute]:
    """Extract routes from Next.js route files."""
    routes: list[CodeRoute] = []

    # Derive the URL path from file path: app/api/items/route.ts → /api/items
    path = _derive_nextjs_path(file_path)

    for match in _NEXTJS_EXPORT_RE.finditer(content):
        method = match.group(1).upper()
        line_number = content[:match.start()].count("\n") + 1

        route_id = _make_route_id(file_path, method, path)
        handler_node_id = f"{file_path}::{method}"

        routes.append(CodeRoute(
            id=route_id,
            method=method,
            path=path,
            handler_node_id=handler_node_id,
            framework="nextjs",
            file_path=file_path,
            line_number=line_number,
            confidence=0.8,
        ))

    return routes


# ── Prefix Resolution ──────────────────────────────────────────────────

# Matches: app.include_router(some_var, ..., prefix="/api/foo", ...)
# prefix= can appear at any position in the kwargs
_INCLUDE_ROUTER_RE = re.compile(
    r'app\.include_router\(\s*(\w+)\s*(?:,\s*[^)]*?prefix\s*=\s*["\']([^"\']+)["\'])?',
    re.MULTILINE,
)

# Matches import patterns:
# from routers import agents_router, ...
# from routers.jobs import router as jobs_router
_IMPORT_FROM_RE = re.compile(
    r'^from\s+([\w.]+)\s+import\s+(.+)$', re.MULTILINE
)


def build_prefix_map(entrypoint_content: str, entrypoint_path: str) -> dict[str, str]:
    """Build a mapping of router file → URL prefix from FastAPI entrypoint.

    Parses import statements to resolve variable names to file paths,
    then matches include_router() calls to extract prefix arguments.

    Args:
        entrypoint_content: Content of main.py or equivalent.
        entrypoint_path: Relative path to entrypoint (e.g. "backend/main.py").

    Returns:
        Dict mapping relative file path → prefix string.
        e.g. {"backend/routers/chat.py": "/api/chat"}
    """
    # Step 1: Resolve variable names → module paths
    var_to_module: dict[str, str] = {}

    for match in _IMPORT_FROM_RE.finditer(entrypoint_content):
        module_path = match.group(1)  # e.g. "routers" or "routers.jobs"
        imports_str = match.group(2).strip()

        # Split on comma first, then handle each import item individually
        # Handles: "import a as b, c as d" and "import x, y, z"
        for item in imports_str.split(","):
            item = item.strip()
            if not item:
                continue
            if " as " in item:
                # "router as jobs_router" → alias maps to this module's file
                parts = item.split(" as ", 1)
                _orig, alias = parts[0].strip(), parts[1].strip()
                file_path = _module_to_filepath(module_path, entrypoint_path)
                var_to_module[alias] = file_path
            else:
                # "agents_router" → convention: strip _router, use as sub-module
                sub_module = item.replace("_router", "")
                file_path = _module_to_filepath(f"{module_path}.{sub_module}", entrypoint_path)
                var_to_module[item] = file_path

    # Step 2: Parse include_router calls → var_name → prefix
    prefix_map: dict[str, str] = {}
    for match in _INCLUDE_ROUTER_RE.finditer(entrypoint_content):
        var_name = match.group(1)
        prefix = match.group(2) or ""  # No prefix arg = ""

        if var_name in var_to_module and prefix:
            prefix_map[var_to_module[var_name]] = prefix

    return prefix_map


def _module_to_filepath(module_path: str, entrypoint_path: str) -> str:
    """Convert a Python module path to a relative file path.

    Uses the entrypoint's directory as the base.
    "routers.chat" with entrypoint "backend/main.py" → "backend/routers/chat.py"
    """
    base_dir = "/".join(entrypoint_path.replace("\\", "/").split("/")[:-1])
    parts = module_path.split(".")
    rel = "/".join(parts) + ".py"
    return f"{base_dir}/{rel}" if base_dir else rel


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_route_id(file_path: str, method: str, path: str) -> str:
    """Generate a stable route ID from file, method, and path."""
    raw = f"{file_path}:{method}:{path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _derive_nextjs_path(file_path: str) -> str:
    """Derive URL path from Next.js file path.

    app/api/items/route.ts → /api/items
    app/dashboard/route.ts → /dashboard
    """
    parts = file_path.replace("\\", "/").split("/")

    # Find 'app' and take everything after it except the filename
    try:
        app_idx = parts.index("app")
        path_parts = parts[app_idx + 1:-1]  # exclude 'app' and the filename
        return "/" + "/".join(path_parts) if path_parts else "/"
    except ValueError:
        return "/"
