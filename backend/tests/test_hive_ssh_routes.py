"""The ``/api/hive/instances/{id}`` routes act on EC2 Hives only (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §6, §13 invariant 1).

Question: called with an SSH machine's id, is every mutating instance route refused with 409
before the EC2 provisioner is reached, and does the instance list leave the machine out?
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from tests import _ssh_machine_store as store


def test_instance_mutators_refuse_ssh_rows(tmp_path, monkeypatch):
    from routers import hive as route
    db = store.make(tmp_path, monkeypatch)
    store.add(db, "e1", "ec2-one")
    store.add(db, "s1", "dev-dsk", carrier="ssh", carrier_config=store.ssh_config())
    monkeypatch.delenv("SWARMAI_MODE", raising=False)

    def no_provisioner():
        raise AssertionError("the EC2 provisioner was reached for an SSH machine")
    monkeypatch.setattr(route, "_get_provisioner", no_provisioner)

    calls = {
        "stop": lambda: route.stop_instance("s1"),
        "start": lambda: route.start_instance("s1"),
        "restart": lambda: route.restart_instance("s1"),
        "update": lambda: route.update_instance("s1"),
        "delete": lambda: route.delete_instance("s1"),
        "replace": lambda: route.replace_instance("s1"),
        "add-access": lambda: route.add_access("s1", route.HiveAccessAdd(name="alice")),
        "revoke-access": lambda: route.revoke_access("s1", "alice"),
        "rotate-host-key": lambda: route.rotate_host_key("s1"),
        "retry": lambda: route.retry_instance("s1"),
    }
    for what, call in calls.items():
        with pytest.raises(HTTPException) as e:
            asyncio.run(call())
        assert e.value.status_code == 409 and "SSH machine" in e.value.detail, what

    monkeypatch.setattr(route, "_get_provisioner", lambda: _NoStale())
    listed = asyncio.run(route.list_instances())
    assert [i.id for i in listed] == ["e1"]


class _NoStale:
    async def release_stale_updates(self):
        return 0
