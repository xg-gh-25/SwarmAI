"""Tests for route_parser.py — route extraction for FastAPI, Express, Next.js."""


from core.code_intel.route_parser import extract_routes, detect_framework, build_prefix_map


# ── test_extract_fastapi_routes ─────────────────────────────────────────

FASTAPI_SAMPLE = '''
from fastapi import APIRouter

router = APIRouter(prefix="/api/users")

@router.get("/")
async def list_users():
    return []

@router.post("/")
async def create_user(user: dict):
    return user

@router.get("/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id}

@router.delete("/{user_id}")
async def delete_user(user_id: int):
    pass
'''

FASTAPI_APP_SAMPLE = '''
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/chat/send")
async def send_message(msg: dict):
    return msg

@app.put("/api/settings")
async def update_settings(settings: dict):
    return settings

@app.patch("/api/profile")
async def patch_profile(data: dict):
    return data
'''


def test_extract_fastapi_routes():
    """Sample FastAPI code extracts correct routes."""
    routes = extract_routes("api/users.py", FASTAPI_SAMPLE, "python")
    assert len(routes) == 4

    # Check methods
    methods = sorted(r.method for r in routes)
    assert methods == ["DELETE", "GET", "GET", "POST"]

    # Check paths — APIRouter(prefix="/api/users") prepends to all routes
    paths = sorted(r.path for r in routes)
    assert "/api/users" in paths
    assert "/api/users/{user_id}" in paths

    # Check framework
    for r in routes:
        assert r.framework == "fastapi"
        assert r.file_path == "api/users.py"
        assert r.confidence == 0.8


def test_extract_fastapi_app_routes():
    """FastAPI app.get/post/put/patch/delete patterns work."""
    routes = extract_routes("main.py", FASTAPI_APP_SAMPLE, "python")
    assert len(routes) == 4

    methods = sorted(r.method for r in routes)
    assert methods == ["GET", "PATCH", "POST", "PUT"]

    paths = sorted(r.path for r in routes)
    assert "/api/chat/send" in paths
    assert "/health" in paths


# ── test_extract_express_routes ─────────────────────────────────────────

EXPRESS_SAMPLE = '''
const express = require("express");
const app = express();
const router = express.Router();

app.get("/api/health", (req, res) => {
    res.json({ status: "ok" });
});

app.post("/api/messages", async (req, res) => {
    const msg = req.body;
    res.json(msg);
});

router.get("/users", getUsers);
router.delete("/users/:id", deleteUser);
'''


def test_extract_express_routes():
    """Sample Express code extracts correct routes."""
    routes = extract_routes("server.js", EXPRESS_SAMPLE, "javascript")
    assert len(routes) == 4

    methods = sorted(r.method for r in routes)
    assert methods == ["DELETE", "GET", "GET", "POST"]

    for r in routes:
        assert r.framework == "express"
        assert r.file_path == "server.js"
        assert r.confidence == 0.8


# ── test_extract_nextjs_routes ──────────────────────────────────────────

NEXTJS_ROUTE_TS = '''
import { NextResponse } from "next/server";

export async function GET(request: Request) {
    return NextResponse.json({ items: [] });
}

export async function POST(request: Request) {
    const body = await request.json();
    return NextResponse.json(body);
}
'''


def test_extract_nextjs_routes():
    """Next.js file-based routing detection works."""
    # Simulated file path in app/ directory with route.ts
    routes = extract_routes("app/api/items/route.ts", NEXTJS_ROUTE_TS, "typescript")
    assert len(routes) == 2

    methods = sorted(r.method for r in routes)
    assert methods == ["GET", "POST"]

    for r in routes:
        assert r.framework == "nextjs"
        assert r.path == "/api/items"
        assert r.confidence == 0.8


# ── test_detect_framework ───────────────────────────────────────────────

def test_detect_framework():
    """Various imports lead to correct framework detection."""
    assert detect_framework("app.py", "from fastapi import FastAPI") == "fastapi"
    assert detect_framework("app.py", "from fastapi import APIRouter") == "fastapi"
    assert detect_framework("server.js", 'const express = require("express")') == "express"
    assert detect_framework("server.ts", 'import express from "express"') == "express"
    assert detect_framework("app/api/route.ts", 'import { NextResponse } from "next/server"') == "nextjs"
    assert detect_framework("app/api/route.ts", 'export async function GET') == "nextjs"


# ── test_no_framework_returns_empty ─────────────────────────────────────

def test_no_framework_returns_empty():
    """Plain Python file with no framework returns empty list."""
    plain_python = '''
def add(a, b):
    return a + b

class Calculator:
    def multiply(self, x, y):
        return x * y
'''
    routes = extract_routes("utils.py", plain_python, "python")
    assert routes == []


# ── test_language_map_coverage ──────────────────────────────────────────

def test_language_map_coverage():
    """LANGUAGE_MAP must have at least 12 entries after expansion."""
    from core.code_intel.parser import LANGUAGE_MAP
    assert len(LANGUAGE_MAP) >= 12


# ── test_build_prefix_map ──────────────────────────────────────────────

MAIN_PY_SAMPLE = '''
from routers import agents_router, chat_router, channels_router
from routers.jobs import router as jobs_router
from routers.pipelines import router as pipelines_router

app = FastAPI()

app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(chat_router, prefix="/api/chat", tags=["chat"])
app.include_router(channels_router, prefix="/api/channels", tags=["channels"])
app.include_router(jobs_router, tags=["jobs"])
app.include_router(pipelines_router, prefix="/api/pipelines", tags=["pipelines"])
'''


def test_build_prefix_map_basic():
    """build_prefix_map resolves router vars to file paths with correct prefixes."""
    pmap = build_prefix_map(MAIN_PY_SAMPLE, "backend/main.py")

    # Bulk import: agents_router → backend/routers/agents.py
    assert pmap.get("backend/routers/agents.py") == "/api/agents"
    assert pmap.get("backend/routers/chat.py") == "/api/chat"
    assert pmap.get("backend/routers/channels.py") == "/api/channels"

    # "as" import: pipelines_router → backend/routers/pipelines.py
    assert pmap.get("backend/routers/pipelines.py") == "/api/pipelines"

    # jobs_router has NO prefix kwarg → should NOT be in map
    assert "backend/routers/jobs.py" not in pmap


def test_build_prefix_map_prefix_not_first_kwarg():
    """prefix= after other kwargs still gets captured."""
    content = '''
from routers import foo_router
app.include_router(foo_router, tags=["foo"], prefix="/api/foo")
'''
    pmap = build_prefix_map(content, "backend/main.py")
    assert pmap.get("backend/routers/foo.py") == "/api/foo"


def test_build_prefix_map_multi_alias_import():
    """Comma-separated 'as' imports on one line resolve correctly."""
    content = '''
from routers.auth import router as auth_router, sub_router as auth_sub_router
app.include_router(auth_router, prefix="/api/auth")
app.include_router(auth_sub_router, prefix="/api/auth/sub")
'''
    pmap = build_prefix_map(content, "backend/main.py")
    # Both aliases come from the same module (routers.auth → backend/routers/auth.py)
    assert pmap.get("backend/routers/auth.py") in ("/api/auth", "/api/auth/sub")


# ── test_test_file_skip ────────────────────────────────────────────────

def test_test_file_routes_skipped():
    """Routes in test files are not extracted."""
    code = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return "ok"
'''
    # Test file patterns that should be skipped
    assert extract_routes("backend/tests/test_api.py", code, "python") == []
    assert extract_routes("src/test/routes.py", code, "python") == []
    assert extract_routes("tests/test_endpoints.py", code, "python") == []

    # Non-test file should work
    routes = extract_routes("backend/main.py", code, "python")
    assert len(routes) == 1

    # "contest" should NOT be skipped (no directory boundary)
    routes = extract_routes("backend/routers/contest.py", code, "python")
    assert len(routes) == 1
