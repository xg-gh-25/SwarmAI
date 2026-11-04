"""A session approval covers the command in the directory it was approved in (design
docs/design/2026-11-04-kirocrew-borrow.md §3 A4).

Question: the danger rules read the command's cwd (a relative ``rm -rf build`` is a
different target in another directory); is a command approved once in one directory
let through again there, and asked again in another?
"""
from __future__ import annotations

import asyncio

from core.permission_manager import PermissionManager


def test_approval_scoped_to_cwd(tmp_path, monkeypatch):
    import core.security_hooks as sh
    monkeypatch.setattr(sh, "get_app_data_dir", lambda: tmp_path)
    pm = PermissionManager(persist_path=tmp_path / "pending.json")
    pm.approve_command("s1", "rm -rf build", cwd="/work/a")
    assert pm.is_command_approved("s1", "rm -rf build", cwd="/work/a")
    assert not pm.is_command_approved("s1", "rm -rf build", cwd="/work/b")
    assert not pm.is_command_approved("s2", "rm -rf build", cwd="/work/a")

    asked = []

    async def _decide(session_id, req, timeout=None):
        asked.append(req)
        return "approve"

    pm.request_decision = _decide
    gate = sh.create_dangerous_command_gate({"sdk_session_id": "s9"}, "s9", pm, True)

    def run(cwd, n):
        out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": "rm -rf build"},
                                "cwd": cwd}, f"t{n}", None))
        assert out == {"decision": "approve"}

    a, b = "/work/a", "/work/b"     # outside the temp dirs, where a recursive rm is asked
    run(a, 1)
    assert len(asked) == 1
    run(a, 2)                       # same command, same directory: not asked again
    assert len(asked) == 1
    run(b, 3)                       # another directory: asked again
    assert len(asked) == 2


def test_a_cut_down_digest_cannot_share_an_approval(tmp_path, monkeypatch):
    """Two commands whose digests agree on a prefix (as a prepared pair would) do not
    share one approval: the key is the whole digest."""
    import hashlib
    import core.permission_manager as pmod

    real_sha256 = hashlib.sha256

    class _Prefixed:
        def __init__(self, data: bytes):
            self._real = real_sha256(data)

        def hexdigest(self) -> str:
            return "0" * 16 + self._real.hexdigest()[16:]

    monkeypatch.setattr(pmod.hashlib, "sha256", _Prefixed)
    pm = PermissionManager(persist_path=tmp_path / "pending.json")
    pm.approve_command("s1", "rm -rf build # n1", cwd="/work/a")
    assert not pm.is_command_approved("s1", "rm -rf ~ # n2", cwd="/work/a")
