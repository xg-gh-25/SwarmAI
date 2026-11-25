# Kind: page (long image or report)

A page is one long HTML page: a long image (长图) for a feed, or a report (dashboard,
scorecard, comparison) to read in a browser. The source is `src/index.html`; the page is one
unit with id `main`. `cut` gives `out/page.png` and the HTML.

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" init --dir D --kind page --title T --size 1080x1920
```

`--size` sets the width and the first screen; the PNG height follows the content.

## Long image (长图)

- 1080 wide. Height at most 8000 px (feeds may cut longer images).
- At most 6 sections. Number them (1/6, 2/6 …) so the reader knows where they are.
- Each section makes sense alone, so a screenshot of it still works.
- Use the poster rules for tokens, centering, spacing, type and brand (kinds/poster.md).
- Section spacing may be more generous than a poster (up to 96 px), because the reader
  scrolls a long way.

## Report

Start from a template in `templates/html-report/` (all use `base.css`):

| Template | Best for |
|---|---|
| `report.html` | an overview: key numbers, trends, sections |
| `scorecard.html` | cards with ratings, an assessment |
| `comparison.html` | options side by side ("A vs B", "对比") |
| `base.css` only | a structure that fits none of the above |

- Width: a reading width of 1200–1440 px; set `--size` to match.
- Numbers come from the sources or the project knowledge, with their unit and date. An
  approximate number stays approximate ("about 30%"), never made exact.
- Interaction is a bonus (tabs, sections that open). Everything important must also be
  visible in the PNG, which is rendered with the page as it first loads.
- Brand: the configured signature in the footer (`cut` checks the watermark, link text
  and QR, as for a poster).

## Revise

A page is one unit, so a scope of `main` lets you change anything on it. Change only the
part the person asked about and keep the rest as it is; say in the delivery paragraph which
sections changed. A restyle keeps at least 90% of the sections (kinds/poster.md § Restyle).

## Delivery

Canvas opens `out/page.png` (a long image) or the HTML (a report). Show the top of the page
as the thumbnail.
