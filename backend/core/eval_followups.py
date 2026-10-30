"""eval_followups — the eval's "what needs your attention" list (design
docs/design/2026-10-26-eval-as-product.md §3.4).

One function, ``build_follow_ups``, used by the HTML report's first section and the
dashboard's Overview, so the two never disagree. Every rule is deterministic: it
reads run records, the golden set and the online-scoring state, never a model.

Each item is ``{kind, severity, rule, what: {en, zh}, why, action: {en, zh}, case_ids}``;
``severity`` is high / medium / low, and the list is sorted by it. An empty list
means nothing needs handling. ``kind`` splits the list in two (design §8 item 2):
``eval`` is a problem with the evaluation itself (a check broke, a run did not
measure, a case looks wrong, a line is overdue) and goes under "Needs you";
``evolution`` is a signal about the agent's behaviour (it got worse, the user
corrected it, a judge scored a session low) and goes under Evolution Suggestion,
the input of the improve loop. Nothing here changes a rule or writes a case.

Rules (numbered as in the design):
  1. a quick check (BVT) failed in the latest run that ran it
  2. cases were not measured: an environment error, a timeout, or the same case
     timing out in two runs in a row (possibly really stuck)
  3. an offline case passed in the previous comparable full run and fails now, or a
     new offline case (no result in that run) fails on its first full run
  4. online scoring: a check fails more often than the week before, low-scoring
     sessions, or the sampler failed
  5. a line is overdue: quick check > 2 days, full run > 15 days, online > 8 days
  6. a case failed in each of its last 4 comparable full runs (the case itself may
     be broken)
  7. the golden set is over its limits, a product case has no purpose, or a private
     case is hidden by a product case with the same id
  8. (evolution) an online check is kept in fewer than 90% of turns or sessions, or
     the user corrected the agent in any session this week; a check already listed
     by rule 4 as worse than last week is not listed twice

Inputs: the run records (EvalHistory), the daily canary state (the per-session
quick check, which writes no run record) and the online-scoring state. From the
online state only these fields are read, each bounded: a check id of the form
``O<n>_NAME``, its ``rate``, up to 3 example sessions' ``title`` (80 chars) or
``session_id``, the low sessions' ``title`` / ``session_id``, the type name of a
``sampler_error``, and ``trend[-2].rates``: the writer appends this week's entry to
``trend`` before it writes the file, so ``trend[-2]`` is the week before. Snippets
and judge reasons never leave the state file through here. With
``include_session_text=False`` (the HTML report, a file on disk) no session title
or id is included at all.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

QUICK_TRIGGERS = frozenset({"canary", "session_canary", "deploy"})
_ONLINE_CHECK_ID = re.compile(r"^O[1-9]_[A-Z_]{1,30}$")
_TITLE_MAX = 80
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
    """A case's short name in a follow-up line: its title (English only), else the
    first sentence of its purpose in ``lang`` (what it protects, without what breaks),
    else the id. A follow-up names up to three cases in one line, so the whole
    two-sentence purpose made the line unreadable; the full purpose is on the case."""
    case = case or {}
    if lang == "en" and case.get("title"):
        return str(case["title"])
    purpose = case.get("purpose")
    if isinstance(purpose, Mapping) and purpose.get(lang):
        text = str(purpose[lang]).strip()
        m = re.match(r"(.+?(?:[。！？]|[.!?](?=\s|$)))", text, re.S)
        return m.group(1) if m else text
    return str(case.get("title") or cid)


EVAL, EVOLUTION = "eval", "evolution"
_STANDING_RATE = 0.10  # rule 8: a check that fails in at least this share is a signal
# Plain names of the online checks (jobs/handlers/session_quality.CHECKS).
ONLINE_CHECK_NAMES = {
    "O1_LANGUAGE": ("Answers in the language you asked in", "用你提问的语言回答"),
    "O2_SILENT_TOOLS": ("No extra text before a tool call", "调工具前不写多余的话"),
    "O3_CORRECTIONS": ("Sessions you did not have to correct", "没被你纠正过的会话"),
    "O4_GOAL": ("Delivered what you asked (judge)", "交付了你要的东西（judge）"),
    "O5_TOOLS": ("Chose reasonable tools (judge)", "工具选得合理（judge）"),
}


def _check_name(cid: str, lang: str) -> str:
    names = ONLINE_CHECK_NAMES.get(cid)
    return names[0 if lang == "en" else 1] if names else cid


def _item(severity, rule, what_en, what_zh, why, action_en, action_zh, case_ids=(), kind=EVAL,
          headline=None):
    """One follow-up. ``what`` is the full line (the report prints it); ``headline`` is
    the same line without the case names, for a list that shows the names on their
    own line; it is ``what`` when the line names no cases."""
    head_en, head_zh = headline or (what_en, what_zh)
    return {"kind": kind, "severity": severity, "rule": rule, "what": {"en": what_en, "zh": what_zh},
            "headline": {"en": head_en, "zh": head_zh},
            "why": why, "action": {"en": action_en, "zh": action_zh},
            "case_ids": list(case_ids)[:_MAX_IDS]}


def _cases_en(n: int, noun: str = "case") -> str:
    """``1 case`` / ``2 cases``: a follow-up line is read, so no ``case(s)``."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _were(n: int) -> str:
    return "was" if n == 1 else "were"


def _have(n: int) -> str:
    return "has" if n == 1 else "have"


def _is_full(run: Mapping[str, Any]) -> bool:
    from scripts.eval_runner import _run_is_comparable
    return _run_is_comparable(dict(run))


def run_line(run: Mapping[str, Any]) -> str:
    """Which line a run belongs to: ``quick`` (a quick check), ``probe`` (a hand-run
    subset, e.g. "re-run this case") or ``full``. Only quick and full runs say
    anything about a release: a probe's denominator is the few cases it ran, so its
    verdict must never stand for the version. What makes a probe is its SCOPE (run
    limited by case ids / tags, or a pre-flag record under 20 cases), never its
    results: a full run where many cases errored is still the full run, and its
    blocked or incomplete verdict must show. A crashed full run (``status: failed``,
    no cases) is full too. Whether a full run is comparable on a trend or for
    regressions is a separate question (``_is_full``). Health, history, the lines
    and the follow-ups' "measured" set all use this one predicate."""
    if run.get("triggered_by") in QUICK_TRIGGERS:
        return "quick"
    if run.get("filtered"):
        return "probe"
    if run.get("status") == "failed":
        return "full"
    return "full" if (run.get("total_cases") or 0) >= 20 else "probe"


def _statuses(run: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {c["id"]: c for c in run.get("cases") or [] if isinstance(c, Mapping) and c.get("id")}


# ── rules ─────────────────────────────────────────────────────────────────────

def _bvt_failures(latest: Mapping[str, Any] | None, by_id) -> list[dict]:
    from core.golden_set_store import is_product_case
    from scripts.eval_runner import _classify_failure, bvt_member

    if not latest:
        return []
    out = []
    for cid, r in _statuses(latest).items():
        case = by_id.get(cid)
        # the same membership compute_bvt uses: a failure outside the BVT is not
        # "a quick check failed"
        if r.get("status") != "failed" or case is None or not bvt_member(dict(case)):
            continue
        broken = _classify_failure(r.get("notes")) == "case-broken"
        product = is_product_case(dict(case))
        out.append(_item(
            "high", 1,
            f"{'A product check' if product else 'A quick check'} failed: {_label(case, cid, 'en')}",
            f"{'产品功能坏了' if product else '快速检查失败'}：{_label(case, cid, 'zh')}",
            _first_line(r.get("notes")),
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
            "medium", 2, f"{_cases_en(len(errors))} {_were(len(errors))} not measured (not scored)",
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
            "high", 2, f"{_cases_en(len(stuck))} timed out twice in a row: possibly really stuck",
            f"{len(stuck)} 条连续两次超时：可能真的卡住了", _first_line(latest[stuck[0]].get("notes")),
            "Run one of them by hand on an idle machine; a hang here is a regression.",
            "在空闲时手动跑其中一条；如果这里卡住，就是退步。", stuck))
    rest = [cid for cid in timeouts if cid not in stuck]
    if rest:
        out.append(_item(
            "medium", 2, f"{_cases_en(len(rest))} did not finish (not scored)",
            f"{len(rest)} 条没跑完，不计分", _first_line(latest[rest[0]].get("notes")),
            "Usually a loaded machine; if it happens again it is listed as stuck.",
            "通常是机器负载高；再出现一次会被列为卡住。", rest))
    return out


def _offline_regressions(full_runs: Sequence[Mapping[str, Any]], by_id) -> list[dict]:
    if not full_runs:
        return []
    now = _statuses(full_runs[0])
    before = _statuses(full_runs[1]) if len(full_runs) > 1 else {}
    failing = [cid for cid, r in now.items()
               if r.get("eval_method") in ("behavior", "llm") and r.get("status") == "failed"]
    # Failing in both runs is not news (rule 6 lists a case that keeps failing).
    flipped = [c for c in failing if (before.get(c) or {}).get("status") == "passed"]
    new = [c for c in failing if c not in before]
    out = []

    def names(ids, lang, sep):
        return sep.join(_label(by_id.get(c), c, lang) for c in ids[:3])
    if flipped:
        out.append(_item("medium", 3, f"{_cases_en(len(flipped), 'offline case')} passed last time and fail{'s' if len(flipped) == 1 else ''} now: "
                                      f"{names(flipped, 'en', '; ')}",
                         f"{len(flipped)} 条离线考题上次通过、这次不通过：{names(flipped, 'zh', '；')}",
                         _first_line(now[flipped[0]].get("notes")),
                         "Read the agent's answer in the run detail; find which rule it no longer follows.",
                         "在运行详情里看 agent 的回答，找出它不再遵守的是哪条规则。", flipped, kind=EVOLUTION,
                         headline=(f"{_cases_en(len(flipped), 'offline case')} passed last time and fail{'s' if len(flipped) == 1 else ''} now",
                                   f"{len(flipped)} 条离线考题上次通过、这次不通过")))
    if new:
        out.append(_item("medium", 3, f"{_cases_en(len(new), 'new offline case')} fail{'s' if len(new) == 1 else ''} on the first run: "
                                      f"{names(new, 'en', '; ')}",
                         f"{len(new)} 条新离线考题第一次运行没通过：{names(new, 'zh', '；')}",
                         _first_line(now[new[0]].get("notes")),
                         "Check the case first (can the right answer be reached in the sample "
                         "workspace?), then the agent's answer.",
                         "先检查 case 本身（样例工作区里能不能做出正确答案），再看 agent 的回答。", new,
                         headline=(f"{_cases_en(len(new), 'new offline case')} fail{'s' if len(new) == 1 else ''} on the first run",
                                   f"{len(new)} 条新离线考题第一次运行没通过")))
    return out


def _consecutive_weeks(before: Any, after: Any) -> bool:
    """ISO-week labels like 2026-W43 / 2026-W44 one week apart."""
    try:
        b = datetime.strptime(f"{before}-1", "%G-W%V-%u")
        a = datetime.strptime(f"{after}-1", "%G-W%V-%u")
    except (TypeError, ValueError):
        return False
    return a - b == timedelta(days=7)


def _session_label(entry: Any) -> str:
    if not isinstance(entry, Mapping):
        return ""
    return str(entry.get("title") or entry.get("session_id") or "")[:_TITLE_MAX]


def _online(state: Mapping[str, Any] | None, include_session_text: bool) -> list[dict]:
    if not state:
        return []
    out = []
    if state.get("sampler_error"):
        # the exception's type only: its message can carry paths or session text
        kind = str(state["sampler_error"]).split(":", 1)[0].strip()[:60]
        out.append(_item("medium", 4, "This week's online scoring did not run", "这周的线上评分没评成",
                         kind,
                         "Fix the cause, then run the session-quality job once.",
                         "修好原因后，手动跑一次 session-quality 任务。"))
    if state.get("judge_error"):
        out.append(_item("medium", 4, "The online judge did not run this week (goal and tool checks)",
                         "这周线上评分的 judge 没跑成（目标和工具两项）",
                         str(state["judge_error"]).split(":", 1)[0][:80],
                         "Check the model credentials, then run the session-quality job once.",
                         "检查模型凭证，然后手动跑一次 session-quality 任务。"))
    trend = [t for t in state.get("trend") or [] if isinstance(t, Mapping)]
    # "last week" only when the entry before this one IS the week before
    previous = (trend[-2].get("rates") or {}) if len(trend) >= 2 and _consecutive_weeks(
        trend[-2].get("week"), trend[-1].get("week")) else {}
    checks = state.get("checks") if isinstance(state.get("checks"), Mapping) else {}
    worse = set()
    for cid, check in sorted(checks.items()):
        if not isinstance(check, Mapping) or not _ONLINE_CHECK_ID.match(str(cid)):
            continue
        rate, last = check.get("rate"), previous.get(cid)
        if not isinstance(rate, (int, float)) or not isinstance(last, (int, float)) or rate <= last:
            continue
        worse.add(cid)
        sessions = [_session_label(e) for e in (check.get("examples") or [])[:3]] \
            if include_session_text else []
        out.append(_item("medium", 4, f"{_check_name(cid, 'en')} fails more often: {last:.0%} → {rate:.0%}",
                         f"「{_check_name(cid, 'zh')}」失败变多：{last:.0%} → {rate:.0%}",
                         "; ".join(s for s in sessions if s),
                         "Open the example sessions and see which rule was not kept.",
                         "打开例子里的会话，看哪条规则没守住。", kind=EVOLUTION))
    out += _standing(checks, worse, include_session_text)
    low = [d for d in state.get("low_details") or [] if isinstance(d, Mapping)]
    if low:
        out.append(_item("low", 4, f"{_cases_en(len(low), 'session')} the judge scored low this week",
                         f"本周有 {len(low)} 个会话被 judge 打了低分",
                         "; ".join(_session_label(d) for d in low[:3]) if include_session_text else "",
                         "Open them; decide whether one deserves a case or a rule change.",
                         "打开看看，决定要不要据此写一条 case 或改规则。", kind=EVOLUTION))
    return out


def _standing(checks: Mapping[str, Any], worse: set, include_session_text: bool) -> list[dict]:
    """Rule 8: this week's online signals worth a look even when not worse than last
    week. Corrections are the user's own words, so any one counts."""
    out = []
    for cid, check in sorted(checks.items()):
        if cid in worse or not isinstance(check, Mapping) or not _ONLINE_CHECK_ID.match(str(cid)):
            continue
        rate, failed, checked = check.get("rate"), check.get("failed"), check.get("checked")
        if not isinstance(rate, (int, float)) or not isinstance(failed, int) or not isinstance(checked, int):
            continue
        sessions = "; ".join(s for s in (_session_label(e) for e in (check.get("examples") or [])[:3]) if s) \
            if include_session_text else ""
        if cid == "O3_CORRECTIONS":
            if failed > 0:
                out.append(_item("medium", 8, f"You corrected the agent in {failed} of {_cases_en(checked, 'session')} this week",
                                 f"本周 {checked} 个会话里，你在 {failed} 个纠正过它", sessions,
                                 "Each correction is where you said it was wrong: decide whether it needs a rule or a case.",
                                 "每次纠正都是你说过“不对”的地方：决定要改规则，还是加一条 case。",
                                 kind=EVOLUTION))
        elif cid in ("O1_LANGUAGE", "O2_SILENT_TOOLS") and rate >= _STANDING_RATE:
            out.append(_item("low", 8, f"“{_check_name(cid, 'en')}” is kept in {1 - rate:.0%} ({checked - failed}/{checked})",
                             f"「{_check_name(cid, 'zh')}」只有 {1 - rate:.0%} 做到（{checked - failed}/{checked}）",
                             sessions, "The rule exists but is not a habit yet: look at the examples.",
                             "规则有了，但还没变成习惯：看看例子。", kind=EVOLUTION))
    return out


def _overdue(runs, full_runs, state, now: datetime) -> list[dict]:
    out = []
    quick = next((_ts(r.get("triggered_at")) for r in runs if run_line(r) == "quick"), None)
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
    return [_item("medium", 6, f"{_cases_en(len(ids))} failed in each of the last {_BROKEN_RUNS} full runs",
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
        out.append(_item("low", 7, f"{_cases_en(len(unexplained), 'product case')} {_have(len(unexplained))} no purpose",
                         f"{len(unexplained)} 条产品 case 没写目的", "",
                         "This should not ship; report it.", "这不该发布出去，请反馈。", unexplained))
    hidden = list(shadowed or [])
    if hidden:
        out.append(_item("low", 7, f"{len(hidden)} of your cases {'is' if len(hidden) == 1 else 'are'} hidden by a product case with the same id",
                         f"{len(hidden)} 条私有 case 和产品 case 同 id，被跳过",
                         ", ".join(hidden[:5]), "Rename or retire them.", "改名或退役它们。", hidden))
    return out


def _daily_canary_run(state: Mapping[str, Any] | None) -> dict | None:
    """The per-session daily canary (context_health_hook) as a quick-check run: it
    writes a state file, not a run record."""
    results = (state or {}).get("results")
    if not isinstance(results, Mapping) or not results:
        return None
    return {"triggered_by": "session_canary", "triggered_at": state.get("updated_at"),
            "bvt": {"total": len(results)},
            "cases": [{"id": cid, "status": r.get("status"), "notes": r.get("notes", ""),
                       "eval_method": "programmatic"}
                      for cid, r in results.items() if isinstance(r, Mapping)]}


def build_follow_ups(history: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]],
                     online_state: Mapping[str, Any] | None = None, now: datetime | None = None,
                     shadowed: Iterable[str] = (), daily_canary: Mapping[str, Any] | None = None,
                     include_session_text: bool = True) -> list[dict]:
    """``history`` is the run records; ``cases`` the merged golden set;
    ``daily_canary`` the daily canary state."""
    now = now or datetime.now(timezone.utc)
    runs = [r for r in history if isinstance(r, Mapping)]
    canary = _daily_canary_run(daily_canary)
    if canary:
        runs.append(canary)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    runs.sort(key=lambda r: _ts(r.get("triggered_at")) or oldest, reverse=True)
    by_id = {c.get("id"): c for c in cases if isinstance(c, Mapping)}
    full_runs = [r for r in runs if _is_full(r)]  # comparable: regressions and the always-failing rule
    # The lines a user sees: quick checks and full runs. A hand-run probe of one
    # case is neither, and must not hide what the last real run found.
    measured = [r for r in runs if run_line(r) != "probe"]
    bvt_run = next((r for r in measured if (r.get("bvt") or {}).get("total")), None)
    items = (_bvt_failures(bvt_run, by_id) + _not_measured(measured, by_id)
             + _offline_regressions(full_runs, by_id) + _online(online_state, include_session_text)
             + _overdue(runs, full_runs, online_state, now) + _always_failing(full_runs, by_id)
             + _golden_set(cases, shadowed))
    return sorted(items, key=lambda i: (_SEVERITY[i["severity"]], i["rule"]))


__all__ = ["QUICK_TRIGGERS", "build_follow_ups", "run_line"]
