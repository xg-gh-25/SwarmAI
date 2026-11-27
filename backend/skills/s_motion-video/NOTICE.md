# Notice

Third-party work this skill builds on:

- the HTML + GSAP frame-seek authoring model, and ideas in the explainer and ad playbooks:
  heygen-com/hyperframes (Apache-2.0);
- most visual style designs: an MIT-licensed public style set (lemomo-ai/lemo-opuscar); no font
  data or texture images were copied (textures are drawn procedurally; the fonts are described
  under Fonts below);
- the anatomy style's design: an MIT-licensed public project (wheresryan22/anatomy, commit
  e6600ffc4e8e6620eeb45bf49bcdbd5e4b84163f, 2026-10-13, Copyright (c) 2026 Ryan), rebuilt for this
  engine from its documents and examples; none of its code, tools or examples were copied.

blueprint, hologram-hud, ink-wash, pictogram-motion and game-show were ported directly from the
style set at commit 227e00a (see the licence pin below), on 2026-11-25: each rewritten as this
skill's style file pair; no source code, fonts, pictures or sounds were copied, and pictogram-motion
uses its own palette. MIT licence texts: Copyright (c) 2026 LemoLab (style set) and Copyright (c)
2026 Ryan (anatomy); the full texts are in
`references/styles/MIT-LICENSES.txt`.

GSAP (`runtime/gsap.min.js`) is distributed under its own license, not under this repository's: the GreenSock
"Standard No Charge" license (https://gsap.com/standard-license). Why vendoring it here is allowed, clause by
clause, is recorded in `runtime/GSAP-LICENSE.txt`.

**Upstream licence pin.** The style set's licence changed after the commit these styles come from.
At `227e00adfbbec1ef4b7065a4189c2e1c8be2fef4` (2026-10-12) the whole repository was plain MIT.
From release 2.0.0 (2026-11-12) only its code stays MIT; every Markdown file (each STYLE.md and
DEMO.md, the guides) is CC BY-NC 4.0 (non-commercial), demo films and stills are CC BY 4.0, and a
separate commercial licence is offered. So port or rebuild styles only from the 2026-10-12 commit
(or earlier), never from a later one, and check the licence of each font, sound or picture a demo
bundles on its own.

## Fonts

The style stacks name macOS system families first; none of those fonts is shipped. Where they are absent
(Windows, Linux) the stacks reach fonts bundled in `references/styles/fonts/`, all under the SIL Open Font
License 1.1, shipped as subsets with their licence texts: Noto Sans SC, Noto Serif SC, Noto Sans Mono, Roboto
Condensed, Jost, Nunito, Great Vibes and Patrick Hand (google/fonts, https://github.com/google/fonts) and LXGW
WenKai (https://github.com/lxgw/LxgwWenKai). Sources, pinned versions, sha256 and the subset recipe:
`references/styles/fonts/FONTS.md`.

## Voice engines (`scripts/tts.py`)

**edge-tts** (https://github.com/rany2/edge-tts, version 7.3.1 when read on 2026-11-27) is a Python client for
the online text-to-speech service that Microsoft Edge's Read Aloud uses. What is known from public sources:
- The library is LGPL-3.0 (srt_composer.py MIT) per its LICENSE and PyPI metadata
  (https://pypi.org/project/edge-tts/). It is not bundled with SwarmAI; the skill imports it only when installed.
- The service has no public API, documentation or terms of its own for third-party clients; edge-tts reaches it
  by presenting itself as the Edge browser. Its README notes that Microsoft rejects any SSML Edge itself would
  not send. The service has broken old clients before (an outdated edge-tts gets HTTP 403).
- Microsoft's consumer services are governed by the Microsoft Services Agreement
  (https://www.microsoft.com/en-us/servicesagreement, published 2026-07-30, effective 2026-09-30). Its software
  licence (section 8) grants use "as part of your use of the Services", forbids to "circumvent or bypass any
  technological protection measures", to "disassemble, decompile, decrypt, hack, emulate, exploit, or reverse
  engineer any software or other aspect of the Services", and to "enable access to the Services ... by
  unauthorized third-party applications"; its Code of Conduct forbids circumventing "restrictions on access to,
  usage, or availability of the Services".
- So no public source authorises edge-tts's use of the endpoint, and a reasonable reading of the agreement is
  that a third-party client emulating Edge is not authorised. Use it for personal, non-commercial work at the
  person's own risk; for a product, a commercial film or anything published, use an official service: Amazon
  Polly (below), or Microsoft's own paid Azure AI Speech, which serves the same neural voices under its terms
  (https://learn.microsoft.com/azure/ai-services/speech-service/).
- Choice made for this skill (2026-11-27): a Chinese film still defaults to edge-tts's Yunxi voice, and the
  person running it carries the risk above; when edge-tts fails, tts.py voices the whole film with Amazon
  Polly instead. Anyone publishing a film, or shipping this skill in a paid product, should voice it with
  Polly (`--engine polly`) or Azure AI Speech.

**Amazon Polly** (https://aws.amazon.com/polly/) is called through boto3 with the person's own AWS credentials
and is billed to their account per character (two requests per scene: audio and word speech marks). Under the
AWS Service Terms (https://aws.amazon.com/service-terms/, section 50 "AI Services") the generated audio is the
customer's content, and AWS may use the submitted text to improve the service unless the account opts out
(AWS Organizations AI services opt-out policy).
