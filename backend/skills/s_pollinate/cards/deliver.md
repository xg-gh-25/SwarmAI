# Card: deliver

The delivery is for the person, not for you. They should see the result at once and copy
what they need without reading anything twice.

## 1. Open the current version

- In the desktop app, open it in Canvas: `ui_action` with the command `open-canvas-file`
  and the workspace-relative path of the main output (`out/deck.html`, `out/poster.png`,
  `out/page.png`).
- A work outside the workspace, or a session with no Canvas: open the output in the browser
  (or give its absolute path) instead. The chat block below stays the same.

## 2. The chat block

One block, in the person's language. Show images inline as markdown images with the
**absolute path** of the PNG (`![slide 1](/abs/path/out/slides/01-s-7f3a.png)`); the chat
loads them from the workspace. For a work outside the workspace, the images may not show:
give their paths instead. Do not use the Read tool to show an image.

```
**<title>**: v003

- out/deck.html · v003 · 1920×1080, 14 slides
- out/deck.pdf · v003 · 14 pages, 3.1 MB
- out/deck.pptx · v003 · 14 slides, 5.4 MB

![slide 1](/abs/path/out/slides/01-s-2b1c.png) ![slide 3](/abs/path/out/slides/03-s-7f3a.png)

### 小红书
标题: <≤ 20 characters>
正文:
<the post text, ready to paste>
标签: #tag1 #tag2 #tag3 #tag4 #tag5

### 朋友圈
<one block of text, ready to paste>

<one paragraph, see below>
```

Rules:

- One line per output: file, version, size (pixels, pages or slides, and file size).
- Each platform's copy is plain text, ready to paste, with no notes mixed in. Use the
  platform's own fields: 小红书 has a title, body and tags; 朋友圈 is one block; X is one
  post (≤ 280 characters) or a short thread; LinkedIn is one post.
- Thumbnails: the cover and the slides this version changed (at most 6). A poster shows
  both variants the first time; after the person picks one, only that one.
- No gate tables, no check lists. When every check passed, the paragraph says so in a few
  words. When something failed or is open, say exactly what.

## 3. The paragraph

One paragraph, plain words, in this order:

1. **The thesis**, in one sentence.
2. **What you decided for them** (design system, size, platforms, the text you cut), so
   they can change it.
3. **What review found and what you fixed.** If no review ran, say that.
4. **What is left**: open findings, checks you could not pass, anything waiting for them.
5. **What this version changed**: "slides 3 (s-7f3a) and 7 (s-91c0) changed; slide 9 is new".

## 4. Stop

Never publish, post, upload or send. Publishing is always the person's click. Offer at
most one next step that fits (for example "a 9:16 story version"), and only if it is real.
