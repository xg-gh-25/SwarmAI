"""A fake Hive's work-order answers, for tests whose Hive is an httpx MockTransport: the
admission state says the Hive prepares workspaces, and ``POST /api/hive/work`` answers
with the order the real one writes (core/hive_workspace.prepare), without git."""
from __future__ import annotations

import json
from typing import Optional

import httpx

from core import hive_workspace as hw

ADMISSION = {"saturated": False, "work_orders": True, "fixed_reserved": True}


def order_for(body: dict) -> dict:
    repos = [{"name": r["name"], "url": r["url"], "ref": r.get("ref"),
              "sha": r.get("sha") or "0" * 40, "access": r["access"],
              "dir": hw.repo_rel(body["work_id"], r["name"]),
              "branch": hw.branch(body["work_id"]) if r["access"] == "write" else None}
             for r in body.get("repos") or []]
    return {"work_id": body["work_id"], "created_at": "2026-10-30T00:00:00Z", "session_id": None,
            "repos": repos, "outputs": hw.outputs_rel(body["work_id"])}


def answer(request: httpx.Request) -> Optional[httpx.Response]:
    """The response to a work-order request, or None for any other path."""
    path = request.url.path
    if path == "/api/hive/work":
        return httpx.Response(200, json=order_for(json.loads(request.content)))
    if path.startswith("/api/hive/work/") and path.endswith("/release"):
        return httpx.Response(200, json={"released": True})
    return None
