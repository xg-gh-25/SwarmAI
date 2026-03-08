# Sonos CLI Command Reference

Binary: `sonos` (installed via homebrew as `sonoscli`)

## Global Flags

| Flag | Description |
|------|-------------|
| `--name "Room"` | Target speaker by name |
| `--ip "192.168.1.x"` | Target speaker by IP |
| `--format plain\|json\|tsv` | Output format (default: plain) |
| `--debug` | Enable debug logging |
| `--timeout 10s` | Network timeout (default: 5s) |

## Discovery & Status

```bash
sonos discover                              # Find all speakers on LAN
sonos discover --all                        # Include hidden/bonded devices
sonos status --name "Room"                  # Current playback info
sonos status --name "Room" --format json    # Machine-readable status
```

## Playback Control

```bash
sonos play --name "Room"                    # Resume playback
sonos pause --name "Room"                   # Pause
sonos stop --name "Room"                    # Stop
sonos next --name "Room"                    # Skip forward
sonos prev --name "Room"                    # Go back
```

## Volume & Mute

```bash
sonos volume get --name "Room"              # Get current volume
sonos volume set --name "Room" 25           # Set volume (0-100)
sonos mute --name "Room"                    # Get/set mute
```

## Search & Play Music

### SMAPI (preferred — no API keys, uses linked Sonos account)

```bash
sonos smapi services                                          # List linked music services
sonos smapi search "query" --name "Room" --open               # Search & play immediately
sonos smapi search "query" --category tracks                  # Search tracks (default)
sonos smapi search "query" --category albums                  # Search albums
sonos smapi search "query" --category artists                 # Search artists
sonos smapi search "query" --category playlists               # Search playlists
sonos smapi search "query" --open --index 2                   # Play 2nd result
sonos smapi search "query" --enqueue --name "Room"            # Add to queue without playing
sonos smapi browse <container-id> --service "Spotify"         # Browse a container
sonos smapi categories --service "Spotify"                    # List search categories
```

### Spotify Web API (needs SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)

```bash
sonos search spotify "query" --name "Room" --open             # Search & play
sonos search spotify "query" --type track                     # Type: track (default)
sonos search spotify "query" --type album                     # Type: album
sonos search spotify "query" --type playlist                  # Type: playlist
sonos search spotify "query" --type show                      # Type: show (podcast)
sonos search spotify "query" --type episode                   # Type: episode
sonos search spotify "query" --limit 5                        # Limit results (1-50)
sonos search spotify "query" --open --index 3                 # Play 3rd result
sonos search spotify "query" --enqueue --name "Room"          # Add to queue
```

## Direct URI Playback

```bash
sonos open <spotify-uri> --name "Room"                        # Enqueue + play Spotify URI
sonos open <spotify-uri> --name "Room" --title "Track Name"   # With display title
sonos enqueue <spotify-uri> --name "Room"                     # Add to queue (no play)
sonos enqueue <spotify-uri> --name "Room" --next              # Enqueue as next (shuffle)
sonos play-uri <uri> --name "Room"                            # Play any URI
sonos play-uri <url> --name "Room" --radio --title "Station"  # Radio-style stream
```

## Queue Management

```bash
sonos queue list --name "Room"              # Show queue
sonos queue play --name "Room" 3            # Play queue item #3 (1-based)
sonos queue remove --name "Room" 3          # Remove item #3
sonos queue clear --name "Room"             # Clear entire queue
```

## Favorites

```bash
sonos favorites list --name "Room"          # List Sonos Favorites
sonos favorites open --name "Room" "Name"   # Play a favorite by title or index
```

## Grouping

```bash
sonos group status                                            # Show all groups
sonos group join --name "Bedroom" --ip <coordinator-ip>       # Join a group
sonos group solo --name "Room"                                # Leave group (play alone)
sonos group party --name "Room"                               # Join ALL speakers to this room
sonos group dissolve --name "Room"                            # Ungroup all members
sonos group volume --name "Room"                              # Get group volume
sonos group volume set --name "Room" 30                       # Set group volume
sonos group mute --name "Room"                                # Get/set group mute
```

## Scenes (Presets)

```bash
sonos scene save "Movie Night"              # Save current grouping + volumes
sonos scene list                            # List saved scenes
sonos scene apply "Movie Night"             # Restore a scene
sonos scene delete "Movie Night"            # Delete a scene
```

## Input Switching

```bash
sonos tv --name "Living Room"               # Switch to TV input
sonos linein --name "Living Room"           # Switch to line-in input
```

## Live Events

```bash
sonos watch --name "Room"                   # Stream events (Ctrl+C to stop)
sonos watch --name "Room" --duration 30s    # Watch for 30 seconds
sonos watch --name "Room" --format json     # JSON event output
```

## Config

```bash
sonos config get                            # Show all config
sonos config get defaultRoom                # Get specific key
sonos config set defaultRoom "Kitchen"      # Set default room
sonos config unset defaultRoom              # Remove a key
sonos config path                           # Show config file location
```

## Auth

```bash
sonos auth smapi                            # Authenticate a music service (DeviceLink/AppLink)
```
