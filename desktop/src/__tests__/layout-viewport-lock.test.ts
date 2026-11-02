/**
 * Bug-condition tests: Layout viewport lock (Tailwind v4 regression).
 *
 * Root cause: Tailwind v4 (@import "tailwindcss") preflight zeroes body margin
 * but does NOT set height/overflow on html/body/#root. Without explicit
 * viewport-lock, the document is unconstrained and the window scrolls.
 *
 * Bug-condition method:
 * - ERROR BEHAVIOR (what was happening) → must NOT happen
 * - EXPECTED BEHAVIOR (what should happen) → must happen
 * - MUST NOT CHANGE (side effects that must remain intact)
 *
 * Fix: index.css adds `html, body, #root { height: 100%; margin: 0; overflow: hidden }`
 * ThreeColumnLayout: h-screen → h-full (lock to #root's 100%)
 * ChatInput: flex-shrink-0 (prevent compression)
 *
 * Testing methodology: Static analysis of CSS source + component class assertions.
 * JSDOM doesn't compute real layout, so we verify the structural invariants
 * (the rules exist, the classes are correct) rather than pixel measurements.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PROJECT_ROOT = resolve(__dirname, '../..');
const INDEX_CSS = readFileSync(resolve(PROJECT_ROOT, 'src/index.css'), 'utf-8');

/**
 * Check if a CSS rule block exists for given selector with given properties.
 */
function cssHasRule(css: string, selector: string, properties: string[]): boolean {
  // Find the selector (may be part of a grouped selector like "html, body, #root")
  const lines = css.split('\n');
  let inBlock = false;
  let braceDepth = 0;
  let blockContent = '';

  for (const line of lines) {
    if (!inBlock && line.includes(selector)) {
      inBlock = true;
      braceDepth = 0;
      blockContent = '';
    }
    if (inBlock) {
      blockContent += line + '\n';
      braceDepth += (line.match(/{/g) || []).length;
      braceDepth -= (line.match(/}/g) || []).length;
      if (braceDepth <= 0 && blockContent.includes('{')) {
        // Block closed — check properties
        const allPresent = properties.every(prop => blockContent.includes(prop));
        if (allPresent) return true;
        inBlock = false;
        blockContent = '';
      }
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// ERROR BEHAVIOR — must NOT happen
// ---------------------------------------------------------------------------

describe('Layout viewport lock — ERROR behavior (must NOT happen)', () => {
  it('html/body/#root must NOT lack height constraint (was: no height rule → document scrolls)', () => {
    // The bug: no height/overflow rules on html, body, or #root
    // Verify the fix exists — these rules MUST be present
    expect(cssHasRule(INDEX_CSS, 'html', ['height: 100%'])).toBe(true);
    expect(cssHasRule(INDEX_CSS, 'body', ['height: 100%'])).toBe(true);
    expect(cssHasRule(INDEX_CSS, '#root', ['height: 100%'])).toBe(true);
  });

  it('html/body/#root must NOT lack overflow constraint (was: document scrollable)', () => {
    expect(cssHasRule(INDEX_CSS, 'html', ['overflow: hidden'])).toBe(true);
    expect(cssHasRule(INDEX_CSS, 'body', ['overflow: hidden'])).toBe(true);
    expect(cssHasRule(INDEX_CSS, '#root', ['overflow: hidden'])).toBe(true);
  });

  it('ThreeColumnLayout must NOT use h-screen (was: 100vh disagrees with visible area)', () => {
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    // h-screen = 100vh, which can differ from visible area on macOS with dynamic toolbar
    // h-full = 100% of parent (#root) = correct
    expect(layoutFile).not.toMatch(/className="[^"]*h-screen[^"]*"/);
    expect(layoutFile).toMatch(/className="[^"]*h-full[^"]*"/);
  });

  it.each(['src/components/swarm-out/RunOverlay.tsx', 'src/components/layout/PipelineRunCard.tsx'])(
    'the big run card in %s must NOT sit under the macOS title bar (was: p-4 + 100vh height, title hidden)', (file) => {
      const src = readFileSync(resolve(PROJECT_ROOT, file), 'utf-8');
      // the window's title bar overlays its top 28px (TitleBarStyle::Overlay): the scrim
      // keeps the card 44px down, and the card's height is a share of the scrim, not of 100vh
      expect(src).toMatch(/className="fixed inset-0 z-\[60\][^"]*\bpt-11\b[^"]*"/);
      expect(src).toMatch(/h-\[min\(880px,100%\)\]/);
      expect(src).not.toMatch(/100vh/);
    });

  it('ChatInput must NOT lack flex-shrink-0 (was: input area compressed by flex)', () => {
    const chatInputFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/pages/chat/components/ChatInput.tsx'), 'utf-8'
    );
    // The root div of ChatInput's return must have flex-shrink-0
    expect(chatInputFile).toMatch(/className="[^"]*flex-shrink-0[^"]*"/);
  });
});

// ---------------------------------------------------------------------------
// EXPECTED BEHAVIOR — must happen
// ---------------------------------------------------------------------------

describe('Layout viewport lock — EXPECTED behavior (must happen)', () => {
  it('viewport lock rule must appear BEFORE base styles (specificity/order)', () => {
    // Viewport lock may be split into "html, body" + "#root" or combined "html, body, #root"
    const viewportLockIdx = Math.max(
      INDEX_CSS.indexOf('html, body, #root'),
      INDEX_CSS.indexOf('html, body {'),
    );
    const baseStylesIdx = INDEX_CSS.indexOf('/* Base styles */');
    expect(viewportLockIdx).toBeGreaterThan(-1);
    expect(baseStylesIdx).toBeGreaterThan(-1);
    expect(viewportLockIdx).toBeLessThan(baseStylesIdx);
  });

  it('viewport lock must set margin: 0 (prevent browser default margins)', () => {
    expect(cssHasRule(INDEX_CSS, 'html', ['margin: 0'])).toBe(true);
  });

  it('ThreeColumnLayout root must have overflow-hidden (clips own children)', () => {
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    expect(layoutFile).toMatch(/className="[^"]*overflow-hidden[^"]*"/);
  });

  it('height constraint chain: #root(100%) → ThreeColumnLayout(h-full=100%) → flex children', () => {
    // #root gets height: 100% from CSS
    expect(cssHasRule(INDEX_CSS, '#root', ['height: 100%'])).toBe(true);
    // ThreeColumnLayout uses h-full (= height: 100% of parent = #root)
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    expect(layoutFile).toMatch(/h-full/);
  });
});

// ---------------------------------------------------------------------------
// MUST NOT CHANGE — behaviors that must remain intact
// ---------------------------------------------------------------------------

describe('Layout viewport lock — MUST NOT CHANGE (preserved behaviors)', () => {
  it('messages container must remain scrollable (chat-scroll-gutter → overflow-y: scroll)', () => {
    // Migration Step 5.1: the scrollable messages container relocated from
    // ChatPage.tsx into the per-tab TabView (keep-mounted N-TabView render).
    // The scroll now comes from the shared `chat-scroll-gutter` utility (it owns
    // overflow + the side gutters so the transcript lines up with the composer),
    // so this guard checks both halves: TabView opts in, and the utility scrolls.
    const tabViewFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/pages/chat/components/TabView.tsx'), 'utf-8'
    );
    expect(tabViewFile).toMatch(/chat-scroll-gutter/);
    const utility = INDEX_CSS.match(/@utility chat-scroll-gutter\s*\{([^}]*)\}/);
    expect(utility).not.toBeNull();
    expect(utility![1]).toMatch(/overflow-y:\s*scroll/);
  });

  it('chat column geometry rules stay coupled (width rule, gutters, scrollbar compensation)', () => {
    // jsdom runs no CSS, so the geometry that aligns transcript, composer and
    // Welcome is pinned here as rule text. Each assertion names the coupling it
    // protects; the pixel result itself is measured by the pipeline's WKWebView probe.
    const body = (name: string) => {
      const m = INDEX_CSS.match(new RegExp(`@utility ${name}\\s*\\{([^}]*)\\}`));
      expect(m, `@utility ${name} missing`).not.toBeNull();
      return m![1];
    };
    // cqi resolves against the chat panel, not the viewport.
    expect(body('chat-panel')).toMatch(/container-type:\s*inline-size/);
    // 940 floor, grows with the panel, 1200 cap, never wider than the gutter box.
    expect(body('chat-column')).toMatch(/width:\s*min\(100%,\s*clamp\(940px,\s*70cqi,\s*1200px\)\)/);
    expect(body('chat-column')).toMatch(/margin-inline:\s*auto/);
    expect(body('chat-gutter')).toMatch(/padding-inline:\s*var\(--chat-gutter\)/);
    // A narrow panel (Canvas open, Swarm Out column) still gets a 20px gutter.
    expect(INDEX_CSS).toMatch(/--chat-gutter:\s*clamp\(20px,\s*2cqi,\s*28px\)/);
    // The scrollbar sits inside the right gutter: right = gutter - bar width.
    const scroll = body('chat-scroll-gutter');
    expect(scroll).toMatch(/padding-left:\s*var\(--chat-gutter\)/);
    expect(scroll).toMatch(/padding-right:\s*calc\(var\(--chat-gutter\)\s*-\s*var\(--scrollbar-size\)\)/);
    // The styled bar and the compensation read the same token.
    expect(INDEX_CSS).toMatch(/::-webkit-scrollbar\s*\{[^}]*width:\s*var\(--scrollbar-size\)/);
    // Chromium ignores ::-webkit-scrollbar once a standard scrollbar property is set.
    expect(INDEX_CSS).not.toMatch(/scrollbar-width\s*:|scrollbar-color\s*:/);
  });

  it('the chat panel is the container the column measures against', () => {
    const chatPage = readFileSync(resolve(PROJECT_ROOT, 'src/pages/ChatPage.tsx'), 'utf-8');
    // without it, 70cqi and --chat-gutter resolve against the viewport, not the panel
    expect(chatPage).toMatch(/className="chat-panel [^"]*flex-1/);
  });

  it('rate-limit row sits on the shared chat column', () => {
    const chatPage = readFileSync(resolve(PROJECT_ROOT, 'src/pages/ChatPage.tsx'), 'utf-8');
    expect(chatPage).toMatch(/<div className="chat-gutter py-2">\s*<div className="chat-column [^"]*text-yellow-400/);
  });

  it('TopBar must remain fixed height and unshrinkable', () => {
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    // TopBar: h-7 flex-shrink-0 (compact chrome, still fixed and unshrinkable)
    expect(layoutFile).toMatch(/h-7.*flex-shrink-0/);
  });

  it('ChatHeader must remain fixed height and unshrinkable', () => {
    const chatHeaderFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/pages/chat/components/ChatHeader.tsx'), 'utf-8'
    );
    // ChatHeader: h-10 ... flex-shrink-0
    expect(chatHeaderFile).toMatch(/h-10/);
    expect(chatHeaderFile).toMatch(/flex-shrink-0/);
  });

  it('BottomBar must remain fixed height and unshrinkable', () => {
    const _layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    // BottomBar is imported and rendered — its own file has h-[30px] flex-shrink-0
    const bottomBarFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/BottomBar.tsx'), 'utf-8'
    );
    expect(bottomBarFile).toMatch(/h-\[30px\]/);
    expect(bottomBarFile).toMatch(/flex-shrink-0/);
  });

  it('workspace explorer must remain independently scrollable', () => {
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    // Explorer panel has its own overflow handling
    expect(layoutFile).toMatch(/overflow-hidden/);
  });

  it('body must retain background-color and color (theme system)', () => {
    // The viewport lock must NOT remove the existing body styling
    expect(INDEX_CSS).toMatch(/body\s*\{[^}]*background-color/);
    expect(INDEX_CSS).toMatch(/body\s*\{[^}]*color:/);
  });

  it('html must retain font-size 13px (information-dense layout)', () => {
    expect(INDEX_CSS).toMatch(/html\s*\{[^}]*font-size:\s*13px/);
  });
});
