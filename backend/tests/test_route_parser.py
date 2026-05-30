"""Tests for route_parser.py — route extraction for FastAPI, Express, Next.js."""

import pytest

from core.code_intel.route_parser import CodeRoute, extract_routes, detect_framework


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

    # Check paths
    paths = sorted(r.path for r in routes)
    assert "/" in paths
    assert "/{user_id}" in paths

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
