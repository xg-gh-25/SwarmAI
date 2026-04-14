# Image Generation

Generate high-quality images for social media, blog posts, presentations, and creative projects using structured prompt engineering and available generation tools.

## Output Location

Save generated images and prompt files to:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/images/
```

For project-specific images:
```
~/.swarm-ai/SwarmWS/Projects/<ProjectName>/assets/
```

## Tool Detection

This skill is **tool-agnostic**. At runtime, detect what's available and use the best option:

| Priority | Tool | Detection | Best For |
|----------|------|-----------|----------|
| 1 | MCP image server | Check for `mcp__*__generate_image` tools | Full integration |
| 2 | DALL-E API via CLI | `which openai` or check for API key | High quality, fast |
| 3 | Stable Diffusion local | `which sd` or check ComfyUI | Privacy, no API cost |
| 4 | Midjourney via API | Check for MJ API key | Artistic quality |
| 5 | Manual prompt export | Always available | User runs elsewhere |

If no generation tool is available, produce the structured prompt JSON and instruct the user where to paste it.

## Workflow

### Step 1: Understand Requirements

Extract from the user's request:

| Dimension | Options | Default |
|-----------|---------|---------|
| **Subject** | What should be in the image | (required) |
| **Purpose** | Social post, blog hero, thumbnail, avatar, ad creative | Social post |
| **Style** | Photorealistic, illustration, flat design, watercolor, 3D render, anime, pixel art | Photorealistic |
| **Mood** | Energetic, calm, professional, playful, dark, warm | Match purpose |
| **Aspect ratio** | 1:1 (social), 16:9 (blog/video), 9:16 (stories), 4:5 (Instagram) | 1:1 |
| **Platform** | Instagram, Twitter/X, LinkedIn, YouTube, WeChat, TikTok, Xiaohongshu | General |
| **Reference images** | URLs or file paths for style/composition guidance | None |

### Step 2: Build Structured Prompt

Create a JSON prompt file that captures all dimensions:

```json
{
  "subject": {
    "description": "A developer working late at a minimalist desk setup",
    "key_elements": ["person", "laptop", "desk lamp", "coffee cup"],
    "action": "typing with focused expression"
  },
  "style": {
    "type": "photorealistic",
    "influences": ["editorial photography", "tech lifestyle"],
    "color_palette": ["warm amber", "deep navy", "soft white"],
    "lighting": "warm desk lamp with cool ambient background"
  },
  "composition": {
    "framing": "medium shot, slightly above eye level",
    "focal_point": "person's hands on keyboard",
    "background": "blurred city lights through window",
    "negative_space": "left third for text overlay"
  },
  "technical": {
    "aspect_ratio": "16:9",
    "resolution": "1920x1080",
    "purpose": "blog hero image"
  },
  "negative_prompt": "text, watermark, logo, blurry, distorted hands, extra fingers"
}
```

### Step 3: Platform-Specific Optimization

Apply platform-specific adjustments:

| Platform | Aspect Ratio | Size | Notes |
|----------|-------------|------|-------|
| Instagram Feed | 1:1 or 4:5 | 1080x1080 / 1080x1350 | Bold colors, centered subject |
| Instagram Story | 9:16 | 1080x1920 | Leave top/bottom for UI elements |
| Twitter/X | 16:9 | 1200x675 | Horizontal, clear at small size |
| LinkedIn | 1.91:1 | 1200x627 | Professional, clean |
| YouTube Thumbnail | 16:9 | 1280x720 | High contrast, readable at 120px |
| WeChat Article | 2.35:1 | 900x383 | Cover image, centered subject |
| WeChat Moments | 1:1 | 1080x1080 | Similar to Instagram |
| Xiaohongshu | 3:4 | 1080x1440 | Lifestyle aesthetic, bright |
| TikTok | 9:16 | 1080x1920 | Bold, attention-grabbing |

### Step 4: Generate or Export

**If generation tool available:**
Execute the generation with the structured prompt. Save both the prompt JSON and the output image.

**If no tool available:**
Save the prompt JSON and provide:
1. A copy-paste ready prompt string optimized for the user's preferred tool
2. The negative prompt separately
3. Recommended settings (steps, CFG scale, sampler) if applicable

#### Prompt String Formats

**For DALL-E / ChatGPT:**
```
{style type} of {subject description}. {composition details}. {lighting}. {mood}. {color palette description}. {aspect ratio context}.
```

**For Midjourney:**
```
{subject description}, {style influences}, {lighting}, {color palette}, {composition} --ar {ratio} --style raw --v 6
```

**For Stable Diffusion:**
```
Positive: {detailed subject}, {style}, {lighting}, {colors}, {composition}, masterpiece, best quality, highly detailed
Negative: {negative prompt}, lowres, bad anatomy, bad hands, text, error, cropped, worst quality
```

### Step 5: Iterate

After showing results:
- Offer specific adjustments (lighting, color, composition, style)
- For reference-guided iteration, save previous output as reference for next generation
- Keep prompt history for the session so iterations build on each other

---

## Prompt Engineering Patterns

### Character/Person
```json
{
  "subject": {
    "description": "...",
    "physical": "age range, build, expression",
    "clothing": "specific garments, era, condition",
    "pose": "action or stance"
  }
}
```

### Scene/Environment
```json
{
  "subject": {
    "description": "...",
    "environment": "location, weather, time of day",
    "atmosphere": "mood keywords",
    "scale": "intimate/vast/aerial"
  }
}
```

### Product/Object
```json
{
  "subject": {
    "description": "...",
    "material": "texture, finish, material",
    "presentation": "floating, on surface, in context",
    "lighting": "studio/natural/dramatic"
  }
}
```

### Social Media Content Series
When generating a series (e.g., weekly social posts):
1. Define a **visual identity** (consistent colors, style, composition rules)
2. Create a **template prompt** with variables
3. Generate each image with the template, varying only the subject
4. Save the template for reuse

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No generation tool detected | Export prompt JSON + formatted strings for manual use |
| Hands/fingers look wrong | Add "detailed hands, correct anatomy" to positive prompt, strengthen negative prompt |
| Text in image is garbled | Never ask AI to render text -- add text in post-processing |
| Style inconsistent across series | Use same seed (if supported) + reference image from first generation |
| Image too busy for text overlay | Specify negative space in composition, use simpler backgrounds |
| Wrong aspect ratio | Double-check platform table, specify both ratio and pixel dimensions |
