# Styles: catalog, choosing, contract

A style is the look of a film: one style per film, written into `storyboard.json` `"style"`. Ported styles have a full style file `styles/<id>.md` plus tokens `styles/<id>.css`. Unported styles are listed so the agent can name them, propose one, or create it with `references/stylegen.md`. Never use an unported style as if it existed: if the brief asks for one, either pick the closest ported style or build it with stylegen first.

## Catalog (50)

| id | Look | Best use | Ported |
|---|---|---|---|
| exec-brief | Ivory editorial brief: serif statements rising line by line, one accent, hairline grid, big numbers, flow, timeline, slow push | Leadership talks, strategy, methodology, internal comms, industry viewpoints | yes |
| exec-dark | Night office: darkened office photo base, glass cards, warm white serif rising from blur, amber accent, full-film amber progress bar | Management sharing, reviews, retros, exec viewpoints | yes |
| dark-keynote | Near-black stage, hairline grid, UI as the hero, one accent, beat-snapped landings, light-sweep reveal, scramble numbers | Software / SaaS / AI product launch, feature explainer | yes |
| dataviz | Cream paper with dot grid; a red/blue pencil plots real data point by point; notes pinned to data; colour = value | KPI readouts, cost and usage trends, benchmarks, data journalism | yes |
| iso-infographic | Floating isometric sandbox on a beige dot grid; three-tone flat blocks, thin ink callouts, cutaways, unit icons | Architecture, process, supply chain, annual report, how it works | yes |
| whiteboard | Glossy whiteboard; a floating marker writes as the voice speaks; magnets and eraser; one continuous shot | Principles, training, online course, how it works | yes |
| swiss-motion | International typographic grid, one typeface, black/white/grey plus one signal colour, beat-snapped, giant numerals | Specs, numbers, brand identity, event promo, design talks | yes |
| living-screencast | Looks like a real screen recording, but all UI is redrawn crisp; zoom and whip in one take; optional pixel mascot living in the UI | Product tutorials, feature launch, onboarding | yes |
| 3d-product | Studio 3D product on dark or white sweep, three-point plus rim light, orbit, explode, callouts | Hardware or software product intro, launches | no (build with stylegen) |
| 3d-stage | Keynote 3D stage: reflective floor, volumetric beams, floating glass panels, logo wall, milestone timeline | Customer cases, company intro, business pitch | no (build with stylegen) |
| 3d-city | Procedural night city, glowing windows, chase cam or drone orbit, glowing cyan route with stop cards | Navigation or driving demos, logistics routes, site selection, city promo | no (build with stylegen) |
| hologram-hud | Glowing cyan wireframe scanned in over a projector pad, target locks, local explode, leader to a plated spec card, scrambled digits locking amber, HUD frame | Product specs, teardowns, launch or crowdfunding, expo loop screens | yes |
| blueprint | Prussian-blue cyanotype sheet; white line art drawn in drafting order with a glowing nib, dimensions, balloons, oblique exploded parts, FIG. rewrites, title block, one red stamp | Engineering, hardware, mechanisms, product anatomy, specs | yes |
| anatomy | One invented machine on a bench in fixed axonometric: paper-white solids, four grey side tones, hairlines, framed card with mono corner labels and live readout, one lit part, staggered explode | How-it-works explainers, architecture as a mechanism, hardware teardown, technical launches | yes |
| ascii-crt | Monochrome CRT where everything is density-mapped characters; amber or green phosphor, scanlines; zoom = font size | Developer tools, tech brand, log-style stories | no |
| pictogram-motion | Beat-flashing cards: geometric pictogram figures on sun discs, huge heavy titles, mono index and tick HUD, sliding one-hue grid-pattern rows | Event openers, "everything at a glance" lists, line-ups, step lists, brand visuals | yes |
| midcentury-toon | 1950s flat limited animation, off-register colour, hands-only presenter, numbered badges, magnifier roundels | Install guides, help centre, step tutorials | no |
| tilt-shift | Miniature town from above, tilt-shift focus band, saturated, time-lapse stepping | City promo, real estate, logistics, "how a system runs" | no |
| watercolor | Nature-notebook page, transparent watercolour, dry brush, handwritten labels, layered parallax, fog wash | Nature and geography, science outreach, travel, gentle product stories | no |
| art-deco | 1930s poster: lacquer black and gold engraving, airbrushed hard edges, central-axis symmetry, bulbs light up | Premium brand, galas, invitations, countdowns | no |
| spy-titles | 1960s four-colour cut-paper title sequence, silhouettes running through sets built from type, graphic match cuts | Openers, trailers, launches with suspense | no |
| stained-glass | Dark stone hall window, lead lines, painted faces, only sunlit panes come alive, coloured light pools | Legends, history and culture, staged stories | no |
| crayon-book | Crayon draws a children's picture book live, wobbly outline, colouring outside the lines, wax-resist reveal | Kids' education, bedtime story, warm brand | no |
| impasto | Palette-knife oil paint, side light shows paint relief, a grey world repainted in colour | Music, art and culture, emotional ads | no |
| ink-wash | Chinese ink on rice paper, five ink tones, 60-80 % empty paper, wet bleed and flying-white dry brush, vertical calligraphy columns, one red seal | Cultural brands, tea and craft, poetry, festivals and greetings, tourism, metaphor-led brand stories | yes |
| one-line | One unbroken warm-black line draws everything; camera follows the nib; line morphs as transition | Brand story, milestones, minimal ads | no |
| urban-sketch | Sketchbook pen line with loose watercolour washes slightly off register, margin notes | City travel, architecture, lifestyle | no |
| paper-lantern | Backlit 6-9 layer paper-cut lightbox, glowing cut-outs, soft shadows between layers | Festive marketing, gifts, culture stories | no |
| papercut-red | Chinese red paper-cut, fold-cut symmetry, backlit holes, jointed paper puppets | Traditional festivals, greetings, folk tales | no |
| shadow-puppet | Oil-lamp-lit screen, lace-cut dyed leather puppets with trembling joints | Myth and folk, heritage, festivals | no |
| ukiyoe | Each shot a woodblock print: washi texture, flat registered colour, graded skies, scroll-pan transitions | Travel, East Asian culture, poetic content | no |
| engraving | Natural-history copperplate: line, hatch, cross-hatch built up, magnifier roundels, hand-tinted wash | Museums, science explainer, brand heritage | no |
| halftone-dossier | Retro halftone case file, misregistered headline, rubber stamps slam down, thick-outline cartoon | Humorous social, "case file" lists | no |
| risograph | Two or three translucent spot inks overprinted, grain, misregistration; each plate a layer of meaning | Indie brands, lifestyle, magazine-style promo | no |
| silkscreen-poster | Live screen print, one colour per squeegee pass, stepped sky bands, dark info bar | Tourism, venue guides, event posters | no |
| woodcut | Black block where light appears only where carved, ink on laid paper, at most one colour | Folk tales, craft brands, book trailers | no |
| glass-product | Black infinite studio, fictional glass product, strip lights, dispersion edges, internal parts | Product launch, consumer electronics, beauty | no |
| brick-toy | Macro shallow-focus plastic bricks on a real desk, stop-motion at 12 fps, bricks snap in | Toys and kids, assembly showcase, creative ads | no |
| lowpoly-island | Orthographic low-poly hex world, bouncy spawns, sky colour tells time | World-building, "build step by step", game teaser | no |
| paper-popup | Pop-up book on a desk, paper scenes spring up under lamp light, page turn = scene change | Kids' books, chaptered explainers | no |
| pixel-rpg | 16-bit console RPG: 320x180 native pixels, limited palette, dithering, dialogue and menu storytelling | Games, nostalgia, playful explainers, brand history | no |
| hd-2d | Pixel sprites standing in lit 3D miniature dioramas, tilt blur, fog, bloom, RPG dialogue boxes | Game trailers, fantasy, chaptered brand stories | no |
| game-show | Rhythm-game variety show: flat vector, thick ink outline, candy stripe/dot/sunburst sets, bean contestants hopping on a 150 BPM grid, tilted judgement words | Year-in-review, rankings, step lists, quizzes, playful promos | yes |
| microgame | Party-game micro-levels, each level a different medium, host stage between, accelerating | Game teasers, onboarding, social shorts | no |
| live-drama | 9:16 photoreal drama stills under a loud sticker layer: labels, item cards, boss bars, CTA, flash cuts | Game user acquisition, short-drama ads | no (build with stylegen) |
| backrooms | Found-footage 90s camcorder, 4:3 handheld one take, mustard office, timecode overlay, tracking tears | Mystery, puzzles, game trailers | no |
| silent-film | Theatre curtains around a 4:3 film window, print-style ink with silver wash, flicker, title cards | Retro brand, comedy shorts, wordless stories | no |
| cel-anime-80s | Airbrushed backgrounds with flat cel characters, neon halos, VHS on a CRT | Music videos, game trailers, nostalgia promos | no |
| rubber-hose | 1930s black-and-white rubber-hose cartoon, everything bounces to jazz, 4:3 gate, film damage | Retro ads, kids, mascot reveal | no |
| scifi-toon | Adult sci-fi sitcom cartoon, boiling thick outlines, portals between colour worlds | Humour ads, tech brand, social skits | no |

## How to choose

Decide in this order; the first match wins. Ask the user only if two ported styles fit equally and the brief gives no tone.

**By topic (what is on screen most of the time)**
- An argument with a thesis, evidence and an action plan: exec-brief (light) or exec-dark (dark).
- A software product, UI, feature or AI agent: dark-keynote (launch energy, the UI is the hero) or living-screencast (step-by-step, "this is what you click").
- Real numbers that must be read honestly (trends, comparisons, distributions): dataviz. A few headline numbers only: exec-brief, exec-dark or swiss-motion big numerals.
- Systems, architecture, flows, supply chains, "where things are": iso-infographic.
- A principle explained from scratch, training, cause and effect built up step by step: whiteboard.
- Specs, facts, brand-grade typography, event or product promo: swiss-motion.
- One machine or mechanism explained part by part: anatomy (a calm bench diagram) or blueprint (drafted engineering sheet). A product's specs as a tech showcase: hologram-hud.
- Lists, line-ups, openers: pictogram-motion. Rankings, year-in-review, quizzes, playful steps: game-show.
- Chinese culture, tea and craft, festivals and greetings, metaphor-led brand stories: ink-wash.

**By audience**
- Executives, leadership, internal all-hands: exec-brief, exec-dark.
- Engineers and technical buyers: dark-keynote, iso-infographic, dataviz, living-screencast.
- Learners and new users: whiteboard, living-screencast, iso-infographic.
- Broad or brand audience: swiss-motion, dark-keynote, pictogram-motion.
- Chinese-speaking audience with a cultural or festive tone: ink-wash.

**By tone**
- Calm, serious, considered: exec-brief, exec-dark, dataviz.
- Energetic, launch, beat-driven: dark-keynote, swiss-motion.
- Friendly, teacherly, handmade: whiteboard. Playful, loud, fun: game-show.
- Precise, engineered: blueprint, anatomy. Futuristic, high-tech: hologram-hud. Poetic, quiet: ink-wash.
- Concrete, practical, "show me": living-screencast, iso-infographic.

**By mode and format**
- Ads and promos (15-60 s): dark-keynote, swiss-motion; exec-dark for an executive message.
- Explainers (1-5 min): exec-brief, whiteboard, iso-infographic, dataviz, living-screencast.
- Portrait 9:16: every ported style has portrait rules; whiteboard, dataviz and iso-infographic need the most re-layout, so prefer swiss-motion, exec-brief or living-screencast for portrait-first social cuts.

**Light vs dark pairs**: exec-brief / exec-dark; dataviz or iso-infographic (light) / dark-keynote (dark). Use the user's brand colour as the accent where the style allows an accent swap; never add a second accent.

**Mixing**: one style per film. A film may borrow a single technique from another ported style only if it is rebuilt in the main style's palette, type and motion timing (e.g. a dataviz chart redrawn in exec-brief ink and paper).

## Style contract

A style defines only the look. The engine (HTML pages, GSAP timeline, renderer, captions runtime, TTS, mixer) is implementation and never part of the style's identity. Changing the engine changes the means, not the style: rebuild the look from the style file without lowering the bar.

**A style file defines exactly these, under these headings**: Identity (the few identity rules that carry recognisability; best for / not for), Palette (hex + role; one-accent rule; alternatives), Type (font stacks, size scale, weights, tracking), Layout grid (grid, margins, safe area, elements per screen, subject size, portrait), Camera grammar (vocabulary: shot -> expresses -> how), Motion (timing table, easing, durations, stagger, beat grid, signature moves), Materials and light (gradients, vignette, shadows, glow, textures with numbers), Technique library (capabilities with how-to and snippets), Transitions, Captions, Image-gen guidance, Taboos (only what the style never uses), Known gaps (what this port cannot yet reproduce).

**Shared conventions every style file and .css follows**
- Unit: `u` = frame short side / 1080. CSS: `--u: calc(min(100vw, 100vh) / 1080)` is a length (1px at 1080p); sizes are `calc(N * var(--u))`; only 1px hairlines stay literal. GSAP: `const u = MV.u`.
- Fonts: macOS system fonts only, no web fonts, no font files, no `@import`, no remote URLs. Base stacks: sans `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`; serif `Georgia, "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", "Times New Roman", serif`; mono `"SF Mono", Menlo, "PingFang SC", monospace`; hand `"Chalkboard SE", "Bradley Hand", "Marker Felt", "Hannotate SC", "HanziPen SC", "Kaiti SC", cursive`.
- Easing names are GSAP: quad = `power1`, cubic = `power2`, quart = `power3`, quint = `power4`; cubic-bezier given where useful. Every move >= 0.25 s.
- Determinism: the picture is a pure function of time (seek-rendered). No CSS animation or transition, no timers, no clock, no unseeded random (use a seeded PRNG), no network. Textures are procedural (CSS gradients, SVG `feTurbulence` and friends), never copied bitmaps.
- Transitions between scene files are only `cut` or `fade` (0.4 s crossfade, from `project-contract.md`). Style-owned transitions (slides, wipes, irises, morphs, camera travel over one board or sheet) are built inside a scene: both states in one scene file, or an entrance in the incoming scene's first frames that echoes the transition. With `fade`, put no key text in the incoming scene's first 0.4 s.
- Captions are drawn by the runtime. A style supplies only CSS variables: `--cap-bg, --cap-fg, --cap-font, --cap-size, --cap-radius, --cap-bottom` (optional `--cap-weight, --cap-accent, --cap-shadow, --cap-border`), with a portrait override. Keep the caption zone free of other text.
- One accent rule (where the style has an accent): two accent-coloured elements on one screen means one of them is wrong.
- Class names in each `.css` use a style prefix (`eb-`, `ed-`, `dk-`, `dv-`, `iso-`, `wb-`, `sw-`, `ls-`, `an-`, `bp-`, `hh-`, `iw-`, `pm-`, `gs-`) so a scene can never mix two styles by accident.

**Adding a style**: follow `references/stylegen.md`, then add a row above with `Ported: yes`.

Design of 43 catalog styles adapted from an MIT-licensed style set (pinned commit, see NOTICE.md) and of anatomy from a second MIT-licensed project; exec-brief and exec-dark adapted from an Apache-2.0-licensed style set. 3d-product, 3d-stage, 3d-city and live-drama are not in the MIT-licensed snapshot: they can only be built from scratch with `references/stylegen.md`, never ported from the style set's later releases (non-commercial licence).
