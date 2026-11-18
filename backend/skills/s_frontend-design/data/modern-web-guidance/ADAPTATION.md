# Modern Web Guidance: how SwarmAI uses it

Source: [GoogleChrome/modern-web-guidance](https://github.com/GoogleChrome/modern-web-guidance),
Apache-2.0 (`LICENSE.upstream`). The commit and the sha256 of every file are in
`PROVENANCE.json`.

`guides/` holds the upstream guides verbatim: one Markdown file per use case or
discipline, maintained by the Chrome, Edge, Firefox and MDN teams. A guide's id is its
file name without `.md`.

What SwarmAI changes:

- The upstream skill runs `npx -y modern-web-guidance@latest` for every search. SwarmAI
  does not: that runs unpinned remote code on the user's machine and needs Node 22 and a
  network. `scripts/web_guidance.py` in this skill searches and retrieves the pinned
  guides instead (`search`, `retrieve`, `list`), offline, on every platform.
- The upstream `legacy-scan` command and its rules are not included.
- The guides point to each other with the upstream command
  (`npx -y modern-web-guidance@latest retrieve "<id>"`). The files keep it verbatim;
  every text `web_guidance.py` prints names the local script instead, so following a
  cross-reference never runs the npm package.
- `INSTRUCTIONS.md` of this skill, section "Modern web guidance", says when to search
  and how to use a guide. It adapts the upstream skill's workflow (search first, follow
  the guide's fallbacks for features that are not Baseline widely available, name the
  guides that shaped the code).

To refresh the guides, run `backend/scripts/vendor_modern_web_guidance.py` on a fresh
checkout (its docstring gives the commands). It replaces `guides/`, the license and
`PROVENANCE.json`, and keeps this file.
