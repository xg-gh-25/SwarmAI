# Bundled fonts

The style stacks (`../*.css`) list the macOS families first, then one of these faces, then Windows/Linux
system names. A machine without the macOS families (Windows, Linux) renders these files, so a frame rendered
on Linux shows what Windows shows. Every face is licensed under the SIL Open Font License 1.1; each licence
text, with its copyright line, is the `OFL-*.txt` file next to the font. The files are subsets (below) and are
used only as web fonts inside the scene pages this skill renders; they are not offered as installable desktop
fonts (the condition of the LXGW WenKai licence's additional permission for its reserved names).

| Family in CSS | File | Weights | Upstream file | Upstream sha256 | File sha256 | Licence |
|---|---|---|---|---|---|---|
| MV Noto Sans SC | NotoSansSC.woff2 | 100-900 | google/fonts `ofl/notosanssc/NotoSansSC[wght].ttf` | a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da | 4a6decfcbf537241ac17c08c32fd5e41983d51c63cf65621b579883ec7e7c1e5 | OFL-NotoSansSC.txt |
| MV Noto Serif SC | NotoSerifSC.woff2 | 200-900 | google/fonts `ofl/notoserifsc/NotoSerifSC[wght].ttf` | 050080d9255a86808f2945bffac582b31ef32bc36411ce29563b4961670c66f9 | 6e110a7f82ffc431bbea394e4fafef5c1432166061d691a3c62e87b08527a9e4 | OFL-NotoSerifSC.txt |
| MV LXGW WenKai | LXGWWenKai-Regular.woff2 | 400 | lxgw/LxgwWenKai release v1.530 `LXGWWenKai-Regular.ttf` | 2d73eea94ad808999d90c0000c239a84a33b09d4bc6ab6c5b8bbf1eda8a7fc8b | 852b5809940872c8bae0abbbff051e7cd9e1870ee73219f79377a49207ef6030 | OFL-LXGWWenKai.txt |
| MV Noto Sans Mono | NotoSansMono.woff2 | 100-900 | google/fonts `ofl/notosansmono/NotoSansMono[wdth,wght].ttf` | 2cb2adb378a8f574213e23df697050b83c54c27df465a2015552740b2769a081 | d4fd884b8d3867c93e70bae0f009d443b507a86143fc12e2be6a62fe024684f2 | OFL-NotoSansMono.txt |
| MV Patrick Hand | PatrickHand-Regular.woff2 | 400 | google/fonts `ofl/patrickhand/PatrickHand-Regular.ttf` | 0f173b3e6cb6d1af25babf7f0057c5ac4ee11f9992b0469bb817e967ef4ad0fc | 44ebf4ce7c82dd980f0b0701e2d5143d6837ccae7cdd741f196157bc1c9ddba7 | OFL-PatrickHand.txt |
| MV Roboto Condensed | RobotoCondensed.woff2 | 100-900 | google/fonts `ofl/robotocondensed/RobotoCondensed[wght].ttf` | dace262afcee68a5276f200d8026c57221735c0118ab5fda8c2c0d3dc409a8d0 | 086d2e258ea2f0f64d32d17238445d086f81c31c442ba34a7f1e41c300ab7d0c | OFL-RobotoCondensed.txt |
| MV Jost | Jost.woff2 | 100-900 | google/fonts `ofl/jost/Jost[wght].ttf` | 6343b70971000b04c5d401c96ae08ce371086135e999d5e1e1413039c0213076 | 7c18224e2c13a7ebfbd67c3691524d349b42fbdba906fd4d3797a401b457ac91 | OFL-Jost.txt |
| MV Nunito | Nunito.woff2 | 200-1000 | google/fonts `ofl/nunito/Nunito[wght].ttf` | bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207 | 80487cad049a40f91ba76261c8a698f2694245f7380371eec234304061683794 | OFL-Nunito.txt |
| MV Great Vibes | GreatVibes-Regular.woff2 | 400 | google/fonts `ofl/greatvibes/GreatVibes-Regular.ttf` | 8d509802186f1b51572531ecf313e8098f9a5bfdfaca93f0c9b34467f9982d15 | dd664e4d43155f09b447099a82fbd5f44914afd263289c53a6848e7e25529ddc | OFL-GreatVibes.txt |

Sources: google/fonts at commit `a85815a42757630ce188fdad368c2dfc444d4773`
(https://github.com/google/fonts/tree/a85815a42757630ce188fdad368c2dfc444d4773/ofl) and
https://github.com/lxgw/LxgwWenKai/releases/tag/v1.530.

## How the files were made

fontTools 4.60.2 `pyftsubset <upstream file> --text-file=<chars> --flavor=woff2 --layout-features='*'`
(variable axes kept). Character sets:

- CJK faces (Noto Sans SC, Noto Serif SC, LXGW WenKai): every character of GB2312 (all 6763 hanzi plus its
  symbols, kana, Greek and Cyrillic) and U+0020-007E, U+00A0-017F, U+2000-206F, U+20A0-20BF, U+2100-218F,
  U+2190-21FF, U+2200-22FF, U+2460-24FF, U+2500-257F, U+25A0-25FF, U+2600-26FF, U+2700-27BF, U+3000-303F,
  U+FE10-FE1F, U+FE30-FE4F, U+FF00-FFEF.
- Latin faces: U+0020-007E, U+00A0-017F, U+2000-206F, U+20A0-20BF, U+2100-2122, U+2190-2199, U+2212.

A character outside a subset (a rare hanzi, say) is drawn by the next family in the stack. To add a face or
widen a subset, rebuild it the same way from the upstream file, then update this table; the skill's tests
compare every file with its sha256 here.
