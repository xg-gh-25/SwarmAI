"""In a non-owner channel the knowledge command may only name shared projects
(knowledge addressing §2.4, §6.3 test 11). The command names no file, so the Bash path
check alone let it read any project's DDD and the Library."""
import asyncio
from pathlib import Path

import pytest

from core.security_hooks import (create_file_access_permission_handler,
                                 restricted_knowledge_denial, shared_project_names)

SHARED = frozenset({"Shared"})


@pytest.mark.parametrize("cmd", [
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --query "quokka"',
    '"$SWARM_BACKEND_BIN" knowledge get --project=Shared 1a2b3c4d',
    '"$SWARM_BACKEND_BIN" knowledge map --project "Shared" --brief',
    'ls -la',
])
def test_allowed(cmd):
    assert restricted_knowledge_denial(cmd, SHARED) is None


@pytest.mark.parametrize("cmd", [
    '"$SWARM_BACKEND_BIN" knowledge find --project Private --query x',
    '"$SWARM_BACKEND_BIN" knowledge map --projects',
    '"$SWARM_BACKEND_BIN" knowledge find --in library --query x',
    '"$SWARM_BACKEND_BIN" knowledge find --query x',
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --project Private --query x',
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --query x; "$SWARM_BACKEND_BIN" knowledge map --project Private',
    '"$SWARM_BACKEND_BIN" knowledge find --project Shared --query x && "$SWARM_BACKEND_BIN" knowledge map',
    '"$SWARM_BACKEND_BIN" py -m core.knowledge_cli find --project Shared --query x',
    '"$SWARM_BACKEND_BIN" py -c "from core import addressing; print(addressing.ddd_map(\'Private\'))"',
    '"$SWARM_BACKEND_BIN" py -c "import core.addressing as a"',
    # each passes the project and count checks; only its own rule stops it
    '"$SWARM_BACKEND_BIN" knowledge map --projects --project Shared',
    '"$SWARM_BACKEND_BIN" knowledge find --in library --project Shared --query x',
    '"$SWARM_BACKEND_BIN" py -c "from core import addressing as a; print(a.ddd_map(\'Private\'))" # knowledge map --project Shared',
])
def test_denied(cmd):
    assert restricted_knowledge_denial(cmd, SHARED)


def test_shared_names_come_from_the_granted_files(tmp_path):
    f = tmp_path / "Projects" / "Shared" / "2-understanding" / "TECH.md"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert shared_project_names([str(f)]) == {"Shared"}


def test_handler_denies_in_a_channel_and_allows_on_desktop_config(tmp_path):
    cmd = {"command": '"$SWARM_BACKEND_BIN" knowledge find --project Private --query x'}
    channel = create_file_access_permission_handler([str(tmp_path)], [], knowledge_projects=SHARED)
    res = asyncio.run(channel("Bash", cmd, {}))
    assert res["behavior"] == "deny" and "not shared" in res["message"]
    owner = create_file_access_permission_handler([str(tmp_path)])
    assert asyncio.run(owner("Bash", cmd, {}))["behavior"] == "allow"


def test_prompt_builder_passes_the_shared_projects_to_the_channel_handler():
    src = (Path(__file__).resolve().parents[1] / "core" / "prompt_builder.py").read_text(encoding="utf-8")
    seg = src[src.index("readonly_files=shared_ddd_files,"):][:200]
    assert "knowledge_projects=shared_project_names(shared_ddd_files)" in seg
