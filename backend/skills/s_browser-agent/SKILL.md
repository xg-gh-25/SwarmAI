---
name: Browser Agent
description: >
  DOM-based browser automation: navigate websites, read compressed page content, click elements,
  fill forms, extract data, and take screenshots using Playwright.
  TRIGGER: "browse", "open website", "browser agent", "web automation", "fill form online",
  "scrape page", "click on website", "navigate to", "browser".
  DO NOT USE: for simple URL fetching (use WebFetch), macOS app UI automation (use peekaboo),
  or API calls (use Bash/curl directly).
---

# Browser Agent — DOM-Based Web Automation

**Why?** Automate real browser interactions: navigate sites, read page content as compressed DOM,
click buttons/links by element index, fill forms, extract data, and take screenshots.
Uses Playwright with DOM compression (not screenshot/coordinate based) for reliable, token-efficient
web automation.

**Key advantage over WebFetch:** Full browser with JavaScript execution, cookies, sessions, tabs,
and interactive element manipulation.

---

## Quick Start

```
"Go to example.com and find the pricing page"
"Fill in the contact form on their website"
"Extract all product names from this page"
"Take a screenshot of the dashboard"
```

---

## Setup

### Prerequisites

Playwright must be installed with browsers:

```bash
# Check if available
npx playwright --version

# Install browsers if needed (one-time)
npx playwright install chromium
```

### Script Location

```
.claude/skills/s_browser-agent/browser-agent.mjs
```

All commands run via:
```bash
node .claude/skills/s_browser-agent/browser-agent.mjs <action> [args...]
```

For convenience in examples below, we use `BA` as shorthand for the full path.

---

## Core Workflow: Launch → Read → Act → Verify

### Step 1: Launch Browser (background)

```bash
# Start browser server in background
node .claude/skills/s_browser-agent/browser-agent.mjs launch &

# Or launch and navigate immediately
node .claude/skills/s_browser-agent/browser-agent.mjs launch https://example.com &
```

> The `launch` command runs in background (use `&` or run_in_background).
> It stays alive until `close` is called.

### Step 2: Navigate + Read DOM

```bash
# Navigate to a URL — returns compressed DOM with element indices
node .claude/skills/s_browser-agent/browser-agent.mjs navigate https://example.com
```

Output shows compressed DOM like:
```
<header>
  <nav>
    [1]<a href="/home">Home</a>
    [2]<a href="/about">About</a>
    [3]<a href="/pricing">Pricing</a>
  </nav>
</header>
<main>
  <h1>Welcome to Example</h1>
  <p>Some content here...</p>
  [4]<button>Get Started</button>
  <form action="/search">
    [5]<input type="text" placeholder="Search...">
    [6]<button type="submit">Search</button>
  </form>
</main>
```

Elements with `[N]` indices are interactive — use these indices for click/type/etc.

### Step 3: Interact

```bash
# Click a link by index
node .claude/skills/s_browser-agent/browser-agent.mjs click 3

# Type into an input
node .claude/skills/s_browser-agent/browser-agent.mjs type 5 "search query"

# Press Enter
node .claude/skills/s_browser-agent/browser-agent.mjs press Enter
```

### Step 4: Verify (re-read DOM)

After each interaction, the `click` command automatically returns the updated DOM.
For other actions, explicitly read:

```bash
node .claude/skills/s_browser-agent/browser-agent.mjs read
```

### Step 5: Close When Done

```bash
node .claude/skills/s_browser-agent/browser-agent.mjs close
```

---

## Command Reference

### Session Management

| Command | Description |
|---------|-------------|
| `launch [url] [--headed]` | Start browser in background. `--headed` for visible window |
| `close` | Stop browser and clean up |

### Navigation

| Command | Description |
|---------|-------------|
| `navigate <url>` | Go to URL, returns compressed DOM |
| `back` | Browser back |
| `forward` | Browser forward |
| `scroll <up\|down> [amount]` | Scroll page (default: 3 units) |

### Reading Page Content

| Command | Description |
|---------|-------------|
| `read [--max-depth N]` | Get compressed DOM (default depth: 15) |
| `screenshot [path] [--full]` | Screenshot (default: /tmp/browser-screenshot.png) |
| `extract <css-selector>` | Get text from all matching elements |

### Interacting with Elements

| Command | Description |
|---------|-------------|
| `click <index>` | Click element by [N] index. Returns updated DOM |
| `type <index> <text>` | Clear field + type text into element |
| `select <index> <value>` | Select dropdown option |
| `hover <index>` | Hover over element. Returns updated DOM |
| `press <key>` | Press keyboard key (Enter, Tab, Escape, ArrowDown...) |
| `submit <index>` | Submit a form |

### Tab Management

| Command | Description |
|---------|-------------|
| `tabs` | List all open tabs |
| `tab <index>` | Switch to tab by index |
| `newtab [url]` | Open new tab |
| `closetab` | Close current tab |

### Advanced

| Command | Description |
|---------|-------------|
| `eval <js-expression>` | Execute JavaScript in page |
| `wait <ms\|selector>` | Wait for time or element to appear |
| `pdf [path]` | Save page as PDF |

---

## DOM Compression

The `read` and `navigate` commands return a compressed DOM representation:

### What Gets Kept
- **Interactive elements** with `[N]` indices: links, buttons, inputs, selects, textareas
- **Structural landmarks**: headers, nav, main, sections, forms, tables, lists, headings
- **Semantic attributes**: href, type, name, placeholder, aria-label, role, value
- **Visible text content** (truncated at 150 chars per node)

### What Gets Stripped
- Scripts, styles, SVGs, canvases, iframes
- Hidden/invisible elements (display:none, visibility:hidden, zero-size)
- Non-semantic attributes: class, style, data-*, event handlers
- Deeply nested non-landmark containers
- Tracking/analytics attributes

### Compression Ratio
Typical: **95-99% reduction**. A 300KB HTML page compresses to ~3-5K tokens.

### Element Index Rules
- Indices `[1], [2], [3]...` are assigned to interactive elements in DOM order
- Indices are **ephemeral** — they reset on each `read`/`navigate`/`click`/`scroll`
- Always use the most recent indices from the last DOM read
- If an action changes the page, the response includes refreshed indices

---

## Common Patterns

### Browse and Navigate

```bash
# Launch, go to site, find and click a link
node BA launch &
sleep 2
node BA navigate https://example.com
# See [3]<a href="/pricing">Pricing</a> in output
node BA click 3
# Now on pricing page with new DOM
```

### Fill a Form

```bash
node BA navigate https://example.com/contact
# DOM shows: [5]<input name="email" placeholder="Email"> [6]<input name="name"> [7]<textarea> [8]<button>Send</button>
node BA type 5 "user@example.com"
node BA type 6 "John Doe"
node BA type 7 "Hello, I have a question about..."
node BA click 8
```

### Search a Site

```bash
node BA navigate https://example.com
# Find search input [5]<input placeholder="Search...">
node BA type 5 "my search query"
node BA press Enter
# Read search results
node BA read
```

### Extract Data

```bash
node BA navigate https://example.com/products
# Extract all product names
node BA extract "h3.product-name"
# Or extract from table
node BA extract "table tbody td:first-child"
```

### Multi-Tab Workflow

```bash
node BA navigate https://site-a.com
node BA newtab https://site-b.com
node BA tabs          # list both
node BA tab 0         # switch back to site-a
```

### Screenshot for Verification

```bash
node BA navigate https://example.com
node BA screenshot /tmp/page.png
# Then use Read tool to view the screenshot
```

---

## Workflow Rules

1. **Always launch first** — run `launch` in background before any other command
2. **Read before acting** — use `navigate` or `read` to see current DOM before clicking
3. **Use indices from latest read** — indices are ephemeral, always use the most recent ones
4. **Verify after important actions** — `click` auto-returns DOM; for `type`, follow with `read`
5. **Close when done** — always run `close` to free resources
6. **Handle errors gracefully** — if element not found, `read` to refresh indices
7. **Scroll for hidden content** — if target element not in DOM, scroll down and re-read
8. **Use extract for data** — for bulk text extraction, `extract` with CSS selector is more efficient than parsing DOM

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No browser session" | Run `launch` in background first |
| Element [N] not found | Run `read` to refresh element indices |
| Click didn't work | Element may have changed. `read` and try new index |
| Page not loading | Check URL starts with http/https. Increase wait time |
| Too much DOM output | Use `--max-depth 4` to reduce depth, or `extract` for specific data |
| Browser crashed | Run `close` then `launch` again |
| Can't see browser | Add `--headed` to `launch` command |
| Need login/cookies | Use `--headed` to manually log in, then automate from there |

---

## Testing Scenarios

| Scenario | Commands | Expected |
|----------|----------|----------|
| Basic navigation | `launch &` → `navigate https://example.com` | Returns compressed DOM with title |
| Click a link | `navigate` → find [N] → `click N` | New page DOM returned |
| Fill form | `navigate` → `type N text` → `click submit` | Form submitted |
| Extract data | `navigate` → `extract "h2"` | Array of h2 texts |
| Screenshot | `navigate` → `screenshot /tmp/test.png` | PNG file created |
| Multi-tab | `newtab url` → `tabs` → `tab 0` | Tab switching works |
| Scroll + read | `scroll down` → auto DOM refresh | New content visible |
| Cleanup | `close` | Browser process killed |
