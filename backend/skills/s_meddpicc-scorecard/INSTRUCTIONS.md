# MEDDPICC Opportunity Scorecard

Score opportunities against the AWS MEDDPICC framework so sellers know exactly what's missing and what to do next — not just "the deal is weak."

## Quick Start

```
User: "MEDDPICC score the Meituan AI Coding deal"
→ Agent pulls SFDC data → scores 8 dimensions → checks stage gates → outputs scorecard with actions
```

## Workflow

### Step 1: Identify the Opportunity

Resolve the user's input to one or more SFDC opportunity IDs.

| User provides | Action |
|---------------|--------|
| Opportunity ID (006...) | Use directly |
| Account name | `search_accounts` → `search_opportunities` (open only) → show list, user picks |
| Opportunity name / keyword | `search_opportunities` with queryTerm → show list, user picks |
| "Score all open opptys for X" | `search_opportunities` with accountId + isClosed=false → score each |

### Step 2: Collect Data

Pull from AWSentral MCP. All calls are best-effort — gracefully degrade if access-denied.

| Tool | Data | Required? |
|------|------|-----------|
| `search_opportunities` (with condition) | Basic oppty fields (stage, amount, ARR, dates, competitor, forecast, next step) | ✅ Yes — minimum viable |
| `get_opportunity_details` | Full details + activity history | Nice to have (often access-denied) |
| `get_opportunity_contact_roles` | Stakeholder mapping (champion, EB, etc.) | ✅ Yes |
| `fetch_account_details` | Health score, adoption phase, TAS, TTM revenue | Nice to have |
| `get_account_spend_summary` | MTD/YTD spend, growth trend | Nice to have |
| `get_account_spend_history` (with monthly breakdown) | Revenue trajectory | Nice to have |

**Minimum viable data:** opportunity search results + contact roles. Everything else enriches the analysis.

### Step 3: Score Each Dimension

**Scoring principle: evidence-based only.** Points are awarded for what is *documented in SFDC*, not what is *probably true*. If it's not in the system, it's not verified.

| Credit Level | Criteria | Points |
|--------------|----------|--------|
| **Full** | Explicit evidence in SFDC fields (description, next steps, contacts, activity) | 100% |
| **Partial** | Reasonable inference from deal context (e.g., stage=TechVal implies some criteria known) | 50-75% |
| **Zero** | No evidence or pure assumption | 0% |

---

#### M — Metrics (13 points, ~13%)

*Quantifiable business impact and urgency of the solution.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Urgency and business problem quantified into a metric | 4 | **Full 4:** description/next steps mention specific KPIs, ROI, or business numbers. **Partial 2:** amount is set but no customer-side business metric documented. **Zero:** no evidence. |
| 2 | Metric verified with Champion | 4 | **Full 4:** champion in contact roles AND metric is referenced in a mutual context. **Partial 2:** champion exists but no metric validation evidence. **Zero:** no champion or no metric. |
| 3 | Metric verified with Economic Buyer | 5 | **Full 5:** EB in contact roles AND engagement evidence mentioning business case. **Partial 2:** EB identified but no metric discussion evidence. **Zero:** EB not identified. |

#### E — Economic Buyer (13 points, ~13%)

*The person(s) with financial authority for the buying decision.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Engaged with the Economic Buyer | 4 | **Full 4:** contact role with "Economic Buyer" or "Decision Maker" role, or activity showing exec meeting. **Partial 2:** senior contact exists but not tagged as EB. **Zero:** no EB-level contact. |
| 2 | Executive alignment call completed | 4 | **Full 4:** activity history shows exec meeting/call. **Partial 2:** next steps mention planned exec engagement. **Zero:** no evidence. |
| 3 | EB verbal go | 5 | **Full 5:** next steps or description mention approval/commitment from EB. **Partial 2:** forecast=Committed or stage=Committed. **Zero:** no evidence. |

#### D — Decision Criteria (13 points, ~13%)

*How the customer will evaluate and decide to purchase.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Know how customer will decide | 3 | **Full 3:** description documents evaluation criteria. **Partial 2:** stage ≥ Qualified implies some criteria exchanged. **Zero:** Prospect stage, no criteria info. |
| 2 | Champion confirmed decision criteria | 3 | **Full 3:** champion in contacts AND criteria documented. **Partial 1:** either champion OR criteria, not both. **Zero:** neither. |
| 3 | Criteria differentiated for AWS | 3 | **Full 3:** description shows AWS-specific advantages vs competitor. **Partial 2:** competition field populated + AWS has known structural advantage for this deal type. **Zero:** no differentiation evidence. |
| 4 | EB confirmed preference for AWS | 4 | **Full 4:** EB engagement + positive forecast status. **Partial 2:** positive forecast but no direct EB confirmation. **Zero:** EB not engaged or forecast negative. |

#### D — Decision Process (13 points, ~13%)

*The formal steps the buyer will follow to make a decision.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Champion provided steps to close | 4 | **Full 4:** next steps contain multi-step plan with timeline. **Partial 2:** next step is a single action (not a plan). **Zero:** next step empty, stale (>30 days), or single status note. |
| 2 | Legal, Finance, EB aligned | 4 | **Full 4:** multiple stakeholder contact roles (legal, procurement, exec). **Partial 2:** some stakeholders but not all key roles. **Zero:** single contact or no contacts. |
| 3 | Executing mutually aligned close plan | 5 | **Full 5:** next step updated within 14 days AND shows progress toward close date. **Partial 2:** next step exists but stale (14-30 days) or vague. **Zero:** next step >30 days old or no close plan evidence. |

#### P — Paper Process (12 points, ~12%)

*Legal and procurement requirements and procedures.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Understand procurement requirements | 4 | **Full 4:** description or next steps mention procurement process, contract type, or legal requirements. **Partial 2:** record type suggests known process (e.g., Committed Contract). **Zero:** no procurement info. |
| 2 | Paper/procurement champion identified | 4 | **Full 4:** contact with procurement/legal role. **Partial 2:** main contact likely handles procurement (small deal). **Zero:** no procurement contact. |
| 3 | Executing aligned paper process | 4 | **Full 4:** next steps show procurement progress (NDA signed, MSA in review, etc.). **Partial 2:** paper process mentioned but not progressing. **Zero:** no evidence. |

#### I — Implicate the Pain (12 points, ~12%)

*Customer's business challenges and the cost of not solving them.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Identified pain and implications | 4 | **Full 4:** description articulates specific business problem. **Partial 2:** deal type implies pain (e.g., migration = vendor dependency pain) but not explicitly stated. **Zero:** no pain articulation. |
| 2 | Sufficient quantifiable negative consequences | 4 | **Full 4:** description or next steps quantify cost of inaction ($$, time, risk). **Partial 2:** pain is described qualitatively but not quantified. **Zero:** no consequence articulation. |
| 3 | Customer agrees pain compels action | 4 | **Full 4:** stage ≥ TechVal AND next step updated within 14 days (active engagement = customer sees urgency). **Partial 2:** stage ≥ Qualified but engagement is slow/stale. **Zero:** Prospect stage or stalled deal. |

#### C — Champion (12 points, ~12%)

*Internal advocate who sells on your behalf.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Champion has power and influence | 4 | **Full 4:** contact role tagged as champion/key contact with senior title. **Partial 2:** contacts exist but none tagged as champion. **Zero:** no contacts. |
| 2 | Champion providing valuable intel | 4 | **Full 4:** next steps contain insider information (competitive intel, internal process details, stakeholder preferences). **Partial 2:** next steps are factual but could come from any external meeting. **Zero:** no insider intel. |
| 3 | Evidence champion is selling internally | 4 | **Full 4:** multiple stakeholders engaged over time (champion is bringing people in). **Partial 2:** single contact with stage progression (someone is pushing internally). **Zero:** no multi-stakeholder evidence. |

#### C — Competition (12 points, ~12%)

*Customer's alternatives and competitive positioning.*

| Q# | Question | Max | How to Score |
|----|----------|-----|-------------|
| 1 | Know customer's alternatives | 4 | **Full 4:** `primary_Competitor__c` populated + `details_if_Other_or_No_Competition__c` has specifics. **Partial 2:** competitor field populated but generic ("Other" without details). **Zero:** competitor field empty. |
| 2 | Differentiated against alternatives | 4 | **Full 4:** description shows specific AWS advantages for this deal. **Partial 2:** AWS has known structural advantage for this deal type (e.g., global regions for international expansion). **Zero:** no differentiation evidence. |
| 3 | Requirements favor AWS | 4 | **Full 4:** forecast positive + stage advancing + criteria aligned to AWS strengths. **Partial 2:** deal is active but no evidence requirements tilt toward AWS. **Zero:** competitor appears to be winning or status is "At Risk." |

---

### Step 4: Stage Gate Check

| Stage | Min Score | Must Be Green (≥75% of max) |
|-------|-----------|------------------------------|
| Prospect | — | — |
| Qualified | — | — |
| Technical Validation | **≥65** | Pain (≥9/12), Champion (≥9/12) |
| Business Validation | **≥65** | Pain (≥9/12), Champion (≥9/12) |
| Committed | **≥80** | Pain (≥9/12), Champion (≥9/12), EB (≥10/13) |

**Color thresholds per dimension:**

| Color | Range | Meaning |
|-------|-------|---------|
| 🟢 Green | ≥75% of max | On track |
| 🟡 Yellow | 25-74% of max | Gaps exist |
| 🔴 Red | <25% of max | Critical gap |

If the current stage requires gates that aren't met → flag **"⚠️ Stage Inflation Risk"**.

### Step 5: Detect Risk Signals

Auto-check these red flags from SFDC data:

| Signal | Condition | Severity |
|--------|-----------|----------|
| **Stale Deal** | `next_Step_Last_Updated__c` > 14 days ago | 🟡 |
| **Very Stale Deal** | `next_Step_Last_Updated__c` > 30 days ago | 🔴 |
| **Close Date Risk** | Close date within 30 days but stage is Prospect/Qualified | 🔴 |
| **Past Due** | Close date is in the past | 🔴 |
| **No Stakeholder Map** | `get_opportunity_contact_roles` returns empty | 🔴 |
| **Forecast Risk** | `forecast_Status__c` = "At Risk" or "Not in Forecast" | 🟡 |
| **Stalled Stage** | `lastStageChangeDate` > 90 days ago | 🔴 |
| **No Sizing** | Amount = 0 or null | 🟡 |
| **No Partner** | `co_Sell_Engagement_Type__c` = "None" | ℹ️ Info |
| **Single Threaded** | Only 1 contact role (or 0) | 🟡 |

### Step 6: Generate Actions

For each 🔴 Red dimension, generate a specific action:

| Action Field | Content |
|--------------|---------|
| **Priority** | 🔴 / 🟡 based on dimension status |
| **Action** | Specific next step (not generic "improve this") |
| **Target** | Which dimension + question it addresses |
| **Est. Impact** | How many points this could add |

Sort actions by: (1) stage gate blockers first, (2) highest point impact, (3) lowest effort.

### Step 7: Output the Scorecard

Use this exact format:

```markdown
## [Opportunity Name] — MEDDPICC Scorecard

**Account:** [name] | **Owner:** [alias] | **Stage:** [stage] (since [date])
**Amount:** $X | **ARR:** $X | **Type:** [record type]
**Close:** YYYY-MM-DD ([X days left]) | **Competitor:** [name] | **Forecast:** [status]
**Account Health:** X/100 | **Adoption:** [phase] | **Spend Trend:** [up ↑/down ↓/flat →]

---

### Dimension Scores

| Dimension | Score | Max | Status | Key Gap |
|-----------|-------|-----|--------|---------|
| **M** Metrics | X | 13 | 🔴/🟡/🟢 | [one-line gap or "On track"] |
| **E** Economic Buyer | X | 13 | 🔴/🟡/🟢 | ... |
| **D** Decision Criteria | X | 13 | 🔴/🟡/🟢 | ... |
| **D** Decision Process | X | 13 | 🔴/🟡/🟢 | ... |
| **P** Paper Process | X | 12 | 🔴/🟡/🟢 | ... |
| **I** Implicate Pain | X | 12 | 🔴/🟡/🟢 | ... |
| **C** Champion | X | 12 | 🔴/🟡/🟢 | ... |
| **C** Competition | X | 12 | 🔴/🟡/🟢 | ... |
| **TOTAL** | **X** | **100** | **🔴/🟡/🟢** | |

### Stage Gate Check

[Current stage] requires: [requirements]
- Score [X]: [PASS ✅ / FAIL ❌] (need ≥[threshold])
- Pain [status]: [PASS ✅ / FAIL ❌]
- Champion [status]: [PASS ✅ / FAIL ❌]
- EB [status]: [PASS ✅ / FAIL ❌] (if Commit stage)

→ [PASS / ⚠️ STAGE INFLATION RISK]

### Risk Signals

[List each detected signal with emoji + one-line explanation]

### Top Actions (by impact)

| # | Action | Target | Est. Impact |
|---|--------|--------|-------------|
| 1 | [Specific action] | [Dimension] | +X pts |
| 2 | ... | ... | +X pts |
| 3 | ... | ... | +X pts |

**If all actions completed, estimated score: X → Y**
```

### Step 8: Save (Optional)

If user requests, save to:
```
Knowledge/Notes/YYYY-MM-DD-meddpicc-[account-short]-[oppty-short].md
```

With YAML frontmatter:
```yaml
---
title: "MEDDPICC Scorecard — [Opportunity Name]"
date: YYYY-MM-DD
tags: [meddpicc, sales, qualification, [account-name]]
opportunity_id: [SFDC ID]
score: [total/100]
stage: [current stage]
---
```

---

## Guardrails

- **DO NOT** give full points for assumptions. "Probably has a champion" = 0 points. Evidence or nothing.
- **DO NOT** skip the stage gate check. A deal scoring 22/100 in Technical Validation is a finding, not a footnote.
- **DO NOT** generate generic actions like "improve champion engagement." Every action must be specific: who to contact, what to ask, what outcome to verify.
- **DO NOT** score based on deal amount alone. $1M deal with zero contacts is worse than $50K deal with a mapped champion and EB.
- **DO NOT** treat "next step exists" as evidence of a close plan. A close plan has multiple steps with a timeline. A single status update is not a plan.

## Language

Match the user's language. Chinese input → Chinese output. English input → English output.
MEDDPICC dimension names, SFDC field names, and scoring labels stay in English regardless.

## Multi-Opportunity Mode

When scoring all open opptys for an account:

1. Score each independently using the full framework
2. After all scores, add a **Portfolio Summary** table:

```markdown
### Portfolio Summary — [Account Name]

| Opportunity | Amount | ARR | Stage | Score | Gate | Top Gap |
|-------------|--------|-----|-------|-------|------|---------|
| [name] | $X | $X | [stage] | X/100 | ✅/❌ | [gap] |
| ... | ... | ... | ... | ... | ... | ... |

**Portfolio Health:** X/Y opptys pass stage gates | Avg score: X | Total pipeline ARR: $X
```

