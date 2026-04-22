# Peekaboo

macOS UI automation: capture screenshots, inspect UI elements, click, type, and drive any app visually. Enables "see and act" workflows — screenshot analysis, form filling, app testing, and visual debugging.

**Platform:** macOS only. Requires Screen Recording + Accessibility permissions.

---

## Quick Start

```
"Take a screenshot of the current screen"
"Click the Submit button in Safari"
"What's on my screen right now?"
"Fill in the login form"
```

---

## Setup

### Permissions Required

On first use, macOS will prompt for two permissions:

1. **Screen Recording** -- System Settings > Privacy & Security > Screen Recording
2. **Accessibility** -- System Settings > Privacy & Security > Accessibility

Grant access to the parent process (Terminal, VS Code, or whichever app runs peekaboo).

### Verify

```bash
peekaboo permissions
```

---

## Core Workflow: See -> Decide -> Act

The fundamental pattern for all UI automation:

### Step 1: See (Capture + Annotate)

```bash
# Annotated screenshot of entire screen -- shows element IDs
peekaboo see --annotate --path /tmp/screen.png

# Annotated screenshot of a specific app
peekaboo see --annotate --app "Safari" --path /tmp/safari.png

# Frontmost app
peekaboo see --annotate --app frontmost --path /tmp/front.png

# Specific window by title
peekaboo see --annotate --window-title "Settings" --path /tmp/settings.png

# JSON output for programmatic use (element map)
peekaboo see --annotate --app "Safari" --json
```

The `--annotate` flag overlays numbered markers (B1, T2, etc.) on interactive elements. Read the screenshot to identify targets.

**Element ID prefixes:**
| Prefix | Element Type |
|--------|-------------|
| B | Button |
| T | Text field / input |
| L | Link |
| M | Menu item |
| C | Checkbox |
| S | Slider |
| I | Image |
| G | Group / container |

### Step 2: Decide

Read the annotated screenshot. Identify the element to interact with by its ID (e.g., B3 for a button, T1 for a text field).

### Step 3: Act

```bash
# Click an element by ID
peekaboo click --on B3

# Click by text query
peekaboo click "Submit"

# Click at coordinates
peekaboo click --coords 500,300

# Click within a specific app
peekaboo click --on B3 --app "Safari"
```

---

## Commands Reference

### Capture & Vision

```bash
# Simple screenshot (no annotation)
peekaboo image --path /tmp/screenshot.png

# Screenshot of specific app
peekaboo image --app "Finder" --path /tmp/finder.png

# Annotated see (main tool for automation)
peekaboo see --annotate --app "Safari" --path /tmp/annotated.png

# AI-powered analysis of what's on screen
peekaboo see --analyze "What error message is shown?"

# Capture specific screen (multi-monitor)
peekaboo image --screen-index 1 --path /tmp/screen2.png
```

### Click

```bash
# By element ID from see --annotate
peekaboo click --on B1
peekaboo click --on T3

# By text content
peekaboo click "OK"
peekaboo click "Save"

# By coordinates
peekaboo click --coords 400,250

# Double-click
peekaboo click --on B1 --double

# Right-click
peekaboo click --on B1 --right

# Click in specific app context
peekaboo click --on B1 --app "Finder"
```

### Type

```bash
# Type text into focused element
peekaboo type "Hello world"

# Type into specific app
peekaboo type "search query" --app "Safari"

# Type with delay between characters (human-like)
peekaboo type "slow typing" --delay 50
```

### Keyboard Shortcuts

```bash
# Common shortcuts
peekaboo hotkey cmd+c          # Copy
peekaboo hotkey cmd+v          # Paste
peekaboo hotkey cmd+a          # Select all
peekaboo hotkey cmd+s          # Save
peekaboo hotkey cmd+w          # Close window
peekaboo hotkey cmd+tab        # Switch app
peekaboo hotkey cmd+shift+3    # macOS screenshot

# In specific app
peekaboo hotkey cmd+t --app "Safari"    # New tab

# Press individual keys
peekaboo press enter
peekaboo press escape
peekaboo press tab
peekaboo press delete
peekaboo press space
```

### Scroll

```bash
# Scroll down
peekaboo scroll down

# Scroll up
peekaboo scroll up

# Scroll specific amount
peekaboo scroll down --amount 5

# Scroll in specific app
peekaboo scroll down --app "Safari"

# Scroll at coordinates
peekaboo scroll down --coords 400,300
```

### Drag & Drop

```bash
# Drag from one point to another
peekaboo drag --from 100,200 --to 400,500

# Drag with human-like motion
peekaboo drag --from 100,200 --to 400,500 --profile human

# Drag element by ID
peekaboo drag --on B1 --to 400,500
```

### App Control

```bash
# List running apps
peekaboo app list

# Launch an app
peekaboo app launch "Safari"

# Quit an app
peekaboo app quit "TextEdit"

# Focus/activate an app
peekaboo app activate "Finder"

# Hide an app
peekaboo app hide "Safari"
```

### Window Management

```bash
# List all windows
peekaboo window list

# List windows for specific app
peekaboo window list --app "Safari"

# List as JSON (get window IDs)
peekaboo window list --json

# Move a window
peekaboo window move --window-id 1234 --x 0 --y 0

# Resize a window
peekaboo window resize --window-id 1234 --width 1200 --height 800

# Focus a window
peekaboo window focus --window-id 1234
```

### Menu Interaction

```bash
# Click a menu item
peekaboo menu "File" "Save As..."

# List menu items for an app
peekaboo menu --app "Safari" --list
```

### Clipboard

```bash
# Read clipboard
peekaboo clipboard read

# Write to clipboard
peekaboo clipboard write "copied text"

# Paste (set clipboard then Cmd+V)
peekaboo paste "text to paste"
```

---

## Common Patterns

### Screenshot Analysis

When user asks "what's on my screen?":

```bash
# Capture and read
peekaboo image --path /tmp/current-screen.png
```

Then read the image file to describe what's visible.

### Fill a Form

```bash
# 1. See the form
peekaboo see --annotate --app "Safari" --path /tmp/form.png
# Read screenshot to identify fields: T1=Name, T2=Email, B5=Submit

# 2. Click first field and type
peekaboo click --on T1
peekaboo type "John Doe"

# 3. Tab to next or click
peekaboo press tab
peekaboo type "john@example.com"

# 4. Submit
peekaboo click --on B5
```

### Navigate a GUI App

```bash
# 1. See current state
peekaboo see --annotate --app "System Settings" --path /tmp/settings.png

# 2. Click sidebar item
peekaboo click "Wi-Fi"

# 3. See updated state
peekaboo see --annotate --app "System Settings" --path /tmp/wifi.png
```

### Multi-Step App Automation

```bash
# Open Safari, navigate, interact
peekaboo app launch "Safari"
sleep 1
peekaboo hotkey cmd+l                    # Focus address bar
peekaboo type "https://example.com"
peekaboo press enter
sleep 2
peekaboo see --annotate --app "Safari" --path /tmp/page.png
# Read screenshot, then click target elements
```

### Verify UI State

After performing an action, always re-capture to verify:

```bash
# Do action
peekaboo click --on B3

# Verify result
sleep 1
peekaboo see --annotate --app "Safari" --path /tmp/after-click.png
# Read screenshot to confirm expected state
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Screen Recording permission required" | System Settings > Privacy & Security > Screen Recording > enable for Terminal/VS Code |
| "Accessibility permission required" | System Settings > Privacy & Security > Accessibility > enable for Terminal/VS Code |
| Element not found | Re-run `see --annotate` -- UI may have changed. Try text query instead of ID |
| Click hits wrong spot | Use `see --annotate` to verify element positions. Try `--coords` for precision |
| App not responding to input | Ensure app is focused: `peekaboo app activate "AppName"` first |
| Screenshot is black | Screen Recording permission not granted or screen is locked |
| Slow response | Reduce capture area with `--app` instead of full screen |
| stale element IDs | IDs reset on each `see` capture. Always re-capture before clicking |

---

## Quality Rules

- Always **see before acting** -- capture an annotated screenshot before clicking
- Always **verify after acting** -- re-capture to confirm the action worked
- Use `--app` to scope captures to the relevant application
- Use `--annotate` for any interaction workflow (not just `image`)
- Element IDs are ephemeral -- they change on each `see` capture
- Add `sleep` between steps (0.5-2s) to let UI update
- Prefer element IDs (`--on B3`) over coordinates for reliability
- Fall back to text queries (`click "Submit"`) when IDs are ambiguous
- For sensitive actions (delete, send, submit), confirm with user before clicking
- Never automate password entry or sensitive credential input without explicit approval

---

## Testing

| Scenario | Expected Behavior |
|----------|-------------------|
| "Screenshot my screen" | Captures, reads, describes content |
| "Click button X in app Y" | See -> identify -> click -> verify |
| "Fill in this form" | See form -> identify fields -> type in each -> submit |
| Permission not granted | Guides user to System Settings |
| Element not found after see | Retries with different query or coordinates |
| Multi-step automation | See -> act -> verify at each step |

