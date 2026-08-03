"""S1 (READ-line finalize, run_2f621986): token-estimator unification +
first-message context instrumentation.

Covers design 2026-06-28-context-observability-and-vector-teardown §1 (kill the
dual estimator) + §2 (first-msg total-context + recall_ms logging).

Method note (DoD1, honest scope): no offline Claude/cl100k tokenizer is bundled
(zero-network box), so we do NOT hardcode a *second* guessed CJK coefficient to
replace `÷1.5` — that would swap one unmeasured constant for another (the exact
R16b/CLASS-B trap). Instead: (1) unify on the single CJK-aware estimator and
prove the crude `len//4` is gone from the recall path; (2) a calibration test
that RUNS only when a real tokenizer is importable (CI/network) and is SKIPPED
offline — so the coefficient gets verified where it can be, never guessed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.context_directory_loader import ContextDirectoryLoader as C

_SR = Path(__file__).resolve().parents[1] / "core" / "session_router.py"


def test_recall_path_has_no_len_div_4_estimator():
    """The dual-estimator divergence is structurally gone: the recall logging
    no longer uses the crude `len(recalled) // 4`. A regression that re-introduces
    a second estimator on this path fails here."""
    src = _SR.read_text(encoding="utf-8")
    assert "len(recalled) // 4" not in src, (
        "recall path re-introduced the crude len//4 estimator — must use the "
        "single CJK-aware estimate_tokens (design §1)"
    )
    assert "len(recalled)//4" not in src


def test_recall_path_uses_estimate_tokens():
    """The recall path imports + uses the single estimator."""
    src = _SR.read_text(encoding="utf-8")
    assert "ContextDirectoryLoader.estimate_tokens" in src
    # the first-msg total-context instrumentation line exists
    assert "first-msg context assembled" in src
    assert "_recall_ms" in src and "recall_leg" in src


def test_estimate_tokens_is_single_source():
    """estimate_tokens stays a pure deterministic fn (signature unchanged so all
    163 callers are safe). Same input → same output, no global state."""
    s = "我们系统的设计哲学是纯文件系统召回 recall keyword FTS5"
    a = C.estimate_tokens(s)
    b = C.estimate_tokens(s)
    assert a == b and a > 0


def test_estimate_tokens_cjk_nonzero_and_monotonic():
    """CJK text must estimate > word-split-of-1 (the bug pure word-split caused),
    and more CJK chars → more tokens (monotonic). This guards the *property* the
    coefficient must satisfy without asserting the exact (unmeasured) coefficient."""
    one = C.estimate_tokens("设计")
    many = C.estimate_tokens("设计" * 50)
    assert many > one > 0
    # a 100-char CJK paragraph must NOT collapse to ~1 token (pure word-split bug)
    assert C.estimate_tokens("中" * 100) >= 40


def _real_tokenizer():
    """Return a callable str->int from any importable real tokenizer, or None."""
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return lambda s: len(enc.encode(s))
    except Exception:
        return None


@pytest.mark.skipif(_real_tokenizer() is None,
                    reason="no offline tokenizer (zero-network box) — calibration "
                           "runs in CI/network where a real tokenizer is importable")
def test_estimate_tokens_calibrated_against_real_tokenizer():
    """When a real tokenizer IS available, the estimator must be within a sane
    band of truth (0.5x–2x) across CJK / mixed / English. This is the gate that
    catches a miscalibrated coefficient — it RUNS where truth exists, instead of
    hardcoding a guess where it doesn't (DoD1)."""
    real = _real_tokenizer()
    samples = [
        "我们系统的设计哲学是纯文件系统召回不依赖向量数据库",
        "recall 用 keyword/FTS5 召回, 不走 Titan embedding, pure-filesystem",
        "the quick brown fox jumps over the lazy dog and runs away fast today",
    ]
    for s in samples:
        est = C.estimate_tokens(s)
        truth = real(s)
        assert 0.5 * truth <= est <= 2.0 * truth, (
            f"estimate {est} off real {truth} for {s!r} — recalibrate CJK coeff"
        )
