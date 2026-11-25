# Kind: copy

Copy is the text people paste into a platform: a 小红书 post, a 朋友圈 text, a 公众号
article, an X post, a LinkedIn post. The source is `src/copy.md`; the whole file is one unit
with id `main`. It goes into the delivery block as text, ready to paste.

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" init --dir D --kind copy --title T
```

Copy that goes with a deck or a poster can be its own work next to it, so it can be revised
on its own.

## Platforms

The person's language picks the default: English → X and LinkedIn; Chinese → 小红书,
朋友圈 and 公众号. A platform the person names wins. Write one section per platform in
`src/copy.md` (`## 小红书`, `## 朋友圈`, `## X` …), each in that platform's own shape:

| Platform | Shape | Limits |
|---|---|---|
| 小红书 | title, body, tags | title ≤ 20 characters; body 200–500 characters, short paragraphs, emoji is fine; 5–10 tags |
| 朋友圈 | one block of text | no title, no tags |
| 公众号 | title, lead, article | the point and one fact in the first paragraph |
| X | one post or a short thread | ≤ 280 characters per post; the first post stands alone |
| LinkedIn | one post | what it means for the reader's work, not technical depth |

Tone per platform: `brand/tone_guide.md`. Title and tag rules: `references/platform-specs.md`.

## Write well

- **Start with the point.** The first sentence is the thesis or the most useful fact.
- **No AI openers** ("In today's rapidly evolving…", "在当今快速发展的…"), no empty
  superlatives ("revolutionary", "game-changing") without proof.
- **Value, not output.** No lines of code, commit counts or "N days" as the claim.
- **What is true, not "what I built".** No hero framing ("我造了…", "我们是最…").
- **Each piece stands alone.** A reader who sees only this post understands it.
- **No internal terms** (module names, code names) in the body.
- English inside Chinese text only where it is stronger than the Chinese word.

The full list: `brand/content_principles.md`.

## Check

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/p2_scan.py" D/src/copy.md
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/geo_score.py" D/src/copy.md --json
```

- `p2_scan.py`: exit 1 lists hero-framing lines; rewrite them.
- `geo_score.py`: use its warnings ("generic opener", unsupported claims) for English text.
  Its score is for long articles; for a short post, ignore the score and fix the warnings.
- Count the limits in the table yourself (title length, tags, characters per post).

Then `work.py cut --dir D` to make the version, and paste each section into the delivery
block (cards/deliver.md).
