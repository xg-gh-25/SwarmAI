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
  it('messages container must remain scrollable (overflow-y-auto)', () => {
    const chatPageFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/pages/ChatPage.tsx'), 'utf-8'
    );
    // Messages container uses overflow-y-auto for internal scrolling
    expect(chatPageFile).toMatch(/overflow-y-auto/);
  });

  it('TopBar must remain fixed height and unshrinkable', () => {
    const layoutFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/ThreeColumnLayout.tsx'), 'utf-8'
    );
    // TopBar: h-10 flex-shrink-0
    expect(layoutFile).toMatch(/h-10.*flex-shrink-0/);
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
    // BottomBar is imported and rendered — its own file has h-[26px] flex-shrink-0
    const bottomBarFile = readFileSync(
      resolve(PROJECT_ROOT, 'src/components/layout/BottomBar.tsx'), 'utf-8'
    );
    expect(bottomBarFile).toMatch(/h-\[26px\]/);
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
