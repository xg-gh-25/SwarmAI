# C011: Pipeline Confidence Is Process Compliance, Not Code Correctness

> 8 pipeline stages. 10/10 confidence score. 57 tests green.
> Feature was 100% non-functional.

## What Happened

Built Voice Conversation Mode through the full autonomous pipeline (EVALUATE→DELIVER). Every stage passed. Confidence score: maximum. Tests: all green.

The feature didn't work at all:
- Recording never auto-stops (VAD not implemented — declared in types, never wired)
- Audio sentences play out of order (concurrent fire-and-forget, no sequencing)
- Interrupt state unreachable (declared in TypeScript types but never set in code)
- `voice_id="Zhiyu"` sent with `language="en-US"` → Polly rejects silently
- `setTimeout` race condition in message send → messages arrive before audio finishes

Required 2 rounds of post-pipeline E2E review (builder's + user's) to catch all issues. Each round found different classes of bugs.

## Why It Happened

Three compounding failures:

**1. State machine declaration ≠ implementation.**
TypeScript declared 6 states. Design doc drew transitions. Code only wired 4. The type system and passing tests gave false confidence — you can't fail a test for code that doesn't exist.

**2. Happy-path-only review.**
Traced the happy path: English text → Polly → audio. Never checked the override path: `voice_id="Zhiyu"` + `language="en-US"` → Polly rejects because Zhiyu only accepts `cmn-CN`. The REVIEW stage checked "does Polly get called?" not "does Polly get called with valid parameters?"

**3. No cross-boundary data flow verification.**
Each unit worked in isolation. The data flowing *between* units was wrong — wrong LanguageCode, wrong timing, wrong ordering. Unit tests verified each piece; nothing verified the seams.

## Structural Prevention

| Fix | What It Does | Location |
|-----|-------------|----------|
| State Machine Audit | For every declared state: name the code path that enters it | Pre-Implementation Checkpoint #5 |
| E2E Checkpoint | After implementation, trace full user path one level downstream | Post-Implementation gate |
| Unreachable state detection | Added to Code Quality Scan 🔴 (auto-fix) | AGENT.md scan rules |
| RP13: State completeness | Pipeline REVIEW verifies every declared state is reachable | `INSTRUCTIONS.md` |
| RP14: Cross-service params | REVIEW checks parameter contracts between services | `INSTRUCTIONS.md` |
| RP15-17: Timing/async patterns | REVIEW detects setTimeout-for-state, unordered async, unsanitized strings | `INSTRUCTIONS.md` |
| Mandatory adversarial | Fresh sub-agent reviews from scratch (no builder bias) | DELIVER stage requirement |

## The Generalizable Insight

**Tests measure what you coded, not what you intended.** A state machine with 6 declared states and 4 wired transitions will pass every test for the 4 wired paths. The 2 unwired paths don't fail — they simply don't exist in the test matrix.

Pipeline confidence scores that measure *stage completion* ("did REVIEW run?") rather than *stage effectiveness* ("did REVIEW catch the unreachable state?") are theater metrics. The score was 10/10 because every stage ran — not because every stage worked.

**The fix is adversarial review by a separate agent.** The builder cannot review their own assumptions because the assumptions are invisible to them. A fresh context (different agent, no shared history) sees what the builder structurally cannot.

## Code References

- Pipeline run: `run_6823b0d4`
- Voice feature commits: `61463f9`, `acfc338`, `41d2f45`, `cfb3ddf`, `8daed8f`
- AGENT.md rules: search "Pre-Implementation Checkpoint", "Post-Implementation E2E"
- Pipeline patterns: `backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` (RP13-RP17)
- Adversarial requirement: `backend/skills/s_autonomous-pipeline/INSTRUCTIONS.md` (DELIVER stage)
