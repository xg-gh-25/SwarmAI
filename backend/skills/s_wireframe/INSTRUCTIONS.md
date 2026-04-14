# Wireframe Skill

Generate visual wireframes from natural language. Output is an Excalidraw-compatible scene that opens instantly in the browser — no install, no account, no friction.

## Workflow

### Step 1: Parse the Request

Extract from the user's description:

| Dimension | Question | Default |
|-----------|----------|---------|
| **Screen type** | Mobile, tablet, desktop, responsive? | Desktop (1280x800) |
| **Page type** | Landing, dashboard, settings, form, list, detail? | Infer from description |
| **Components** | What UI elements are needed? | Infer from page type |
| **Flow** | Single screen or multi-screen flow? | Single screen |
| **Fidelity** | Lo-fi boxes or mid-fi with labels? | Mid-fi (labeled boxes) |

If the request is clear enough, skip clarification and generate directly.

### Step 2: Map to Excalidraw Elements

Use this component library to translate UI concepts into Excalidraw shapes:

#### Layout Primitives

```
Screen frame:     rectangle, w=375/768/1280 depending on device, strokeWidth=2
Section:          rectangle, dashed border, with text label
Container:        rectangle, light fill (#f5f5f5), rounded corners
Divider:          line, full width, strokeWidth=1, opacity=0.3
```

#### Common Components

```
Navbar:           rectangle h=64, full width, dark fill (#333) + white text labels
Sidebar:          rectangle w=240, full height, light fill + stacked text items
Button (primary): rectangle, rounded, blue fill (#228be6), white text, w=120 h=40
Button (secondary): rectangle, rounded, stroke only, dark text
Text input:       rectangle h=40, light stroke, with placeholder text inside
Text area:        rectangle h=100, light stroke, placeholder text
Checkbox:         small square (16x16) + text label offset right
Radio:            small circle (16x16) + text label offset right
Toggle:           rounded rectangle (40x20) with circle inside
Dropdown:         rectangle h=40 with down-arrow text "v" at right
Card:             rectangle with shadow (roughness=0), internal text hierarchy
Avatar:           circle d=40, gray fill, initials or "img" text
Icon placeholder: circle d=24, gray fill, "X" text centered
Image placeholder: rectangle, diagonal lines (two crossing lines corner-to-corner), "Image" text centered
Table:            grid of rectangles, header row darker fill
List item:        rectangle full-width h=60, left icon circle + two text lines
Tab bar:          row of text labels, active one underlined
Breadcrumb:       text items joined with ">" arrows
Modal:            centered rectangle with shadow + overlay note
Toast:            small rounded rectangle, top-right position
```

#### Typography (Excalidraw text elements)

```
Page title:       fontSize=28, fontFamily=1 (hand-drawn)
Section heading:  fontSize=20, fontFamily=1
Body text:        fontSize=16, fontFamily=1
Caption/label:    fontSize=14, fontFamily=1, opacity=0.6
Placeholder:      fontSize=14, fontFamily=1, opacity=0.4
```

#### Annotations

```
Arrow:            arrow element pointing to annotated component
Note:             text with background highlight, offset from main layout
Flow arrow:       arrow between screens (for multi-screen flows)
Numbering:        circled numbers (1, 2, 3) for step sequences
```

### Step 3: Generate Excalidraw Scene JSON

Build a valid Excalidraw scene. The structure:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "swarmai-wireframe",
  "elements": [ ...array of elements... ],
  "appState": {
    "gridSize": 20,
    "viewBackgroundColor": "#ffffff"
  },
  "files": {}
}
```

Each element follows this schema:

```json
{
  "id": "<unique-id>",
  "type": "rectangle|ellipse|text|line|arrow|diamond|freedraw",
  "x": 0,
  "y": 0,
  "width": 100,
  "height": 50,
  "angle": 0,
  "strokeColor": "#1e1e1e",
  "backgroundColor": "transparent",
  "fillStyle": "solid",
  "strokeWidth": 2,
  "strokeStyle": "solid",
  "roughness": 1,
  "opacity": 100,
  "groupIds": [],
  "roundness": { "type": 3 },
  "seed": <random-int>,
  "version": 1,
  "versionNonce": <random-int>,
  "isDeleted": false,
  "boundElements": null,
  "updated": 1,
  "link": null,
  "locked": false,
  "text": "Label",
  "fontSize": 16,
  "fontFamily": 1,
  "textAlign": "center",
  "verticalAlign": "middle"
}
```

**Important rules:**
- Every element needs a unique `id` (use short random strings like `el_001`, `el_002`)
- `seed` and `versionNonce` should be random integers (use any number 1-999999)
- For text inside shapes, use `boundElements` to link text to its container
- Group related elements with shared `groupIds` for easy selection
- Use `roughness: 1` for hand-drawn wireframe feel, `roughness: 0` for clean lines
- Position elements on the 20px grid for alignment

### Step 4: Generate Self-Contained HTML

Wrap the scene JSON into a self-contained HTML file that redirects to Excalidraw:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Wireframe: {description}</title>
  <style>
    body {
      font-family: system-ui, -apple-system, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      background: #f8f9fa;
      color: #333;
    }
    .container {
      text-align: center;
      padding: 2rem;
    }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    p { color: #666; margin-bottom: 1.5rem; }
    .btn {
      display: inline-block;
      padding: 12px 24px;
      background: #228be6;
      color: white;
      border-radius: 8px;
      text-decoration: none;
      font-weight: 500;
      font-size: 1rem;
      transition: background 0.2s;
    }
    .btn:hover { background: #1c7ed6; }
    .alt { margin-top: 1rem; font-size: 0.85rem; color: #999; }
    .alt a { color: #228be6; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Wireframe: {title}</h1>
    <p>{component count} components | {screen type}</p>
    <a id="open-btn" class="btn" href="#">Open in Excalidraw</a>
    <div class="alt">
      <p>Or <a id="download-link" href="#" download="wireframe.excalidraw">download the .excalidraw file</a></p>
    </div>
  </div>
  <script>
    const scene = {SCENE_JSON};
    const json = JSON.stringify(scene);

    // Method 1: Excalidraw URL with hash
    const encoded = encodeURIComponent(json);
    const excalidrawUrl = `https://excalidraw.com/#json=${encoded}`;
    document.getElementById('open-btn').href = excalidrawUrl;

    // Method 2: Download .excalidraw file
    const blob = new Blob([json], { type: 'application/json' });
    document.getElementById('download-link').href = URL.createObjectURL(blob);
  </script>
</body>
</html>
```

### Step 5: Save and Open

**Output location:**

```
~/.swarm-ai/SwarmWS/Knowledge/Notes/prototypes/wireframe-{description}.html
~/.swarm-ai/SwarmWS/Knowledge/Notes/prototypes/wireframe-{description}.excalidraw
```

Always save **both** files side by side:
- `wireframe-{description}.html` — launcher page (open in browser)
- `wireframe-{description}.excalidraw` — raw Excalidraw JSON (for re-import/version control)

Create the output directory if it doesn't exist.

After saving, open in browser:
```bash
open ~/.swarm-ai/SwarmWS/Knowledge/Notes/prototypes/wireframe-{description}.html
```

Tell the user:
- The wireframe is open in their browser
- Click "Open in Excalidraw" to edit interactively
- They can download the `.excalidraw` file for later
- When done editing, they can ask "implement this wireframe" to generate code via the `frontend-design` skill

## Multi-Screen Flows

For flows (e.g., "wireframe the signup flow"):

1. Place screens side by side with 100px horizontal gap
2. Connect screens with labeled arrows showing transitions
3. Number the screens (Screen 1, Screen 2, ...)
4. Group each screen's elements together

```
[Screen 1: Landing] --"Sign Up"--> [Screen 2: Form] --"Submit"--> [Screen 3: Success]
     1280x800              100px gap           1280x800
```

## Common Page Templates

When the user says a generic page type, use these default layouts:

### Landing Page (Desktop 1280x800)
```
[Navbar: logo left, nav links right, CTA button]
[Hero: big heading, subtext, CTA button, illustration placeholder]
[Features: 3-column grid with icon + heading + text]
[Social proof: testimonial cards or logo strip]
[CTA section: heading + button, contrasting background]
[Footer: links grid, copyright]
```

### Dashboard (Desktop 1280x800)
```
[Top bar: logo, search, avatar]
[Sidebar: nav items with icons, 240px wide]
[Main: stat cards row + chart placeholder + data table]
```

### Settings Page (Desktop 1280x800)
```
[Top bar]
[Sidebar: settings categories]
[Main: form sections with labels, inputs, toggles, save button]
```

### Mobile App Screen (375x812)
```
[Status bar: time, icons]
[Nav bar: back arrow, title, action]
[Content area: scrollable]
[Tab bar: 4-5 icons with labels]
```

### Form Page (Desktop 1280x800)
```
[Navbar]
[Centered container max-600px]
  [Heading + description]
  [Form fields: label + input pairs, stacked]
  [Action row: Cancel + Submit buttons]
```

## Design-to-Code Bridge

When the user says "implement this wireframe" or "turn this into code":

1. Read the `.excalidraw` JSON file
2. Parse the component structure (groups, labels, hierarchy)
3. Hand off to the `frontend-design` skill with the extracted structure as the brief
4. The wireframe becomes the layout specification

This closes the loop: **Chat -> Wireframe -> Edit -> Implement**.

## Tips

- **Keep it rough** — Wireframes should look intentionally sketchy. Use `roughness: 1`.
- **Label everything** — Every box should have a text label. Unlabeled rectangles are ambiguous.
- **Use real-ish content** — "John Doe" not "Name", "$29/mo" not "Price", "Dashboard" not "Page Title".
- **Annotate decisions** — Use note elements to explain non-obvious choices.
- **Consistent spacing** — Use 20px grid. 20px padding inside containers, 20px gap between elements.
