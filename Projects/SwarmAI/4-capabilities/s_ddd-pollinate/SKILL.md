---
name: ddd-pollinate
description: "Express THIS DDD's product value to audiences — message-first, format follows — via a bundled, decoupled copy of SwarmAI's real pollinate engine (11 format tracks + scripts + moat gates), sourcing value from the DDD's own ② PRODUCT.md, file-based, no SwarmAI backend. DDD-native decouple of s_pollinate.\n  TRIGGER: \"ddd pollinate\", \"express this ddd's value\", \"make content from this ddd\".\n  NOT FOR: SwarmAI's own s_pollinate (bound to Swarm brand + SwarmWS paths)."
tier: lazy
---
# DDD-Native Pollinate (s_ddd-pollinate)

The **value-expression** capability a DDD carries: turn what the product IS (its ②
Knowledge — vision, differentiation, proof) into audience-shaped media.
**Message-first, format follows the audience.**

> **DDD-native decouple of SwarmAI's `s_pollinate`.** This is NOT a hand-written shell —
> it bundles the REAL engine (11 format tracks, ~25 scripts, the moat gates) copied from
> `s_pollinate` and decoupled: every hardcoded `~/.swarm-ai` path resolves from
> `$SWARM_WORKSPACE`, SwarmAI's brand assets are stripped (you supply your own via
> `brand/identity.yaml`), and the publish target + brand checks are configurable — so a
> DDD runs it on Kiro / Claude Code / an AIM package with no SwarmAI backend.

## What's bundled (real engine, not a stub)

| Part | What | Portable? |
|------|------|-----------|
| `tracks/` (11) | track-a-video … track-k-podcast — the format playbooks | ✅ verbatim |
| `references/`, `REVIEW_PATTERNS.md`, `phonemes.json` | platform specs, review rubric, TTS phonemes | ✅ verbatim |
| `brand/identity.yaml` | **TEMPLATE** — fill with YOUR project's name/colors/voice | ⚙️ you fill |
| `data/slide_bold_previews/` | 34 deck design systems (bundled, was in s_frontend-design) | ✅ bundled |
| `scripts/` | evaluate/format-recommend/validator/gates/tts/shorts/publish | ✅ decoupled |
| the moat gates | `pollinate_validator.py` + `convergence_gate.py` + `cross_format_check.py` + `p2_scan.py` | ✅ pure stdlib |

## The principle (retained)

Do NOT start from "make a poster." Start from: **who is the audience**, what is the ONE
**message** that changes their mind, THEN pick the **format** that lands it. Source every
claim from THIS DDD's ② `PRODUCT.md` — never invent proof not in ② Knowledge.

## THE MOAT (option C — validator-run, agent-enforced)

Unlike the pipeline, pollinate has no `artifact_cli` chokepoint — its moat is **run the
validator as a BLOCKING step** (the same way the poster track already runs `p2_scan`):

```bash
python scripts/pollinate_validator.py <content_dir> --json   # exit 1 = FIX before deliver
```
`pollinate_validator.py` (9 structural invariants incl. cross-format 7-9) +
`convergence_gate.py` (8-layer poster gate) + `p2_scan.py` (anti-slop first-person scan)
are ALL pure stdlib and run standalone. The agent MUST run the validator before DELIVER
and fix every error — degrading to "review this adversarially" only where no runtime can
execute it. Never silently skip.

**Brand checks are DDD-configurable, NOT SwarmAI-locked** (opt-in via env):
`POLLINATE_REQUIRE_QR=1`, `POLLINATE_REQUIRE_LINK=<substr>`, `POLLINATE_WATERMARK=<text>`.
Unset → those checks auto-pass (a portable DDD isn't forced to carry SwarmAI's QR/watermark).

## Deliver boundary

Emit a portable package under `<workspace>/.artifacts/pollinate/output/<slug>/`; the
human/runtime publishes it. Publishing needs `POLLINATE_PUBLISH_REPO=<owner>/<repo>` (YOUR
repo — never a hardcoded one). NEVER auto-post to an external channel.

See INSTRUCTIONS.md for the full stage flow.
