"""A task that needs a private network is placed, not refused at intake (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §4.4, §13 invariants 5-6).

Questions: (1) does admission let a task with private needs through; (2) when no location
that reaches a private network can take it, is it refused as a private task (422 ``private``
for the router and the card, counted in the history's refusals) with every location's reason,
while a task with no private need still gets a plain refusal, not counted?
"""
from __future__ import annotations

import asyncio

from core import swarm_out as so, task_spec as ts
from core.carriers.base import Needs, Offer, TIER_WARM
from core.carriers.placement import place
from tests.test_task_spec import _brain, _build, ws  # noqa: F401 — the fixture

INTERNAL = """\
bindings:
  - repo: svc
    kind: internal
    clone: "ssh://git.amazon.com/pkg/Svc"
    delivery_contract: {remote_kind: code-amazon-cr, branch: mainline, review_path: crux, auto_send: manual-push}
"""


def test_admit_and_private_refusal(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "private_hosts", lambda: [])
    b = _brain(ws)
    (b / "bindings.yaml").write_text((b / "bindings.yaml").read_text() + INTERNAL.split("bindings:\n", 1)[1])
    spec = _build(ws, "Lib", "fix", "ok")
    assert spec["private"] and "svc is an internal repository" in spec["private"][0]
    assert not any("private network" in r for r in ts.admit(spec))

    monkeypatch.setattr(so, "_state_dir", lambda: tmp_path)
    placement = place(Needs(private=tuple(spec["private"])),
                      [Offer("ec2", "e1", "ec2-one", TIER_WARM),
                       Offer("ssh", "s1", "dev-dsk", TIER_WARM, reaches_private=True, blocked="cert_expired")])
    err = asyncio.run(so._placement_refused("Lib", "T", placement, "none", True))
    assert isinstance(err, so.SwarmOutRefused) and err.private
    assert "ec2-one: private" in str(err) and "dev-dsk: cert_expired" in str(err)
    assert so.refusal_counts() == {"total": 1, "private": 1}

    plain = asyncio.run(so._placement_refused("Lib", "T", place(Needs(), []), "none", False))
    assert type(plain) is so.SwarmOutError
    assert so.refusal_counts() == {"total": 1, "private": 1}            # a plain one is not counted

    from fastapi import HTTPException
    from routers import swarm_out as route
    import pytest

    async def refuse():
        raise err
    with pytest.raises(HTTPException) as h:
        asyncio.run(route._call(refuse()))
    assert h.value.status_code == 422 and h.value.detail["private"] is True
