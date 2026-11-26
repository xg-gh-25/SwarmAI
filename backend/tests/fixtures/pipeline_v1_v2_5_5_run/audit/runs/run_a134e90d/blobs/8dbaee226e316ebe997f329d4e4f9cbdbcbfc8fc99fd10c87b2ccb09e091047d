EXPERT-ID: gate0-skeptic
PIPELINE-HANDOUT: run_a134e90d 47399e8dc78cc28010ee15f8c58106ec

You are a skeptic. The understanding is the "understanding.claim" in the JSON below;
the work type is on the "WORK TYPE:" line.
Do NOT trust it. Answer these 6 questions and NOTHING else.
1. Is the claim supported by OBSERVATION (code-trace
   file:line / ps / log counts / repro / characterization), or only inference? Name it.
2. Name the ONE simplest alternative framing that fits the same facts, and why
   it loses. Exactly one — if none fits, say "no alternative fits" and move on.
3. Is the implied change already true / a no-op? grep and check.
4. If a root cause is given: is its last why_chain level the ROOT, or another symptom
   ("the code does X" is a symptom; "nothing enforces rule Y" is a root)? Grep for ONE
   prior instance or sibling (same shape elsewhere) its recurrence search missed.
5. Acceptance wording, in any language the requirements are written in: is each
   acceptance criterion verifiable (an observable or measurable response, not "fast",
   "simple", "appropriate" with no bound)? Does each criterion whose text forbids
   something or says it holds for every case carry the matching flag (negative /
   universal), and does no flag claim what the text does not say? Name each failing
   acceptance id; any failure makes the verdict at most SUPPORTED-WITH-CHANGES.
6. Verdict: SUPPORTED (evidence cited) | SUPPORTED-WITH-CHANGES (sound, but item 5
   or a small correction needs fixing first) | UNSUPPORTED (inference only) |
   ALREADY-SATISFIED (no-op) | WRONG-FRAME (symptom / wrong layer / the "root" is a symptom / a missed sibling).

SCOPE BUDGET: read at most 3 files, at most 6 tool calls (reading PROMPT-FILE
does not count), answer under 250 words. These 6 questions ARE the whole task —
do not expand into a general audit of the codebase or the plan.

GATE-0 ATTEMPT 1 OF 2
WORK TYPE: bugfix
{
  "analysis": [
    {
      "kind": "missing_edge",
      "note": "none",
      "refs": [
        "R1.1"
      ],
      "resolution": "rewritten"
    }
  ],
  "bugfix": {
    "current": [
      {
        "condition": "add is called",
        "id": "C1",
        "negative": false,
        "pattern": "event",
        "response": "it returns the sum",
        "universal": false
      }
    ],
    "expected": [
      {
        "condition": "add is called",
        "id": "E1",
        "negative": false,
        "pattern": "event",
        "response": "it returns the sum",
        "universal": false
      }
    ],
    "unchanged": [
      {
        "condition": "add is called",
        "id": "U1",
        "negative": false,
        "pattern": "event",
        "response": "it returns the sum",
        "universal": false
      }
    ]
  },
  "clarifications": [
    {
      "answer": "core.mod",
      "answered_by": "evidence",
      "qid": "Q1",
      "question": "Which add?"
    }
  ],
  "knowledge_refs": [],
  "requirements": [
    {
      "acceptance": [
        {
          "condition": "add is called",
          "id": "R1.1",
          "negative": false,
          "pattern": "event",
          "response": "it returns the sum",
          "universal": false
        }
      ],
      "id": "R1",
      "story": "As a caller"
    }
  ],
  "understanding": {
    "claim": "swap the operands",
    "evidence": [
      "backend/core/mod.py"
    ],
    "readings": [
      "one"
    ],
    "root_cause": null
  }
}

OUTPUT CONTRACT (exact; prose may precede rows):
Gate-0 only: first non-empty line is VERDICT: SUPPORTED | SUPPORTED-WITH-CHANGES | UNSUPPORTED | WRONG-FRAME | ALREADY-SATISFIED
R2 status: {"finding_id":"CF-1234abcd","status":"RESOLVED|OPEN|REFUTED|ADEQUATE|TEST_INADEQUATE","kind_confirm":null,"note":"..."}
New finding: {"severity":"HIGH|MEDIUM|LOW","kind":"behavior|contract|absence|docs|design|security_policy","file":"path","line":1,"claim":"...","fix":"..."} (file: its path from the repository root, as in the diff's file header; line: the line in the file as it is in the tree you review)
When there are no new findings, include a separate line: NO FINDINGS
Do not emit a VERDICT line for REVIEW experts.
