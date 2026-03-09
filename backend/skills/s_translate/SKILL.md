---
name: Translate
description: >
  Translate text, documents, and code between languages. Supports files, inline text, and batch operations.
  TRIGGER: "translate", "translation", "convert to Spanish", "in Japanese", "localize", "i18n", "multilingual".
  DO NOT USE: for code transpilation (e.g., Python to JavaScript) or summarizing content in a different language (use summarize).
version: "1.0.0"
---

# Translate

Translate text, documents, and structured files between languages. Zero external dependencies -- uses Claude's built-in multilingual capabilities plus optional free verification APIs.

## Workflow

### Step 1: Parse the Request

Determine:
- **Source language**: auto-detect if not specified
- **Target language(s)**: one or more
- **Content type**: inline text, file, directory of files, or i18n resource bundle
- **Tone/register**: formal, informal, technical, casual (default: match source)
- **Domain**: general, legal, medical, technical, marketing (affects terminology)

If ambiguous: "What language should I translate to?"

### Step 2: Identify Content Source

| Source | How to Handle |
|--------|---------------|
| Inline text | Translate directly from the message |
| Single file | Read with Read tool, translate, write output |
| i18n JSON/YAML | Parse structure, translate values only (preserve keys) |
| Markdown/docs | Translate content, preserve formatting and structure |
| Code comments | Translate comments only, leave code untouched |
| Subtitle files (.srt, .vtt) | Translate text lines, preserve timestamps |

### Step 3: Translate

#### For Inline Text

Translate directly using Claude's capabilities. Present as:

```
Source (English):
  "The quick brown fox jumps over the lazy dog."

Translation (Japanese):
  "素早い茶色の狐が怠惰な犬を飛び越える。"

Notes:
  - Literal translation; a more natural Japanese version: "茶色の狐がのんびりした犬を素早く飛び越えた。"
```

**Rules:**
- Always show source and target side by side for short text
- Flag idiomatic expressions that don't translate directly
- Offer alternatives when multiple valid translations exist
- Preserve tone: formal stays formal, casual stays casual

#### For i18n Resource Files (JSON, YAML, .strings, .properties)

```bash
# Detect i18n file format
# JSON: {"key": "value"} or {"key": {"nested": "value"}}
# YAML: key: value
# .strings: "key" = "value";
# .properties: key=value
```

Translate values ONLY. Never modify:
- Keys/identifiers
- Interpolation variables: `{name}`, `{{count}}`, `%s`, `%d`, `$t(key)`
- HTML tags within values
- Plural forms structure (keep ICU/i18next patterns)

Example i18n translation:

```json
// Source: en.json
{
  "greeting": "Hello, {name}!",
  "items_count": "You have {count} items",
  "error.not_found": "Page not found"
}

// Output: ja.json
{
  "greeting": "こんにちは、{name}さん！",
  "items_count": "{count}個のアイテムがあります",
  "error.not_found": "ページが見つかりません"
}
```

#### For Documents (Markdown, Text, DOCX)

- Preserve all formatting (headers, lists, bold, links)
- Translate alt text on images
- Keep code blocks untranslated
- Preserve front matter structure (translate values if text)

#### For Code Comments

```python
# Source
def calculate_tax(amount):
    # Calculate the tax based on the current rate
    # Returns the tax amount rounded to 2 decimal places
    rate = 0.08  # 8% consumption tax
    return round(amount * rate, 2)

# Translated to Japanese
def calculate_tax(amount):
    # 現在の税率に基づいて税金を計算する
    # 小数点以下2桁に丸めた税額を返す
    rate = 0.08  # 8% 消費税
    return round(amount * rate, 2)
```

### Step 4: Quality Checks

After translating, verify:

1. **Completeness**: No untranslated segments left behind
2. **Variables preserved**: All `{placeholders}` intact
3. **Formatting intact**: Markdown, HTML tags, whitespace preserved
4. **Length**: Flag translations significantly longer than source (UI overflow risk)
5. **Consistency**: Same term translated the same way throughout

For critical translations, offer verification via free API:

```bash
# MyMemory API (free, no key, 5000 chars/day)
curl -s "https://api.mymemory.translated.net/get?q=Hello&langpair=en|ja"

# LibreTranslate (if self-hosted instance available)
curl -s -X POST "https://libretranslate.com/translate" \
  -H "Content-Type: application/json" \
  -d '{"q":"Hello","source":"en","target":"ja"}'
```

Use these for spot-checking, NOT as primary translation (Claude's quality is higher for most language pairs).

### Step 5: Output

| Content Type | Output Format |
|-------------|---------------|
| Inline text | Show in conversation |
| Single file | Write to `{filename}.{lang}.{ext}` or user-specified path |
| i18n bundle | Write to `{lang}.json` / `{lang}.yaml` in same directory |
| Batch files | Create `{lang}/` subdirectory mirroring source structure |

Always confirm output path before writing files.

---

## Supported Languages (Common)

| Code | Language | Code | Language |
|------|----------|------|----------|
| en | English | ko | Korean |
| es | Spanish | ar | Arabic |
| fr | French | hi | Hindi |
| de | German | pt | Portuguese |
| it | Italian | ru | Russian |
| ja | Japanese | zh | Chinese (Simplified) |
| zh-TW | Chinese (Traditional) | nl | Dutch |
| sv | Swedish | th | Thai |
| vi | Vietnamese | id | Indonesian |

For other languages, auto-detect or ask the user to specify the ISO 639-1 code.

---

## Batch Translation

For translating to multiple languages at once:

```
User: "Translate my en.json to Spanish, French, and Japanese"
```

1. Read the source file once
2. Translate to each target language
3. Write each output file: `es.json`, `fr.json`, `ja.json`
4. Present a summary table:

```markdown
| Key | en | es | fr | ja |
|-----|-----|-----|-----|-----|
| greeting | Hello! | Hola! | Bonjour! | こんにちは！ |
| ... | ... | ... | ... | ... |

Files written:
- locales/es.json (42 keys)
- locales/fr.json (42 keys)
- locales/ja.json (42 keys)
```

---

## Specialized Modes

### Legal/Formal Translation
- Preserve exact meaning over readability
- Flag ambiguous terms with translator notes
- Never simplify or paraphrase legal clauses

### Marketing/Creative Translation
- Prioritize natural flow in target language
- Adapt idioms and cultural references
- Offer 2-3 variants for taglines/slogans

### Technical Translation
- Keep technical terms consistent (provide glossary)
- Preserve code/command references untranslated
- Match domain-specific terminology

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Ambiguous source language | Ask user to confirm, or run detection heuristic |
| Mixed-language source | Identify boundaries, translate each segment appropriately |
| i18n plurals (ICU format) | Preserve `{count, plural, one {# item} other {# items}}` structure, translate inner text only |
| RTL languages (Arabic, Hebrew) | Note that UI may need RTL layout changes beyond just text |
| Very long document | Process in sections to maintain context; summarize key terminology decisions |
| Character encoding issues | Ensure UTF-8 output; warn if source has encoding problems |

## Quality Rules

- Never guess at proper nouns (names, brands, places) -- keep original or ask
- Always preserve variables, HTML tags, and formatting markers
- Flag cultural adaptations needed (date formats, units, address formats)
- For ambiguous terms, offer alternatives with context
- Zero dependencies: uses only Claude's multilingual capability + optional free API verification
