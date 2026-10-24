"""Product skills do not assume a SwarmAI source checkout (usable-by-anyone P7).

Every skill under backend/skills ships to every user, so none may point at the SwarmAI
source tree ($SWARMAI_ROOT, SWARMAI_SOURCE, ~/Desktop/SwarmAI-Workspace). SwarmAI's own
dev tooling (build, release, CI, daemon, its test contracts) lives in the SwarmAI DDD
(Projects/SwarmAI/4-capabilities), not here.
"""
import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
SOURCE_REF = re.compile(r"\$\{?SWARMAI_ROOT\b|SWARMAI_SOURCE|SwarmAI-Workspace")


def test_no_product_skill_points_at_the_swarmai_checkout():
    bad = []
    for f in sorted(SKILLS.rglob("*")):
        if not f.is_file() or f.suffix not in (".md", ".py", ".sh", ".yaml", ".json"):
            continue
        rel = f.relative_to(SKILLS)
        if "node_modules" in rel.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if SOURCE_REF.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_swarmai_dev_skills_are_not_built_in():
    for name in ("s_swarm-build", "s_swarm-release", "s_swarm-ci", "s_swarm-daemon",
                 "s_chat-brain-check", "s_health-check"):
        assert not (SKILLS / name).exists(), f"{name} belongs to the SwarmAI DDD"
