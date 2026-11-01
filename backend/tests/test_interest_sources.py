"""Signal relevance comes from the person's own context, never the author's (R5.4).

- signal_digest: with no user context the model is told to rank by general relevance
  to software and AI practitioners; no built-in interests (SwarmAI, the Chinese AI
  industry, ...) are put in their place.
- self_tune.extract_interests_from_memory learns from the person's USER.md (the
  Focus / Role lines and the Work Context section) and MEMORY.md with the same generic
  tech vocabulary the project extractor uses; there is no fixed author list.
"""
from __future__ import annotations

import pytest

from jobs.models import RawSignal

AUTHOR_TERMS = ("SwarmAI", "Claude SDK", "Physical AI", "大模型", "具身智能", "中文AI",
                "context engineering", "Chinese AI industry")


@pytest.fixture
def captured(monkeypatch):
    import jobs.bedrock as bedrock
    prompts: list[str] = []

    def fake_text(prompt, **_k):
        prompts.append(prompt)
        return "# digest\n"

    def fake_structured(prompt, **_k):
        prompts.append(prompt)
        return {"scores": [{"idx": 0, "relevance_score": 0.5}]}

    monkeypatch.setattr(bedrock, "text", fake_text)
    monkeypatch.setattr(bedrock, "structured", fake_structured)
    monkeypatch.setattr(bedrock, "get_model_id", lambda *a, **k: "model")
    return prompts


def _signals():
    return [RawSignal(feed_id="f", title="A new release", url="https://example.com/a",
                      summary="Something shipped.")]


def test_digest_without_user_context_is_neutral(captured):
    from jobs.handlers import signal_digest
    signal_digest._llm_digest(_signals(), "")
    assert len(captured) == 2
    for prompt in captured:
        assert "general relevance to software and AI practitioners" in prompt
        for term in AUTHOR_TERMS:
            assert term not in prompt, term


def test_digest_uses_the_users_context(captured):
    from jobs.handlers import signal_digest
    signal_digest._llm_digest(_signals(), "Mobile games with Unity")
    assert all("Mobile games with Unity" in p for p in captured)
    assert not any("general relevance to software" in p for p in captured)


def test_rollup_without_user_context_is_neutral(captured):
    from jobs.handlers import signal_digest
    signal_digest._llm_rollup("## day 1\n- item\n", "", 7)
    assert captured and "general relevance to software and AI practitioners" in captured[0]
    assert all(term not in captured[0] for term in AUTHOR_TERMS)


@pytest.fixture
def context_dir(tmp_path, monkeypatch):
    from jobs import self_tune
    monkeypatch.setattr(self_tune, "CONTEXT_DIR", tmp_path)
    return tmp_path


def test_interests_come_from_user_md_and_memory(context_dir):
    from jobs import self_tune
    (context_dir / "USER.md").write_text(
        "# User\n\n- **Name:** Sam\n- **Role:** Data engineer working in Kotlin\n"
        "- **Focus:** streaming pipelines on Postgres and Kubernetes\n\n"
        "## Work Context\n\nWe ship a Django service; tests in pytest.\n\n"
        "## Pet Peeves\n\nRust evangelism.\n", encoding="utf-8")
    (context_dir / "MEMORY.md").write_text(
        "# Memory\n\n- Decided to move infra to Terraform.\n- AIDLC and launchd notes.\n",
        encoding="utf-8")
    got = self_tune.extract_interests_from_memory()
    assert {"kotlin", "postgres", "kubernetes", "django", "pytest", "terraform"} <= set(got)
    assert "rust" not in got                 # a pet peeve is not a focus line
    assert "aidlc" not in got and "launchd" not in got


def test_the_template_placeholders_teach_nothing(context_dir):
    from jobs import self_tune
    (context_dir / "USER.md").write_text(
        "- **Role:**\n\n## Work Context\n\n_(What do you do? What's your tech stack?)_\n",
        encoding="utf-8")
    assert self_tune.extract_interests_from_memory() == []


def test_no_files_no_interests(context_dir):
    from jobs import self_tune
    assert self_tune.extract_interests_from_memory() == []


def test_projects_and_memory_share_one_vocabulary(context_dir):
    from jobs import self_tune
    (context_dir / "MEMORY.md").write_text("Uses Postgres.\n", encoding="utf-8")
    projects = [{"tech_content": "We use Postgres."}]
    assert "postgres" in self_tune.extract_tech_stack_from_projects(projects)
    assert "postgres" in self_tune.extract_interests_from_memory()
