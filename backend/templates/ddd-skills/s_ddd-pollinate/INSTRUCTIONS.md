# s_ddd-pollinate — Full Flow (engine-driven, portable)

You drive pollinate through the **bundled, decoupled engine** in this skill's `scripts/`
+ `tracks/`. NO SwarmAI backend, NO `~/.swarm-ai` — all state resolves from
`$SWARM_WORKSPACE` (fallback: cwd) under `<workspace>/.artifacts/pollinate/`. Runs on any
runtime (Kiro / Claude Code / AIM package) after `aim` export.

## Setup

```bash
SKILL_DIR=<this skill dir>
export SWARM_WORKSPACE=<workspace root holding this DDD>
# optional publish/brand config (unset = portable defaults, no SwarmAI brand-lock):
#   export POLLINATE_PUBLISH_REPO="<owner>/<repo>"   # required only to publish
#   export POLLINATE_WATERMARK="Made with <you>"      # optional deck watermark
#   export POLLINATE_REQUIRE_QR=1 / POLLINATE_REQUIRE_LINK=<substr>  # opt-in brand gates
```
Fill `brand/identity.yaml` with THIS DDD's brand (name/colors/voice) before producing
visual/audio tracks — sourced from the DDD's own ② `PRODUCT.md`.

## The flow (message-first — same shape as native)

1. **DISCOVER — "搞清楚再动手".** Who is the audience, what ONE message changes their mind,
   which channel. Fast-path OK for a single obvious track. `confirmed_tracks` is the
   authority — no downstream stage adds/removes a track without re-confirmation.
2. **INIT.** Content dir = `<workspace>/.artifacts/pollinate/output/<slug>/`. Create
   `tracks/<name>/` ONLY for confirmed tracks (zero wasted production).
3. **EVALUATE / STRATEGIZE.** ROI against confirmed scope. `python scripts/evaluate_topic.py`
   + `python scripts/format_recommend.py` recommend format from audience/outcome/context.
4. **PLAN.** Populate only the content-package layers the confirmed tracks need.
5. **BUILD.** Execute each confirmed track per its `tracks/track-*.md` playbook. Source
   claims from ② `PRODUCT.md` — never invent proof. Poster track runs `p2_scan.py` BLOCKING.

   **Track dispatch** (confirmed-track token → playbook doc):

   | Token | Playbook |
   |-------|----------|
   | `video` | `tracks/track-a-video.md` |
   | `poster` | `tracks/track-b-poster.md` |
   | `narrative` | `tracks/track-c-narrative.md` |
   | `deck` | `tracks/track-e-deck.md` |
   | `html_deck` | `tracks/track-e2-html-deck.md` |
   | `data_report` | `tracks/track-g-data-report.md` |
   | `document` | `tracks/track-h-document.md` |
   | `image` | `tracks/track-i-image.md` |
   | `interactive_report` | `tracks/track-j-interactive-report.md` |
   | `podcast` | `tracks/track-k-podcast.md` |
   | `one_pager` / `full_pdf` | `tracks/track-f-pdf.md` |
6. **REVIEW.** Self-review against `REVIEW_PATTERNS.md`.
7. **DELIVER — run the moat (option C, BLOCKING):**
   ```bash
   python "$SKILL_DIR/scripts/pollinate_validator.py" <content_dir> --json   # exit 1 → FIX
   ```
   Fix every error before delivering. This is the enforced gate (no artifact_cli chokepoint
   in the portable engine — the agent runs the validator, same as the poster track runs
   p2_scan). Emit the package; the human/runtime publishes (`publish_to_pages.py` needs
   `POLLINATE_PUBLISH_REPO`). NEVER auto-post.
8. **REFLECT.** Record what the audience/format choice was (demand signal). Sediment any
   new pollinate lessons into THIS DDD's ② `IMPROVEMENT.md`.

## THE MOAT (never skip)

| Gate | Script | Enforcement |
|------|--------|-------------|
| Structural + cross-format (9 invariants) | `pollinate_validator.py <dir> --json` | BLOCKING at DELIVER (exit 1) |
| Poster 8-layer convergence | `convergence_gate.py` | BLOCKING in poster BUILD |
| Anti-slop first-person scan | `p2_scan.py` | BLOCKING in poster BUILD |
| Cross-format consistency (advisory) | `cross_format_check.py` | WARN |

All four are pure stdlib and run standalone. Brand checks (QR / link / watermark) are
opt-in via env — a portable DDD is never forced to carry SwarmAI's brand.

## Decouple invariants (do not regress)

- `grep -rnE '^\s*(from|import) (core|config|utils|jobs)\b' scripts/*.py` → 0 (SwarmAI backend).
- `grep -rn '\.swarm-ai\|SwarmWS\|xg-gh-25\|swarm-content\|s_frontend-design' scripts/*.py | grep -vE '#|"""'` → 0 live (paths resolve from `$SWARM_WORKSPACE`; brand/publish targets are env-driven).
- The moat scripts import + run with SwarmAI's `core/` off `sys.path`.
- `brand/identity.yaml` is a `{{...}}`-placeholder template; `brand/assets/` ships NO SwarmAI binaries.

## Why engine-in-the-skill (not a shell)

The engine is COPIED, not re-authored — so the format playbooks + the anti-slop/convergence
moat travel intact. What was decoupled: the `~/.swarm-ai` paths (→ `$SWARM_WORKSPACE`), the
Swarm brand assets (stripped; you supply your own), and the hardcoded publish repo + brand
gates (→ env-configurable). The method + engine travel; the SwarmAI-specific machine room
stayed behind.
