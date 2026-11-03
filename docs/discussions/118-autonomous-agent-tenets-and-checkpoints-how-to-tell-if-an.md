---
title: "Autonomous Agent Tenets and Checkpoints — how to tell if an agent workflow is really autonomous"
created: 2026-11-03
updated: 2026-11-03
status: published
---
<!-- GitHub Discussion #118: https://github.com/xg-gh-25/SwarmAI/discussions/118 -->

How we judge whether an agent workflow is really autonomous: four tenets, a short checkpoint matrix, and the eval dimensions behind it.

## 1. Tenets

> Unless you have better ones.

### T1 · Domain knowledge is the foundation — cultivate it, don't just document it. 【Priority 0】

Domain knowledge is the experts' judgment, handed to the agent: purpose and boundaries, know-how, product and asset definitions, technical constraints and pitfalls, lessons learned, and shared terminology, plus the domain's own skills and tools where it needs them. Autonomy can't go beyond that judgment. Build the knowledge first, and keep it growing before you raise autonomy.

Agents read the knowledge before each task and write back what they learned. We encourage automating this; to start, domain experts can do it by hand. A domain expert is accountable for its quality, and it stays current, correct, and readable by people and agents.

- **We reject**: a knowledge base written once and left to rot; patching missing domain understanding with more prompt.

### T2 · Human-on-the-loop — humans review and judge, the system remembers.

Autonomy removes human execution and babysitting, not human review and judgment. Execution can't depend on anyone staying online, so remote cloud run is a hard requirement (H2). People step in only at review and decision points set in advance by task type, risk, and maturity, and only when the trigger fires: requirement clarification when real ambiguity remains; design review for new, immature, or high-risk task types, moving to sampling once a task type proves reliable. Every judgment and correction goes into the knowledge or the eval, so no one answers the same question twice.

- **We reject**: "autonomy" that only works while someone watches; counting "no human involved" as high autonomy; an agent that guesses when it is unsure; approval at every step; a correction left in chat and repeated next time.

### T3 · Eval the whole autonomous loop, not just the deliverable — and let the eval steer.

A correct deliverable doesn't prove autonomy; someone may have fixed it ten times along the way. Evaluate the whole loop, with measures and a baseline set before you start:

1. **Autonomy**: does the agent get the work right on its own, with people giving only intent and judgment?
2. **Human attention**: is it spent at the designed review and decision points, and is it going down?
3. **Knowledge**: does it improve the agent's judgment, and does it stay current and consistent, without drifting or rotting?

Every run leaves a trace that answers these, and records its full token cost. When something fails, gets corrected, or costs more than expected, find out why. The causes show where to invest next.

- **We reject**: building first and checking results later; judging autonomy by the deliverable alone; the agent's own numbers, or activity counts like "ran 100 runs", as evidence.

### T4 · Quality and security are the floor — autonomy is built on top of them, never traded for them.

Autonomy doesn't lower the bar. The floor is your defect rate and your production security findings; faster delivery that lets either one slip is not progress. Every autonomous flow has its own quality and security gates and catches its own bugs and risks instead of shipping them to the right. External, irreversible, or customer-data actions always need human approval. No other tenet overrides this one.

- **We reject**: shipping bugs and risks to the right for reviewers, production, or customers to catch; buying speed with quality or security; treating a green build as proof of quality; one-size-fits-all "fully automatic"; adding approvals only after an incident.

---

## 2. Checkpoints Matrix

🔴 = must-have.

| Area | # | Checkpoint | What shows it |
|---|---|---|---|
| **Domain Knowledge** | K1 | 🔴 Domain knowledge is in place: experts, stakeholders, and agents can use it, and engineers or builders can manage it (no full automation needed at first). A domain expert owns its quality | The knowledge, in use by people and agents |
| **Domain Knowledge** | K2 | Knowledge is used and grows: it shapes the agent's decisions in real runs, and what runs learn gets written back | A few runs and their write-backs |
| **Human-on-the-loop** | H1 | Review and decision points are set per task type, each with a trigger: requirement clarification when real ambiguity remains, design review for new, immature, or high-risk task types, approval for dangerous permissions | The list, and runs where each one fired |
| **Human-on-the-loop** | H2 | 🔴 Remote cloud run: run state persists, so a task can run remotely, ask a question asynchronously, and resume after the answer; the trace goes to eval | One real task completed unattended in the cloud, and a passing pause → answer → resume test |
| **Eval** | E1 | 🔴 Every run leaves a trace and records its full token cost; a standalone eval layer and dashboard built on those traces track autonomous rate, human attention, and knowledge health | Traces, cost records, and the dashboard |
| **Quality & Security** | Q1 | 🔴 Bugs and risks are caught before the PR, by the flow's own quality and security gates or an adversarial review; external, irreversible, and customer-data actions need a person's approval | Gate or review results from before the PR, and approval records |

---

## 3. Three Eval Dimensions and the Cost Record

| Dimension | What to look at |
|---|---|
| **① Autonomous Rate** | How much of the delivered work the agent carries itself, with people mostly giving direction and judgment. Read it alongside quality |
| **② Human-on-the-loop** | Where people step in, and whether they give intent and judgment or end up doing the agent's engineering. Whether the same correction keeps coming back |
| **③ Knowledge** | Whether knowledge changes the agent's decisions; whether known conflicts surface, stale content gets flagged, and the gaps behind failures get filled |
| **④ Cost record** | Full token cost per delivered task, and the share spent on rework |

---

## Appendix: FAQ

**Q: T1 is Priority 0 and T4 overrides everything. Which wins?**
T4 is a constraint, not a priority. It sets the space we work in, and inside that space T1 comes first.

**Q: How is "autonomous" defined?**
There is no standard definition yet. The working test: the person acted as director and approver, not engineer. Answering the agent at a planned point, or unblocking it with direction, still counts as autonomous; designing the solution or handing over the root cause doesn't. Non-code work uses the same test.

**Q: Why is remote cloud run a hard requirement?**
A flow that needs someone's laptop or session open still has that person in the loop, however good the agent is. In the cloud, the same knowledge, eval, and gates apply, questions reach people wherever they are, and run records, cost, and learnings flow back into the same domain knowledge. H2 holds even if a shared cloud platform isn't ready yet.

**Q: Where exactly do humans step in?**
Each point fires on its trigger, not on every run. Every team defines ①, ②, and ④ (H1); the rest are examples.

| Point | Trigger | Example |
|---|---|---|
| **① Requirement clarification** | Real ambiguity remains after the agent has used the knowledge | "Make the weekly report faster": faster queries, or sent earlier? The agent asks; the person picks |
| **② Design-spec review** | A new, immature, or high-risk task type (moves to sampling once proven reliable) | The agent writes a short spec before any code; the person approves it or sends it back |
| **③ Mid-run ruling** | Knowledge conflicts or is missing | Two sources count refunds differently; the agent pauses and the person decides |
| **④ Dangerous-action approval** | Release, external send, data deletion, permission changes | The agent attaches the evidence; the person approves or rejects |
| **⑤ Batched taste review** | Choices with a sensible default that still need taste | Metric names, tone, chart style, reviewed together at delivery |
| **⑥ Delivery acceptance** | Every delivery (moves to sampling once proven reliable) | The person reads the delivery report, not every line of code, and ships it or sends it back |
| **⑦ After-the-fact correction** | A low eval score, or an escaped defect | The person reviews only the flagged runs and writes the right result |

**Q: Why do skills and tools go with domain knowledge?**
A skill captures how the domain's work is done right; a tool gives the agent access to the domain's systems. Shipped with the knowledge, they give whoever takes over the domain both the judgment and the means to act on it. Each is managed differently: knowledge for accuracy and freshness, skills for versions and validation, tools for permissions and security. A domain only needs them if its work calls for them; they are not a must-have.

**Q: Does knowledge cultivation have to be automated?**
Not at first. We encourage automation because that is how cultivation scales, but a domain expert can start by hand. What doesn't count is no mechanism at all: knowledge that never gets updated, or corrections that never reach it.

**Q: Why is token cost recorded but not capped?**
A cap cuts off real work, and cost can rise for good reasons: knowledge in context costs tokens, and it is worth it when it changes decisions. The record shows where to improve autonomous performance (tokens spent on rework, spinning loops, or knowledge that costs more than it helps), and it builds the team's harness engineering and operational excellence as engineers learn where a harness spends and how to make it leaner and more reliable.

**Q: How does a team earn more autonomy?**
Mostly by improving the front of the loop. Existing coding agents increasingly turn an approved design spec into code and a PR; adopt them rather than rebuild them. A team's own effort pays off most in knowledge cultivation: better knowledge turns a request into the right spec faster, with fewer clarification rounds and fewer specs sent back. Once a task type is reliable, with no escaped defects and the floor holding, design review and delivery acceptance move to sampling; high-risk changes keep design review. An incident moves it back.

---

**Reference implementation:** [SwarmAI v2.4 — One brain, many hands: autonomy that compounds, so it needs less of your attention](https://github.com/xg-gh-25/SwarmAI/discussions/116)
