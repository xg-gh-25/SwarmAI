"""eval_followups — the eval's "what needs your attention" list (design
docs/design/2026-10-26-eval-as-product.md §3.4).

One function, ``build_follow_ups``, used by the HTML report's first section and the
dashboard's Overview, so the two never disagree. Every rule is deterministic: it
reads run records, the golden set and the online-scoring state, never a model.

Each item is ``{severity, rule, what: {en, zh}, why, action: {en, zh}, case_ids}``;
``severity`` is high / medium / low, and the list is sorted by it. An empty list
means nothing needs handling.

Rules (numbered as in the design):
  1. a quick check (BVT) failed in the latest run that ran it
  2. cases were not measured: an environment error, a timeout, or the same case
     timing out in two runs in a row (possibly really stuck)
  3. an offline case passed in the previous comparable full run and fails now
  4. online scoring: a check fails more often than the week before, low-scoring
     sessions, or the sampler failed
  5. a line is overdue: quick check > 2 days, full run > 15 days, online > 8 days
  6. a case failed in each of its last 4 comparable full runs (the case itself may
     be broken)
  7. the golden set is over its limits, a product case has no purpose, or a private
     case is hidden by a product case with the same id
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

QUICK_TRIGGERS = frozenset({"canary", "session_canary"})
_SEVERITY = {"high": 0, "medium": 1, "low": 2}
_QUICK_MAX_AGE = timedelta(days=2)
_FULL_MAX_AGE = timedelta(days=15)
_ONLINE_MAX_AGE = timedelta(days=8)
_STUCK_RUNS = 2
_BROKEN_RUNS = 4
_MAX_IDS = 10


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _first_line(text: Any, limit: int = 200) -> str:
    line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return line[:limit]


def _label(case: Mapping[str, Any] | None, cid: str, lang: str) -> str:
    purpose = (case or {}).get("purpose")
    if isinstance(purpose, Mapping) and purpose.get(lang):
        return str(purpose[lang])
    return str((case or {}).get("title") or cid)


def _item(severity, rule, what_en, what_zh, why, action_en, action_zh, case_ids=()):
    return {"severity": severity, "rule": rule, "what": {"en": what_en, "zh": what_zh},
            "why": why, "action": {"en": action_en, "zh": action_zh},
            "case_ids": list(case_ids)[:_MAX_IDS]}


def _is_full(run: Mapping[str, Any]) -> bool:
    from scripts.eval_runner import _run_is_comparable
    return _run_is_comparable(dict(run))


def _statuses(run: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {c["id"]: c for c in run.get("cases") or [] if isinstance(c, Mapping) and c.get("id")}


# ── rules ─────────────────────────────────────────────────────────────────────

def _bvt_failures(latest: Mapping[str, Any] | None, by_id) -> list[dict]:
    from scripts.eval_runner import _classify_failure

    if not latest:
        return []
    out = []
    for cid, r in _statuses(latest).items():
        if r.get("status") != "failed" or r.get("eval_method", "programmatic") != "programmatic":
            continue
        case = by_id.get(cid)
        broken = _classify_failure(r.get("notes")) == "case-broken"
        out.append(_item(
            "high", 1, f"A product check failed: {_label(case, cid, 'en')}",
            f"产品功能坏了：{_label(case, cid, 'zh')}", _first_line(r.get("notes")),
            "Check the case first: its reference looks missing." if broken
            else "Fix the behaviour, then re-run the quick check.",
            "先检查这条 case：它引用的东西像是不存在了。" if broken else "修好这个功能，再跑一次快速检查。",
            [cid]))
    return out


def _not_measured(runs: Sequence[Mapping[str, Any]], by_id) -> list[dict]:
    if not runs:
        return []
    latest = _statuses(runs[0])
    out = []
    errors = [cid for cid, r in latest.items() if r.get("status") == "error"]
    if errors:
        out.append(_item(
            "medium", 2, f"{len(errors)} case(s) were not measured (not scored)",
            f"{len(errors)} 条没测成，不计分", _first_line(latest[errors[0]].get("notes")),
            "Look at the environment error; the cases themselves were not judged.",
            "看一下环境报错；这些 case 本身没有被评判。", errors))
    timeouts = [cid for cid, r in latest.items() if r.get("status") == "timeout"]
    stuck = []
    for cid in timeouts:
        seen = [_statuses(run).get(cid) for run in runs]
        seen = [r for r in seen if r is not None][:_STUCK_RUNS]
        if len(seen) == _STUCK_RUNS and all(r.get("status") == "timeout" for r in seen):
            stuck.append(cid)
    if stuck:
        out.append(_item(
            "high", 2, f"{len(stuck)} case(s) timed out twice in a row: possibly really stuck",
            f"{len(stuck)} 条连续两次超时：可能真的卡住了", _first_line(latest[stuck[0]].get("notes")),
            "Run one of them by hand on an idle machine; a hang here is a regression.",
            "在空闲时手动跑其中一条；如果这里卡住，就是退步。", stuck))
    rest = [cid for cid in timeouts if cid not in stuck]
    if rest:
        out.append(_item(
            "medium", 2, f"{len(rest)} case(s) did not finish (not scored)",
            f"{len(rest)} 条没跑完，不计分", _first_line(latest[rest[0]].get("notes")),
            "Usually a loaded machine; if it happens again it is listed as stuck.",
            "通常是机器负载高；再出现一次会被列为卡住。", rest))
    return out


def _offline_regressions(full_runs: Sequence[Mapping[str, Any]], by_id) -> list[dict]:
    if len(full_runs) < 2:
        return []
    now, before = _statuses(full_runs[0]), _statuses(full_runs[1])
    flipped = [cid for cid, r in now.items()
               if r.get("eval_method") in ("behavior", "llm") and r.get("status") == "failed"
               and (before.get(cid) or {}).get("status") == "passed"]
    if not flipped:
        return []
    names = "; ".join(_label(by_id.get(c), c, "en") for c in flipped[:3])
    names_zh = "；".join(_label(by_id.get(c), c, "zh") for c in flipped[:3])
    return [_item("medium", 3, f"{len(flipped)} offline case(s) passed last time and fail now: {names}",
                  f"{len(flipped)} 条离线考题上次通过、这次不通过：{names_zh}",
                  _first_line(now[flipped[0]].get("notes")),
                  "Read the agent's answer in the run detail; find which rule it no longer follows.",
                  "在运行详情里看 agent 的回答，找出它不再遵守的是哪条规则。", flipped)]


def _online(state: Mapping[str, Any] | None) -> list[dict]:
    if not state:
        return []
    out = []
    if state.get("sampler_error"):
        out.append(_item("medium", 4, "This week's online scoring did not run", "这周的线上评分没评成",
                         _first_line(state["sampler_error"]),
                         "Fix the cause, then run the session-quality job once.",
                         "修好原因后，手动跑一次 session-quality 任务。"))
    trend = [t for t in state.get("trend") or [] if isinstance(t, Mapping)]
    previous = (trend[-2].get("rates") or {}) if len(trend) >= 2 else {}
    for cid, check in sorted((state.get("checks") or {}).items()):
        if not isinstance(check, Mapping):
            continue
        rate, last = check.get("rate"), previous.get(cid)
        if not isinstance(rate, (int, float)) or not isinstance(last, (int, float)) or rate <= last:
            continue
        sessions = [e.get("title") or e.get("session_id") for e in (check.get("examples") or [])[:3]
                    if isinstance(e, Mapping)]
        out.append(_item("medium", 4, f"Online check {cid} fails more often: {last:.0%} → {rate:.0%}",
                         f"线上检查 {cid} 失败变多：{last:.0%} → {rate:.0%}",
                         "; ".join(str(s) for s in sessions if s),
                         "Open the example sessions and see which rule was not kept.",
                         "打开例子里的会话，看哪条规则没守住。"))
    low = [d for d in state.get("low_details") or [] if isinstance(d, Mapping)]
    if low:
        out.append(_item("low", 4, f"{len(low)} low-scoring session(s) this week",
                         f"本周有 {len(low)} 个低分会话",
                         "; ".join(str(d.get("title") or d.get("session_id") or "") for d in low[:3]),
                         "Open them; decide whether one deserves a case.",
                         "打开看看，决定要不要据此写一条 case。"))
    return out


def _overdue(runs, full_runs, state, now: datetime) -> list[dict]:
    out = []
    quick = next((_ts(r.get("triggered_at")) for r in runs if r.get("triggered_by") in QUICK_TRIGGERS), None)
    full = next((_ts(r.get("triggered_at")) for r in full_runs), None)
    online = _ts((state or {}).get("last_run"))
    for when, limit, en, zh in ((quick, _QUICK_MAX_AGE, "The quick check", "快速检查"),
                                (full, _FULL_MAX_AGE, "The full run", "全量运行"),
                                (online, _ONLINE_MAX_AGE, "Online scoring", "线上评分")):
        if when is None or now - when > limit:
            ago = "has never run" if when is None else f"last ran {(now - when).days} days ago"
            ago_zh = "从没跑过" if when is None else f"上次是 {(now - when).days} 天前"
            out.append(_item("low", 5, f"{en} {ago}", f"{zh}{ago_zh}", "",
                             "Check its job in Jobs & Runs.", "到 Jobs & Runs 里看它的任务。"))
    return out


def _always_failing(full_runs: Sequence[Mapping[str, Any]], by_id) -> list[dict]:
    if len(full_runs) < _BROKEN_RUNS:
        return []
    window = [_statuses(r) for r in full_runs[:_BROKEN_RUNS]]
    ids = [cid for cid in window[0]
           if all((w.get(cid) or {}).get("status") == "failed" for w in window)]
    if not ids:
        return []
    return [_item("medium", 6, f"{len(ids)} case(s) failed in each of the last {_BROKEN_RUNS} full runs",
                  f"{len(ids)} 条在最近 {_BROKEN_RUNS} 次全量里都不通过",
                  ", ".join(ids[:5]), "The case itself may be broken: fix it or retire it.",
                  "这条可能本身坏了：修好或者退役。", ids)]


def _golden_set(cases: Sequence[Mapping[str, Any]], shadowed: Iterable[str]) -> list[dict]:
    from core.golden_set_store import PRODUCT_CAP, TOTAL_CAP, is_product_case

    out = []
    live = [c for c in cases if c.get("tier") != "archived"]
    product = [c for c in live if is_product_case(dict(c))]
    if len(product) > PRODUCT_CAP or len(live) > TOTAL_CAP:
        out.append(_item("low", 7, f"The golden set has {len(live)} cases (limit {TOTAL_CAP}; product "
                                   f"{len(product)}, limit {PRODUCT_CAP})",
                         f"golden set 有 {len(live)} 条（上限 {TOTAL_CAP}；产品 {len(product)}，上限 {PRODUCT_CAP}）",
                         "", "Retire your own cases that a product case already covers.",
                         "退役那些产品 case 已经覆盖的私有 case。"))
    unexplained = [c["id"] for c in product if not (c.get("purpose") or {}).get("en")]
    if unexplained:
        out.append(_item("low", 7, f"{len(unexplained)} product case(s) have no purpose",
                         f"{len(unexplained)} 条产品 case 没写目的", "",
                         "This should not ship; report it.", "这不该发布出去，请反馈。", unexplained))
    hidden = list(shadowed or [])
    if hidden:
        out.append(_item("low", 7, f"{len(hidden)} of your case(s) are hidden by a product case with the same id",
                         f"{len(hidden)} 条私有 case 和产品 case 同 id，被跳过",
                         ", ".join(hidden[:5]), "Rename or retire them.", "改名或退役它们。", hidden))
    return out


def build_follow_ups(history: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
                     online_state: Mapping[str, Any] | None = None, now: datetime | None = None,
                     shadowed: Iterable[str] = ()) -> list[dict]:
    """``history`` is the run records newest first; ``cases`` the merged golden set."""
    now = now or datetime.now(timezone.utc)
    runs = [r for r in history if isinstance(r, Mapping)]
    by_id = {c.get("id"): c for c in cases if isinstance(c, Mapping)}
    full_runs = [r for r in runs if _is_full(r)]
    # The lines a user sees: quick checks and full runs. A hand-run probe of one
    # case is neither, and must not hide what the last real run found.
    measured = [r for r in runs if r.get("triggered_by") in QUICK_TRIGGERS or _is_full(r)]
    bvt_run = next((r for r in measured if (r.get("bvt") or {}).get("total")), None)
    items = (_bvt_failures(bvt_run, by_id) + _not_measured(measured, by_id)
             + _offline_regressions(full_runs, by_id) + _online(online_state)
             + _overdue(runs, full_runs, online_state, now) + _always_failing(full_runs, by_id)
             + _golden_set(cases, shadowed))
    return sorted(items, key=lambda i: (_SEVERITY[i["severity"]], i["rule"]))


__all__ = ["QUICK_TRIGGERS", "build_follow_ups"]
