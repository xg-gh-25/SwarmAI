"""Cognition templates say what the product does (design
2026-10-24-swarmai-usable-by-anyone.md §3.6, §5.2).

Every template in CONTEXT_FILES (system files and copy-only-if-missing seeds alike):
- no wording that makes the pipeline mandatory or review-before-every-commit a rule;
- no removed gate named as if it still runs;
- the EVOLUTION seed carries no correction / optimization / competence entries.
"""
import re
from pathlib import Path

from core.context_directory_loader import CONTEXT_FILES

CTX = Path(__file__).resolve().parents[1] / "context"

FORBIDDEN = [
    r"NO CODE CHANGE WITHOUT PIPELINE",
    r"NO COMMIT WITHOUT ADVERSARIAL",
    r"ONLY route for code",
    r"Pipeline is mandatory",
    r"Every code change goes through\s+`?s_autonomous-pipeline",
    r"Adversarial review before EVERY commit",
    r"Pipeline is unconditional default",
    r"adversarial review mandatory",
    r"\b(direct_commit_gate|adversarial_commit_gate|pytest_command_guard)\b",
]


def _templates():
    for spec in CONTEXT_FILES:
        path = CTX / spec.filename
        if path.is_file():
            yield spec.filename, path.read_text(encoding="utf-8")


def test_no_template_makes_the_pipeline_or_commit_review_mandatory():
    hits = [(name, pat) for name, text in _templates() for pat in FORBIDDEN
            if re.search(pat, text, re.IGNORECASE)]
    assert not hits, hits


def test_evolution_seed_has_no_personal_entries():
    text = (CTX / "EVOLUTION.md").read_text(encoding="utf-8")
    assert not re.search(r"^### [COK]\d{3}\b", text, re.MULTILINE)
