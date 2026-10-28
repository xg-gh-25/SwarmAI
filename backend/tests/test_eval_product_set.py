"""The product golden set itself: shippable to every user (design §3.1, §3.2)."""
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.golden_set_store import PRODUCT_CAP, product_golden_set_path  # noqa: E402
from scripts.golden_case_validator import _is_gate_eligible, compute_case_stamp, gate_purpose  # noqa: E402

# Things only one machine, one user or one source checkout has.
_INSTANCE = re.compile(r"/Users/|Desktop/|\.venv/|cd backend|python backend/|Projects/|\.context/"
                       r"|SwarmAI-Workspace|gawan", re.I)
# A path into the fixture workspace an offline case runs in ships with the product, so
# it is not one machine's: a `Projects/...` path that exists there is allowed.
_FIXTURE_WS = Path(__file__).resolve().parents[1] / "templates" / "eval" / "fixtures" / "workspace"
_FIXTURE_PATH = re.compile(r"Projects/[\w.-]+(?:/[\w.-]+)*")


def _without_fixture_paths(text: str) -> str:
    def _keep(m: re.Match) -> str:
        rel = m.group(0)
        shipped = ".." not in rel.split("/") and (_FIXTURE_WS / rel).exists()
        return "" if shipped else rel
    return _FIXTURE_PATH.sub(_keep, text)


def _cases():
    return yaml.safe_load(product_golden_set_path().read_text(encoding="utf-8"))["cases"]


def test_product_set_stays_small():
    assert 0 < len(_cases()) <= PRODUCT_CAP


def test_every_case_says_why_it_exists_in_both_languages():
    for c in _cases():
        ok, errs = gate_purpose(c, require_both=True)
        assert ok, f"{c['id']}: {errs}"


def test_no_case_depends_on_one_machine_or_checkout():
    for c in _cases():
        body = yaml.safe_dump({k: v for k, v in c.items() if k not in ("affected_by", "purpose")})
        hit = _INSTANCE.search(_without_fixture_paths(body))
        assert not hit, f"{c['id']}: instance-specific '{hit.group(0)}'"


def test_only_a_path_the_fixture_workspace_has_is_allowed():
    shipped = "Projects/Tokensign/.artifacts/bootstrap/survey.json"
    assert (_FIXTURE_WS / shipped).is_file()
    assert not _INSTANCE.search(_without_fixture_paths(f"read `{shipped}` first"))
    for live in ("Projects/SwarmAI/2-understanding/TECH.md", "Projects/Tokensign/../../../README.md"):
        assert _INSTANCE.search(_without_fixture_paths(f"read `{live}` first")), live


def test_commands_use_the_backend_program():
    for c in _cases():
        v = c.get("verification") or {}
        for key in ("command", "negative_command"):
            cmd = v.get(key)
            if cmd:
                assert cmd.startswith('"$SWARM_BACKEND_BIN" py -m scripts.'), f"{c['id']}.{key}: {cmd}"


def test_gate_eligible_cases_have_runtime_teeth_and_a_current_stamp():
    for c in _cases():
        if not _is_gate_eligible(c):
            continue
        v = c.get("verification") or {}
        assert v.get("negative_command") and v.get("negative_expected_contains"), c["id"]
        assert c.get("validated_by_4gate") == compute_case_stamp(c), (
            f"{c['id']}: stale stamp — re-stamp it in templates/eval/golden_set.yaml")


def test_ids_are_unique():
    ids = [c["id"] for c in _cases()]
    assert len(ids) == len(set(ids))
