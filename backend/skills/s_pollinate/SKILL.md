---
name: pollinate
description: "Make and revise decks, posters, long images, reports and social copy. TRIGGER: deck, slides, HTML deck, 演示文稿, 海报, poster, 长图, report, 报告, 小红书, 文案, \"改这份 deck\", \"revise this deck\". NOT FOR: editing a .pptx file directly (s_pptx), Word/Excel."
tier: lazy
python-deps: [boto3, pptx, yaml, PIL, lxml, pypdf]
python-optional-deps: []
---

# Pollinate: the content creator

Pollinate makes content that people revise many times: HTML decks (with PDF and PPTX
export), posters, long images and reports, and platform copy. The first version looks
professional. Each later round changes only the agreed slides and never loses the
person's own edits.

Read INSTRUCTIONS.md before you start. It routes the request, explains the work folder
and lists the commands. The cards (`cards/`) and the kind pages (`kinds/`) hold the
detail; read only the ones the task needs.

## Verification

Before you say a version is done, show evidence for each line:

- [ ] `work.py cut` exited 0 for this version (render, craft checks and guard all passed),
      or you said which check failed and why it is left.
- [ ] Only the declared scope changed: `guard.json` has no violations.
- [ ] At most 2 review rounds ran, and you said what they found and what you fixed.
- [ ] Canvas (or the browser, for a work outside the workspace) shows the current version.
- [ ] The chat has one copy-ready delivery block: one line per output (file, version,
      size), each platform's copy text, inline thumbnails, and the summary paragraph.
- [ ] Nothing was published or sent. Publishing is always the person's click.
