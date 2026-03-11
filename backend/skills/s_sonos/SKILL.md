---
name: Sonos Speaker Control
description: >
  Control Sonos speakers via the `sonos` CLI: playback, volume, grouping, queue, search & play music, favorites, scenes.
  TRIGGER: "sonos", "play music", "play song", "what's playing", "set volume", "pause music", "skip track", "next song", "stop music", "group speakers", "party mode".
  DO NOT USE: for Apple Music (not supported), non-Sonos Bluetooth speakers, or music production/editing.
---

# Sonos Speaker Control

Control Sonos speakers from the terminal using the `sonos` CLI (homebrew: `sonoscli`, binary: `sonos`).

## Quick Start

| User Says | Command |
|-----------|---------|
| "What's playing?" | `sonos status --name "Room"` |
| "Play Miles Davis in Kitchen" | `sonos smapi search "Miles Davis" --name "Kitchen" --open` |
| "Pause" | `sonos pause --name "Room"` |
| "Volume to 30" | `sonos volume set --name "Room" 30` |
| "Play everywhere" | `sonos group party --name "Room"` then `sonos play --name "Room"` |

## Step 1: Resolve Target Speaker

Before any command, determine which speaker to target:

1. If the user specifies a room name → use `--name "Room Name"`
2. If not specified → check config: `sonos config get` for `defaultRoom`
3. If no default set → run `sonos discover` and ask user which speaker
4. Always quote names with spaces: `--name "Living Room"`

## Step 2: Execute the Request

Map user intent to commands. See **REFERENCE.md** for the full command list.

### Common Patterns

**"Play [song/artist/album] on [speaker]"**
```bash
sonos smapi search "query" --name "Speaker" --open
```

**"Play [song/artist/album] everywhere"**
```bash
sonos group party --name "Speaker"
sonos smapi search "query" --name "Speaker" --open
```

**"What's playing on [speaker]?"**
```bash
sonos status --name "Speaker"
```

**"Set volume to N on [speaker]"**
```bash
sonos volume set --name "Speaker" N
```

**"Skip / Next / Previous"**
```bash
sonos next --name "Speaker"
sonos prev --name "Speaker"
```

**"Play my favorite [name]"**
```bash
sonos favorites list --name "Speaker"   # if name unknown
sonos favorites open --name "Speaker" "Favorite Name"
```

**"Save/restore a scene"**
```bash
sonos scene save "Scene Name"
sonos scene apply "Scene Name"
```

## Step 3: Confirm Result

After executing, verify with `sonos status --name "Speaker"` if the action was playback-related. Report the result to the user in natural language.

## Rules

1. **Prefer SMAPI over Spotify Web API** — SMAPI uses the linked Sonos account, no API keys needed
2. **Use `--format json`** when parsing output programmatically (e.g., extracting track info)
3. **"All speakers" / "everywhere"** → `sonos group party --name "Speaker"` first, then play
4. **`sonos watch` is long-running** — always use `--duration` or run in background with timeout
5. **Discovery first** — if no speaker is known and no defaultRoom configured, discover before acting
6. **Don't assume speaker names** — discover or ask the user

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "no speakers found" | Check network; speakers must be on same LAN; try `--timeout 10s` |
| SMAPI search fails | Run `sonos auth smapi` to authenticate the music service |
| "unknown speaker" | Run `sonos discover` to list available speakers |
| Volume not changing | Speaker may be grouped; try `sonos group volume set --name "Room" N` |
| Stream won't play | Use `sonos play-uri <url> --radio --title "Name"` for HTTP streams |
