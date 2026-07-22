# Brand Assets — supply your own

This DDD-native pollinate ships NO brand binaries. SwarmAI's own assets (Swarm logo,
QR codes, BGM) were intentionally stripped — a portable DDD is not SwarmAI-branded.

Provide your project's own assets here before pollinating:
- `logo/` — your logo/QR (referenced by poster/video tracks)
- `bgm/<name>.mp3` — background music; set `audio.default_bgm` in `../identity.yaml`

If absent: video/poster tracks degrade gracefully (no logo overlay, no BGM mix) —
the same fail-soft behavior as a missing ffmpeg/TTS tool.
