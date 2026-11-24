"""Fixed backend text written into the person's files or shown to them is English (R3.5, F4).

- Each listed module is parsed (ast) and every string literal is checked: none holds
  CJK text, except the ones named in ``EXCEPTIONS`` with the reason they stay (a heading
  or key that existing files and reports on disk still use, or text that is only matched
  or sent to a model, never shown). Docstrings and comments are not shown to anyone and
  are not checked.
- Text shown in the app's language (core/ui_language) comes in pairs of tables: the
  Chinese one is assigned to a name ending in ``_ZH`` and is allowed CJK only because a
  table of the same name ending in ``_EN`` sits next to it with the same keys and the
  same ``{placeholders}`` (``test_zh_tables_match_en_tables``).
- F4: a STEERING.md that already has the old Chinese appendix heading gets the new
  auto-extracted entry under that heading: no second appendix section.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

MODULES = (
    "core/ingestion/contracts_v2.py",
    "core/ingestion/writer.py",
    "core/context_directory_loader.py",
    "core/run_protocol/reflect_library.py",
    "core/ingestion/summary_lane.py",
    "core/ingestion/gaps.py",
    "desktop_main.py",
    "routers/memory.py",
    "core/pipeline_stage_events.py",
    "jobs/handlers/brain_mistake_audit.py",
    "core/ingestion/worker.py",
    "core/ui_language.py",
    "core/inbox/store.py",
    "core/inbox/drafts.py",
    "core/inbox/submit.py",
    "core/inbox/connect.py",
    "core/inbox/sources/github.py",
    "core/inbox/sources/mail.py",
    "core/inbox/sources/slack.py",
    "core/inbox/sources/common.py",
    "core/inbox/feeds/weibo_trending.py",
    "core/inbox/feeds/eastmoney_market.py",
    "core/output_degeneration.py",
    "core/streaming_orchestrator.py",
    "core/swarm_out_wake.py",
    "core/run_interventions.py",
    "scripts/eval_runner.py",
    "core/feature_map/runtime.py",
    "core/attention_authority.py",
    "core/swarm_out_wait_card.py",
    "core/swarm_out_follow.py",
)

# (module, the name the literal is assigned to) -> why it stays
EXCEPTIONS = {
    ("core/ingestion/contracts_v2.py", "LEGACY_SYSTEM_APPENDIX_HEADING"):
        "the old appendix heading, matched in files written before it became English",
    ("core/ingestion/contracts_v2.py", "TYPE_LABEL_ZH"):
        "type labels in the extraction prompt, read by the model only",
    ("core/run_protocol/reflect_library.py", "LESSONS"):
        "the lessons heading of the pipeline-lessons files already on disk; nothing writes "
        "new ones (write() is retired) and the readers match this exact heading",
    ("core/run_protocol/reflect_library.py", "PENDING"):
        "the pending-review heading of the same files, matched the same way",
    ("jobs/handlers/brain_mistake_audit.py", "OUTCOMES"):
        "the outcome keys stored in the monthly report JSON and read by "
        "core/ingestion/cognition_level.py and the Cognition Level panel, which shows "
        "them through i18n; the report shows OUTCOME_LABELS",
    ("core/inbox/sources/common.py", "_QUOTE_HEADERS"):
        "patterns matching the quote headers Chinese mail clients write; matched, never shown",
    ("core/inbox/sources/common.py", "_FROM_LINE"):
        "the sender line of a Chinese mail client's quoted header; matched, never shown",
    ("core/inbox/sources/common.py", "_SENT_LINE"):
        "the date line of a Chinese mail client's quoted header; matched, never shown",
    ("core/inbox/sources/mail.py", "_BANNER_WORDS"):
        "external-sender banner words in Chinese mail; matched to strip the banner, never shown",
    ("core/inbox/sources/mail.py", "_REPLY_PREFIX"):
        "the reply prefix Chinese mail clients put on a subject; matched, never shown",
    ("core/swarm_out_wake.py", "LEGACY_MARKERS"):
        "the wake markers transcripts written before they became English still start with; "
        "matched by is_wake_text, never sent again",
}

_CJK = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _assigned_names(tree: ast.AST) -> dict[int, str]:
    """id(literal node) -> the name of the assignment it sits in."""
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names = []
        for t in targets:
            names += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        if not names or node.value is None:
            continue
        for sub in ast.walk(node.value):
            out.setdefault(id(sub), names[0])
    return out


def _cjk_literals(rel: str) -> list[tuple[int, str, str]]:
    tree = ast.parse((BACKEND / rel).read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    names = _assigned_names(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if _CJK.search(node.value):
                found.append((node.lineno, names.get(id(node), ""), node.value))
    return found


def _zh_table(name: str) -> bool:
    return name.endswith("_ZH")


@pytest.mark.parametrize("rel", MODULES)
def test_no_cjk_in_fixed_text(rel):
    bad = [(line, name, text[:60]) for line, name, text in _cjk_literals(rel)
           if (rel, name) not in EXCEPTIONS and not _zh_table(name)]
    assert bad == [], f"{rel}: CJK in fixed text (line, assigned to, text): {bad}"


def _module_of(rel: str):
    import importlib
    return importlib.import_module(rel[:-3].replace("/", "."))


def _fields(value: str) -> set[str]:
    import string
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(value) if f is not None}


@pytest.mark.parametrize("rel", MODULES)
def test_zh_tables_match_en_tables(rel):
    """Every ``*_ZH`` table (the only place CJK is allowed besides EXCEPTIONS) has an
    ``*_EN`` twin with the same keys and placeholders, and the English side is English."""
    names = {n for _l, n, _t in _cjk_literals(rel) if _zh_table(n) and (rel, n) not in EXCEPTIONS}
    if not names:
        return
    mod = _module_of(rel)
    for name in names:
        zh, en = getattr(mod, name), getattr(mod, name[:-3] + "_EN", None)
        assert isinstance(zh, dict) and isinstance(en, dict), f"{rel}:{name} has no {name[:-3]}_EN table"
        assert set(zh) == set(en), f"{rel}:{name} keys differ from its _EN table"
        for key in zh:
            assert _fields(zh[key]) == _fields(en[key]), f"{rel}:{name}[{key}] placeholders differ"
            assert not _CJK.search(en[key]), f"{rel}:{name[:-3]}_EN[{key}] is not English"


def test_every_exception_is_still_needed():
    for rel, name in EXCEPTIONS:
        assert any(n == name for _l, n, _t in _cjk_literals(rel)), f"stale exception {rel}:{name}"


def test_appendix_headings():
    from core.ingestion import contracts_v2
    assert contracts_v2.SYSTEM_APPENDIX_HEADING == "## System appendix (auto-extracted)"
    assert contracts_v2.LEGACY_SYSTEM_APPENDIX_HEADING == "## 系统追加（自动提取）"


def test_f4_legacy_appendix_gets_the_new_entry(tmp_path):
    from core.ingestion import contracts_v2
    from core.ingestion.writer import _append_system_appendix

    legacy = contracts_v2.LEGACY_SYSTEM_APPENDIX_HEADING
    content = ("# Steering\n\n## Rules\n\n- Owner rule.\n\n"
               f"{legacy}\n\n### Standing Rules\n\n- Old extracted rule.\n")
    out = _append_system_appendix(content, "STEERING", None, "- New extracted rule.")

    assert out.count(legacy) == 1
    assert contracts_v2.SYSTEM_APPENDIX_HEADING not in out
    assert out.count("### Standing Rules") == 1
    appendix = out.split(legacy, 1)[1]
    assert appendix.index("- Old extracted rule.") < appendix.index("- New extracted rule.")
    assert out.startswith("# Steering\n\n## Rules\n\n- Owner rule.\n")


def test_new_file_gets_the_english_heading():
    from core.ingestion import contracts_v2
    from core.ingestion.writer import _append_system_appendix

    out = _append_system_appendix("# Steering\n", "STEERING", None, "- Rule.")
    assert out.count(contracts_v2.SYSTEM_APPENDIX_HEADING) == 1
    assert contracts_v2.LEGACY_SYSTEM_APPENDIX_HEADING not in out


def test_find_system_appendix_knows_both_headings():
    from core.ingestion import contracts_v2
    for heading in (contracts_v2.SYSTEM_APPENDIX_HEADING, contracts_v2.LEGACY_SYSTEM_APPENDIX_HEADING):
        text = f"# T\n\n- a\n\n{heading}\n\n### X\n\n- b\n"
        match = contracts_v2.find_system_appendix(text)
        assert match is not None and text[match.start():].startswith(heading)
    assert contracts_v2.find_system_appendix("# T\n\n## Other\n") is None


def test_loader_splits_a_legacy_appendix():
    """A file written before the heading became English still has its appendix split
    off by the context loader (owner text vs CDF-managed text)."""
    from core.context_directory_loader import split_system_appendix
    from core.ingestion.contracts_v2 import LEGACY_SYSTEM_APPENDIX_HEADING
    owner = "# Steering\n\nmy rule\n\n"
    appendix = LEGACY_SYSTEM_APPENDIX_HEADING + "\n\n- [guideline] x\n"
    assert split_system_appendix(owner + appendix) == (owner, appendix)
