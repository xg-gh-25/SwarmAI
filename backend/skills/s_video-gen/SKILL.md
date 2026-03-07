---
name: Video Generation
description: >
  Create videos using structured prompts, storyboards, and AI generation tools.
  TRIGGER: "generate video", "create video", "make a video", "video content", "short video", "video clip", "social video", "Reels", "TikTok video".
  DO NOT USE: for video editing of existing footage (use ffmpeg directly) or for static images (use image-gen).
version: "1.0.0"
---

# Video Generation

Generate short-form video content for social media, presentations, and creative projects using structured storyboarding, prompt engineering, and available generation tools.

## Output Location

Save video files and prompts to:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/videos/
```

Files per video:
- `YYYY-MM-DD-<topic>-storyboard.json` -- structured prompt/storyboard
- `YYYY-MM-DD-<topic>-reference.jpg` -- reference frame (if generated)
- `YYYY-MM-DD-<topic>.mp4` -- final video output

## Tool Detection

| Priority | Tool | Detection | Best For |
|----------|------|-----------|----------|
| 1 | MCP video server | Check for `mcp__*__generate_video` tools | Full integration |
| 2 | Runway API | Check for `RUNWAY_API_KEY` env var | High quality gen-3 |
| 3 | Pika API | Check for `PIKA_API_KEY` env var | Stylized short clips |
| 4 | ffmpeg + images | `which ffmpeg` + image-gen skill | Slideshow/montage from AI images |
| 5 | Prompt-only export | Always available | User runs on external platform |

If no video generation tool is available, produce the storyboard JSON and reference images for the user to generate elsewhere.

## Workflow

### Step 1: Understand Requirements

| Dimension | Options | Default |
|-----------|---------|---------|
| **Subject** | What the video shows | (required) |
| **Duration** | 3-5s clip, 15s short, 30s, 60s | 5s clip |
| **Style** | Cinematic, animated, documentary, product, social/casual | Cinematic |
| **Platform** | TikTok, Reels, YouTube Shorts, WeChat Channels, general | General |
| **Aspect ratio** | 16:9 (landscape), 9:16 (vertical), 1:1 (square) | 9:16 for social |
| **Reference image** | Starting frame or style guide | None |
| **Audio** | Music, voiceover, SFX, silent | Silent |

### Step 2: Build Storyboard

Create a structured JSON storyboard:

```json
{
  "title": "Product Launch Reveal",
  "duration": "5s",
  "aspect_ratio": "9:16",
  "scenes": [
    {
      "scene": 1,
      "duration": "5s",
      "description": "Sleek product floating in space, slowly rotating with dramatic lighting",
      "camera": {
        "type": "Orbiting shot",
        "movement": "Slow 45-degree orbit around subject",
        "angle": "Slightly below eye level, looking up",
        "focus": "Sharp on product, background bokeh"
      },
      "lighting": "Single key light from upper left, rim light from behind, dark environment",
      "mood": "Premium, mysterious, anticipation",
      "reference_prompt": "A premium tech product floating in dark space, dramatic side lighting, cinematic, product photography, 8K, volumetric light rays"
    }
  ],
  "audio": [
    {"type": "ambient", "description": "Deep bass drone building tension", "volume": 0.4},
    {"type": "sfx", "description": "Subtle whoosh on reveal", "volume": 0.7}
  ],
  "text_overlays": [
    {"text": "Coming Soon", "timestamp": "4s", "style": "minimal white, fade in"}
  ]
}
```

### Step 3: Generate Reference Frame

Before generating video, create a reference image that establishes the visual style:

1. Use the `image-gen` skill to generate a key frame
2. This reference image guides the video generation for visual consistency
3. Save as `*-reference.jpg` alongside the storyboard

**Reference frame prompt should include:**
- Exact subject description from storyboard
- Lighting and mood keywords
- Camera angle from scene 1
- Style keywords: "cinematic still frame", "movie screenshot", "film grain"

### Step 4: Generate or Export

**If video generation tool available:**

Execute generation with storyboard parameters:
- Pass the reference image as the starting/guiding frame
- Set duration, aspect ratio, and motion parameters
- Save output to the videos directory

**If only ffmpeg available (slideshow/montage approach):**

Generate 3-5 AI images using image-gen skill, then:

```bash
# Create a slideshow with Ken Burns effect
ffmpeg -framerate 1/3 -i frame_%03d.jpg \
  -vf "zoompan=z='min(zoom+0.001,1.5)':d=90:s=1080x1920,fade=t=in:st=0:d=1,fade=t=out:st=2:d=1" \
  -c:v libx264 -pix_fmt yuv420p -r 30 output.mp4
```

**If no tools available:**

Deliver:
1. Storyboard JSON
2. Reference images
3. Platform-specific prompt strings for Runway/Pika/Kling
4. Instructions for the user's preferred tool

#### Prompt Formats for External Tools

**Runway Gen-3:**
```
{subject description}. {camera movement}. {lighting}. {mood}. Cinematic quality.
```

**Pika:**
```
{subject}, {action/motion}, {style}, {camera movement}
```

**Kling (for Chinese social platforms):**
```
{detailed scene description in English}, {camera movement}, {style keywords}, cinematic, high quality
```

### Step 5: Platform Optimization

| Platform | Aspect Ratio | Duration | Notes |
|----------|-------------|----------|-------|
| TikTok | 9:16 | 15-60s | Hook in first 1s, text overlays, trending audio |
| Instagram Reels | 9:16 | 15-90s | Similar to TikTok, slightly more polished |
| YouTube Shorts | 9:16 | <60s | Can be more informational |
| WeChat Channels | 9:16 or 16:9 | 15-60s | More polished, less meme-y |
| Xiaohongshu | 9:16 or 3:4 | 15-60s | Lifestyle aesthetic, warm tones |
| LinkedIn | 16:9 or 1:1 | 30-120s | Professional, text overlays important |
| Twitter/X | 16:9 | 5-30s | Short, punchy, auto-plays muted |

### Step 6: Add Audio (Optional)

If audio is requested:

**Voiceover:** Use `podcast-gen` skill's TTS workflow for narration
**Background music:** Suggest royalty-free sources:
- Pixabay Music (free, no attribution)
- YouTube Audio Library (free for YouTube)
- Artlist / Epidemic Sound (subscription)

**Combine with ffmpeg:**
```bash
ffmpeg -i video.mp4 -i audio.mp3 -c:v copy -c:a aac -shortest output_with_audio.mp4
```

---

## Video Type Templates

### Product Reveal
- 5s, dark background, dramatic lighting
- Slow rotation or orbit camera
- Reveal text at end

### Social Media Promo
- 15-30s, bright/energetic
- Quick cuts between scenes (3-5 scenes)
- Text overlays with key message
- CTA at end

### Explainer/Tutorial
- 30-60s, clean/professional
- Step-by-step scenes
- Text labels on each step
- Voiceover recommended

### Aesthetic/Mood Content
- 5-15s, atmospheric
- Single continuous shot
- Minimal or no text
- Focus on mood and visual quality

### Before/After
- 5-10s, split or transition
- Clear comparison moment
- Works great for product/design showcases

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No video gen tool | Use ffmpeg slideshow from AI images as fallback |
| Video quality low | Use higher-res reference image, add quality keywords to prompt |
| Motion too fast/jarring | Specify "slow", "gentle", "smooth" in camera movement |
| Style inconsistent | Always use reference image from frame 1 to guide subsequent scenes |
| Audio sync issues | Use ffmpeg `-shortest` flag, trim audio to match video length |
| Vertical video looks wrong | Ensure reference images are generated in correct aspect ratio (9:16) |
| Social platform compression | Export at slightly higher quality than needed, platforms will re-encode |
