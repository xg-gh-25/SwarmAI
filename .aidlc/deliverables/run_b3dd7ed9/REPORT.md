# Autonomous Pipeline Report: Thinking controls follow what the model accepts — one rule for the wire and the Settings UI

**Run ID:** run_b3dd7ed9 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-10-17 10:17 UTC | **Duration:** 53.9 min (in progress) | **Tokens:** 180,000
**Gate 1:** WARN | **Files:** 8 | **Tests:** True/1

## TL;DR
Settings thinking controls must reflect what the default model actually accepts: opus>=5 rejects thinking_mode disabled/enabled and prompt_builder silently downgrades them to adaptive, while (a) Setti

## 1. Requirement
Settings thinking controls must reflect what the default model actually accepts: opus>=5 rejects thinking_mode disabled/enabled and prompt_builder silently downgrades them to adaptive, while (a) Settings → AIModelsTab still offers Enabled/Disabled and shows the stored mode as if active, and (b) on that downgrade _build_effort/_build_thinking_display still read the raw 'disabled' config and return None, so the adaptive session runs with no effort and no --thinking-display. Fix: backend computes the effective thinking mode once per model (registry predicate) and both prompt_builder (effort/display) and GET/PUT /api/settings (supported_thinking_modes + effective_thinking_mode) use it; the Settings UI offers only supported modes and states when the stored mode is not in effect. Stored config is not rewritten.

## 需求溯源审计 (Requirement Provenance Audit)

### 原始意图 (Original Intent)
> Settings thinking controls must reflect what the default model actually accepts: opus>=5 rejects thinking_mode disabled/enabled and prompt_builder silently downgrades them to adaptive, while (a) Settings → AIModelsTab still offers Enabled/Disabled and shows the stored mode as if active, and (b) on that downgrade _build_effort/_build_thinking_display still read the raw 'disabled' config and return None, so the adaptive session runs with no effort and no --thinking-display. Fix: backend computes the effective thinking mode once per model (registry predicate) and both prompt_builder (effort/display) and GET/PUT /api/settings (supported_thinking_modes + effective_thinking_mode) use it; the Settings UI offers only supported modes and states when the stored mode is not in effect. Stored config is not rewritten.

### 需求推导 (Derivation)
## Requirements

| ID | work_class | source | status | AC present | Acceptance (GWT) |
|----|-----------|--------|--------|:---------:|------------------|
| FR-1 | bug | user-stated: XG 2026-10-17: 修第2条 | confirmed | ● | Given thinking_mode=disabled, model claude-opus-5-5, when build_options, then thinking adaptive, effort == configured effort, --thinking-display set |
| FR-2 | user-story | user-stated: XG 2026-10-17: 修第2条 | confirmed | ● | Given default model claude-opus-5-5, stored disabled, when Settings opens, then only Adaptive offered, Adaptive selected, note explains Disabled is not supported by this model |

### 澄清问答 (Clarifications — who decided what)
| Q | 问题 (question) | 回答 (answer) | 答者 (answered_by) |
|---|------------------|---------------|---------------------|
| Q1 | Reject disabled/enabled on PUT for opus>=5, or keep stored value? | A. Keep stored value, compute effective | 🧠 self |
| Q2 | Hide unsupported options or show them disabled? | A. Hide + note | 🧠 self |

### ⚠️ 遗留 / 未闭合 (Leftover — what the AI decided for you)
- [SELF-ANSWERED] Q1: A. Keep stored value, compute effective (🧠 AI 替你拍的板 — resolution: Gate-0: the session model can differ from the default (agent override) and the default can change later; a write-time rejection still leaves the runtime case, and the stored choice becomes valid again if the user switches back to a model that supports it.)
- [SELF-ANSWERED] Q2: A. Hide + note (🧠 AI 替你拍的板 — resolution: The shared Dropdown has no per-option disabled state; a one-line note carries the explanation without changing a shared component.)

## 2. Evaluation
| Dimension | Score | Recommendation |
|---|---|---|
| Strategic | 6.00 | |
| Priority | 6.00 | |
| Feasibility | 8.00 | |

**Acceptance Criteria:**
1. AC1: model_registry.supported_thinking_modes(model) -> ['adaptive'] for opus>=5, ['adaptive','enabled','disabled'] otherwise; effective_thinking_mode(stored, model) -> stored if supported else 'adaptive'.
2. AC2: prompt_builder.build_options for an opus>=5 model with stored thinking_mode='disabled' produces thinking {'type':'adaptive'} AND a non-None effort AND the thinking-display extra arg (same as a stored 'adaptive').
3. AC3: prompt_builder for opus-4-8 with stored 'disabled' still produces disabled thinking, effort None, no display (unchanged).
4. AC4: GET and PUT /api/settings responses include supported_thinking_modes and effective_thinking_mode computed for default_model.
5. AC5: AIModelsTab offers only supportedThinkingModes, selects effectiveThinkingMode, and shows a one-line note when the stored mode differs from the effective one; falls back to all three modes if the backend omits the field.
6. AC6: stored thinking_mode in config.json is never rewritten by this logic.


## 3. Design & Approach
**Research Findings (THINK):**
- model_registry.supports_disabled_thinking(model) is the single family predicate; prompt_builder._guard_thinking_for_model already uses it at the call site where the resolved model is known.
- _build_effort and _build_thinking_display read self._config['thinking_mode'] directly; they have no model input — the downgrade happens after they could have seen it.
- settings router responses are plain dicts transformed snake->camel generically by desktop settingsService.transformKeys — new top-level keys reach the UI with no service change beyond the TS type.
- AIModelsTab's Dropdown has no per-option disabled state (shared component).

**Alternatives Evaluated:**
- SIMPLICITY — effort: S-M
- QUALITY — effort: M
- DELETION — effort: S

**Chosen Approach:** Single effective-mode authority in model_registry; prompt_builder effort/display take the effective mode; settings response exposes supported + effective for default_model; AIModelsTab renders from them.

**Success Criteria:**
- pytest thinking-guard + settings router green
- vitest AIModelsTab thinking test green
- tsc --noEmit clean
- mutation: revert effective-mode in _build_effort -> AC2 RED

**Always:**
- one support rule in model_registry; frontend never re-derives it
- registry stays stdlib-only

**Never:**
- rewrite stored thinking_mode
- reject the PUT


## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
| evaluate | full profile (fe+be, not a bug-only change) | mechanical | multi-file feature fix across seam |
| think | effective mode passed as param, not PromptBuilder state | mechanical | PromptBuilder shared across cached/prewarmed sessi |
| plan | hide unsupported options + note | taste | Dropdown has no per-option disabled |
| build | Guard reuses effective_thinking_mode; model=None returns sto | mechanical | Gate-1 WARN items |
| review | Judge thinking capability on pre-translation model name | mechanical | ARN-mapped models carry no family version |
| test | Pre-existing failures verified on clean HEAD worktree | mechanical | identical failure set |

**Summary:** 5 mechanical, 1 taste, 0 judgment

## 7. Quality Gates
### 7.5 Adversarial Review
**⚠️ NOT RUN** — adversarial sub-agent was not spawned during this run.
This is a quality gap. Pipeline DELIVER requires adversarial review.

### 7.6 Completion Audit
(No completion audit data)


## 8. Files Changed
- `backend/model_registry.py`
- `backend/core/prompt_builder.py`
- `backend/routers/settings.py`
- `backend/tests/test_prompt_builder_thinking_guard.py`
- `backend/tests/test_settings_router.py`
- `desktop/src/services/settings.ts`
- `desktop/src/components/settings/AIModelsTab.tsx`
- `desktop/src/components/settings/__tests__/AIModelsTab.thinking.test.tsx`

## 9. Lessons
- [pitfall] When a capability guard rewrites one field of a request (thinking type) but sibling fields are derived independently from the raw config (effort, display), the guarded request is internally inconsistent — compute the effective value once and feed every consumer from it, rather than guarding one field after the fact.
- [pitfall] A model-capability rule judged on a translated identifier (a Bedrock profile id, or a user-mapped ARN) silently fails open when the translation carries no family/version; judge capability on the model's canonical name before translation, the same name every other surface (Settings, registry) uses.
- [principle] A UI that offers options the backend will silently override is a lie the user cannot detect; expose the backend's effective value and supported set as read-only response fields and render from them, so the frontend never re-derives the rule and stays truthful when the rule changes.
- [guideline] After ANY settings write, re-apply every derived field from the response — a change to one setting (default model) can change what another setting (thinking mode) means; test each write path, because a missed refresh is invisible until reload.


## 10. Known Gaps & Attention Flags
None identified.


---
*Generated by SwarmAI Autonomous Pipeline | 2026-10-17 10:17 UTC*
