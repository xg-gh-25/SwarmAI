# Notice

Third-party work this skill builds on:

- the HTML + GSAP frame-seek authoring model, and ideas in the explainer and ad playbooks:
  heygen-com/hyperframes (Apache-2.0);
- most visual style designs: an MIT-licensed public style set (lemomo-ai/lemo-opuscar); no font
  data or texture images were copied (fonts are system fonts, textures are drawn procedurally);
- the anatomy style's design: an MIT-licensed public project (wheresryan22/anatomy, commit
  e6600ffc4e8e6620eeb45bf49bcdbd5e4b84163f, 2026-10-13, Copyright (c) 2026 Ryan), rebuilt for this
  engine from its documents and examples; none of its code, tools or examples were copied.

blueprint, hologram-hud, ink-wash, pictogram-motion and game-show were ported directly from the
style set at commit 227e00a (see the licence pin below), on 2026-11-25: each rewritten as this
skill's style file pair; no source code, fonts, pictures or sounds were copied, and pictogram-motion
uses its own palette. MIT licence texts: Copyright (c) 2026 LemoLab (style set) and Copyright (c)
2026 Ryan (anatomy); the full texts are in
`references/styles/MIT-LICENSES.txt`.

GSAP (`runtime/gsap.min.js`) is distributed under its own license, see `runtime/GSAP-LICENSE.txt`.

**Upstream licence pin.** The style set's licence changed after the commit these styles come from.
At `227e00adfbbec1ef4b7065a4189c2e1c8be2fef4` (2026-10-12) the whole repository was plain MIT.
From release 2.0.0 (2026-11-12) only its code stays MIT; every Markdown file (each STYLE.md and
DEMO.md, the guides) is CC BY-NC 4.0 (non-commercial), demo films and stills are CC BY 4.0, and a
separate commercial licence is offered. So port or rebuild styles only from the 2026-10-12 commit
(or earlier), never from a later one, and check the licence of each font, sound or picture a demo
bundles on its own.
