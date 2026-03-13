#!/usr/bin/env node
/**
 * browser-agent.mjs — DOM-based browser automation for SwarmAI
 *
 * Architecture:
 *   - `launch` starts Chromium with --remote-debugging-port, saves CDP URL
 *   - All other commands connect via CDP (connectOverCDP) — pages/tabs persist across connections
 *   - DOM compression: strips invisible/non-interactive nodes, assigns [N] indices to interactive elements
 *   - Element map cached to /tmp for cross-command element reference
 *
 * Usage: node browser-agent.mjs <action> [args...]
 *
 * Session:
 *   launch [url] [--headed]     Start browser server (background). Optionally navigate.
 *   close                       Stop browser server
 *
 * Navigation:
 *   navigate <url>              Go to URL, return compressed DOM
 *   back / forward              Browser history navigation
 *   scroll <up|down> [amount]   Scroll page (default: 3 units)
 *
 * Reading:
 *   read [--max-depth N]        Get compressed DOM with element indices
 *   screenshot [path]           Screenshot (default: /tmp/browser-screenshot.png)
 *   extract <css-selector>      Extract text from matching elements
 *
 * Interaction:
 *   click <index>               Click element by [N] index
 *   type <index> <text>         Clear + type into element
 *   submit <index>              Submit a form element
 *   select <index> <value>      Select dropdown option
 *   hover <index>               Hover element
 *   press <key>                 Press key (Enter, Tab, Escape, etc.)
 *
 * Tabs:
 *   tabs                        List open tabs
 *   tab <index>                 Switch to tab by index
 *   newtab [url]                Open new tab
 *   closetab                    Close current tab
 *
 * Advanced:
 *   eval <js-expression>        Evaluate JS in page context
 *   wait <ms|selector>          Wait for time or element
 *   pdf [path]                  Save page as PDF
 */

import { writeFileSync, readFileSync, existsSync, mkdirSync } from 'fs';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// ─── Self-contained dependency resolution ──────────────────────────
// Install playwright in the skill's own directory, not the user's CWD.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const skillNodeModules = join(__dirname, 'node_modules');

if (!existsSync(join(skillNodeModules, 'playwright'))) {
  console.error('[browser-agent] Installing playwright (one-time setup)...');
  execSync('npm install --no-fund --no-audit', { cwd: __dirname, stdio: 'inherit' });
}

const { chromium } = await import(join(skillNodeModules, 'playwright', 'index.mjs'));

const STATE_FILE = '/tmp/.browser-agent-state.json';
const ELEMENT_MAP_FILE = '/tmp/.browser-agent-elements.json';
const DEFAULT_SCREENSHOT = '/tmp/browser-screenshot.png';

// ─── State Management ───────────────────────────────────────────────
function saveState(data) {
  writeFileSync(STATE_FILE, JSON.stringify({ ...data, ts: Date.now() }));
}

function loadState() {
  if (!existsSync(STATE_FILE)) return null;
  try {
    const s = JSON.parse(readFileSync(STATE_FILE, 'utf8'));
    return (s?.cdpUrl || s?.wsEndpoint) ? s : null;
  } catch { return null; }
}

function clearState() {
  try { writeFileSync(STATE_FILE, '{}'); } catch {}
  try { writeFileSync(ELEMENT_MAP_FILE, '[]'); } catch {}
}

// ─── Element Map ────────────────────────────────────────────────────
function saveElementMap(elements) {
  writeFileSync(ELEMENT_MAP_FILE, JSON.stringify(elements));
}

function loadElementMap() {
  if (!existsSync(ELEMENT_MAP_FILE)) return [];
  try { return JSON.parse(readFileSync(ELEMENT_MAP_FILE, 'utf8')) || []; }
  catch { return []; }
}

function getElement(idx) {
  const elements = loadElementMap();
  const el = elements.find(e => e.index === idx);
  if (!el) throw new Error(`Element [${idx}] not found. Run 'read' to refresh element indices. Available: [${elements.map(e=>e.index).join(',')}]`);
  return el;
}

// ─── Connect to Running Browser via CDP ─────────────────────────────
async function connect() {
  const state = loadState();
  const cdpUrl = state?.cdpUrl;
  if (!cdpUrl) {
    throw new Error('No browser session. Run: node browser-agent.mjs launch');
  }
  try {
    const browser = await chromium.connectOverCDP(cdpUrl);
    const contexts = browser.contexts();
    let context = contexts[0];
    if (!context) context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const pages = context.pages();
    const page = pages.length > 0 ? pages[pages.length - 1] : await context.newPage();
    return { browser, context, page };
  } catch (err) {
    clearState();
    throw new Error(`Cannot connect to browser (may have crashed). Re-run: node browser-agent.mjs launch\n${err.message}`);
  }
}

// ─── DOM Compression Engine ─────────────────────────────────────────
async function compressDOM(page, maxDepth = 15) {
  return await page.evaluate((maxDepth) => {
    const INTERACTIVE = new Set([
      'a','button','input','select','textarea','details','summary',
      'label','option'
    ]);
    const SKIP = new Set([
      'script','style','noscript','svg','path','meta','link','head',
      'br','hr','canvas','iframe','object','embed','template','slot',
      'colgroup','col','source','track'
    ]);
    const LANDMARK = new Set([
      'header','footer','nav','main','aside','section','article',
      'form','table','thead','tbody','tfoot','tr','th','td',
      'h1','h2','h3','h4','h5','h6','ul','ol','li','dl','dt','dd',
      'p','blockquote','pre','code','figure','figcaption','div'
    ]);
    const KEEP_ATTRS = new Set([
      'href','type','name','value','placeholder','aria-label',
      'aria-expanded','aria-selected','aria-checked','role','title',
      'alt','action','method','checked','disabled','readonly',
      'required','selected','multiple','min','max','pattern','for',
      'id'
    ]);

    let idx = 0;
    const elementMap = [];

    function isVisible(el) {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      if (parseFloat(style.opacity) === 0) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return false;
      return true;
    }

    function buildSelector(el) {
      if (el.id) return `#${CSS.escape(el.id)}`;

      // Try aria-label
      const aria = el.getAttribute('aria-label');
      const tag = el.tagName.toLowerCase();
      if (aria) return `${tag}[aria-label="${CSS.escape(aria)}"]`;

      // Try name
      const name = el.getAttribute('name');
      if (name) return `${tag}[name="${CSS.escape(name)}"]`;

      // Try unique text content for buttons/links
      if ((tag === 'button' || tag === 'a') && el.textContent.trim().length < 50) {
        const text = el.textContent.trim();
        if (text) return `${tag}:text("${text.substring(0, 40)}")`;
      }

      // CSS path fallback
      const parts = [];
      let cur = el;
      for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
        let part = cur.tagName.toLowerCase();
        if (cur.id) { parts.unshift(`#${CSS.escape(cur.id)}`); break; }
        const parent = cur.parentElement;
        if (parent) {
          const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
          if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
        }
        parts.unshift(part);
        cur = parent;
      }
      return parts.join(' > ');
    }

    function compress(node, depth) {
      if (depth > maxDepth) return '';
      if (node.nodeType === 3) { // TEXT
        const t = node.textContent.trim().replace(/\s+/g, ' ');
        if (!t) return '';
        return t.length > 150 ? t.substring(0, 147) + '...' : t;
      }
      if (node.nodeType !== 1) return ''; // ELEMENT only

      const tag = node.tagName.toLowerCase();
      if (SKIP.has(tag)) return '';

      try { if (!isVisible(node)) return ''; } catch { return ''; }

      const isInteractive = INTERACTIVE.has(tag) ||
        node.getAttribute('role') && ['button','link','tab','menuitem','checkbox','radio','switch','option','combobox','textbox','searchbox'].includes(node.getAttribute('role')) ||
        node.getAttribute('contenteditable') === 'true' ||
        node.hasAttribute('tabindex') && node.tabIndex >= 0;

      // children
      const kids = [];
      for (const ch of node.childNodes) {
        const c = compress(ch, depth + 1);
        if (c) kids.push(c);
      }
      const inner = kids.join(' ').replace(/\s+/g, ' ').trim();

      if (isInteractive) {
        idx++;
        const attrs = {};
        for (const a of node.attributes) {
          if (KEEP_ATTRS.has(a.name)) {
            let v = a.value;
            if (a.name === 'href' && v.length > 80) v = v.substring(0, 77) + '...';
            if (v) attrs[a.name] = v;
          }
        }
        const displayText = inner || node.getAttribute('aria-label') || node.getAttribute('title') || node.getAttribute('placeholder') || node.getAttribute('alt') || '';
        const rect = node.getBoundingClientRect();

        elementMap.push({
          index: idx, tag,
          text: displayText.substring(0, 100),
          attrs,
          selector: buildSelector(node),
          rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
        });

        const attrStr = Object.entries(attrs)
          .filter(([k]) => k !== 'id') // id shown in selector, skip in output
          .map(([k,v]) => `${k}="${v}"`).join(' ');
        return `[${idx}]<${tag}${attrStr ? ' ' + attrStr : ''}>${displayText.substring(0,80)}</${tag}>`;
      }

      // Structural landmark or shallow depth: keep tag wrapper
      if (LANDMARK.has(tag) && inner) {
        const role = node.getAttribute('role');
        const aria = node.getAttribute('aria-label');
        let open = `<${tag}`;
        if (role) open += ` role="${role}"`;
        if (aria) open += ` aria-label="${aria}"`;
        if (tag === 'form') {
          const act = node.getAttribute('action');
          if (act) open += ` action="${act}"`;
        }
        if (tag === 'a') {
          const href = node.getAttribute('href');
          if (href) open += ` href="${href.substring(0,60)}"`;
        }
        open += '>';
        // Skip div wrappers that add no semantic value
        if (tag === 'div' && !role && !aria && depth > 3) return inner;
        return `${open}${inner}</${tag}>`;
      }

      return inner;
    }

    const body = compress(document.body, 0);
    return {
      title: document.title,
      url: window.location.href,
      body,
      elementMap,
      stats: {
        totalElements: document.querySelectorAll('*').length,
        interactiveElements: idx,
        compressedLength: body.length
      }
    };
  }, maxDepth);
}

// Truncate DOM output to keep within token budget
function truncateDOM(body, maxChars = 12000) {
  if (body.length <= maxChars) return body;
  return body.substring(0, maxChars) + '\n... [truncated, use scroll or extract for more]';
}

// ─── Output helper ──────────────────────────────────────────────────
function out(obj) {
  console.log(JSON.stringify(obj, null, 2));
}

// ─── Main ───────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  const action = args[0];

  if (!action) {
    console.error('Usage: node browser-agent.mjs <action> [args...]');
    console.error('Run with --help for full action list');
    process.exit(1);
  }

  try {
    switch (action) {

      // ── Session ──────────────────────────────────────────────────
      case 'launch': {
        // Kill any existing browser first
        const old = loadState();
        if (old?.cdpUrl) {
          try {
            const port = new URL(old.cdpUrl).port;
            execSync(`lsof -ti:${port} | xargs kill -9 2>/dev/null`, { stdio: 'ignore' });
          } catch {}
        }

        const headless = process.env.BROWSER_HEADLESS !== 'false' && !args.includes('--headed');
        const CDP_PORT = 9222;

        // Launch Chromium directly with remote debugging
        const browser = await chromium.launch({
          headless,
          args: [
            '--no-sandbox',
            '--disable-blink-features=AutomationControlled',
            `--remote-debugging-port=${CDP_PORT}`,
          ],
        });

        const cdpUrl = `http://localhost:${CDP_PORT}`;
        saveState({ cdpUrl });

        // Connect via CDP to set up initial page
        const cdpBrowser = await chromium.connectOverCDP(cdpUrl);
        const contexts = cdpBrowser.contexts();
        const context = contexts[0] || await cdpBrowser.newContext({ viewport: { width: 1280, height: 800 } });
        const pages = context.pages();
        const page = pages[0] || await context.newPage();

        const url = args.find(a => a.startsWith('http'));
        if (url) {
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        }

        out({
          status: 'ok',
          message: 'Browser launched (CDP)',
          cdpUrl,
          url: page.url(),
          title: await page.title()
        });

        // Keep process alive — browser dies when this process exits
        await new Promise(() => {});
        break;
      }

      case 'close': {
        const state = loadState();
        if (state?.cdpUrl) {
          try {
            const port = new URL(state.cdpUrl).port;
            execSync(`lsof -ti:${port} | xargs kill -9 2>/dev/null`, { stdio: 'ignore' });
          } catch {}
        }
        // Also kill any launch process
        try { execSync(`pkill -f "browser-agent.mjs launch" 2>/dev/null`, { stdio: 'ignore' }); } catch {}
        clearState();
        out({ status: 'ok', message: 'Browser closed and cleaned up' });
        break;
      }

      // ── Navigation ───────────────────────────────────────────────
      case 'navigate': case 'goto': case 'go': {
        const url = args[1];
        if (!url) throw new Error('Usage: navigate <url>');
        const { browser, page } = await connect();
        await page.goto(url.startsWith('http') ? url : `https://${url}`, {
          waitUntil: 'domcontentloaded', timeout: 30000
        });
        await page.waitForTimeout(800);
        const dom = await compressDOM(page);
        saveElementMap(dom.elementMap);
        out({
          status: 'ok', url: dom.url, title: dom.title,
          stats: dom.stats,
          dom: truncateDOM(dom.body)
        });
        break;
      }

      case 'back': {
        const { browser, page } = await connect();
        await page.goBack({ waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(500);
        out({ status: 'ok', url: page.url(), title: await page.title() });
        break;
      }

      case 'forward': {
        const { browser, page } = await connect();
        await page.goForward({ waitUntil: 'domcontentloaded' });
        await page.waitForTimeout(500);
        out({ status: 'ok', url: page.url(), title: await page.title() });
        break;
      }

      case 'scroll': {
        const direction = args[1] || 'down';
        const amount = parseInt(args[2]) || 3;
        const { browser, page } = await connect();
        const delta = { down: [0,300], up: [0,-300], right: [300,0], left: [-300,0] };
        const [dx, dy] = (delta[direction] || delta.down).map(v => v * amount);
        await page.mouse.wheel(dx, dy);
        await page.waitForTimeout(500);
        const dom = await compressDOM(page);
        saveElementMap(dom.elementMap);
        out({
          status: 'ok', action: `scrolled ${direction} x${amount}`,
          stats: dom.stats,
          dom: truncateDOM(dom.body)
        });
        break;
      }

      // ── Reading ──────────────────────────────────────────────────
      case 'read': case 'dom': {
        const maxDepth = args.includes('--max-depth')
          ? parseInt(args[args.indexOf('--max-depth') + 1]) : 15;
        const { browser, page } = await connect();
        const dom = await compressDOM(page, maxDepth);
        saveElementMap(dom.elementMap);
        out({
          status: 'ok', url: dom.url, title: dom.title,
          stats: dom.stats,
          dom: truncateDOM(dom.body)
        });
        break;
      }

      case 'screenshot': case 'ss': {
        const path = args.find(a => !a.startsWith('-') && a !== action) || DEFAULT_SCREENSHOT;
        const fullPage = args.includes('--full');
        const { browser, page } = await connect();
        await page.screenshot({ path, fullPage });
        out({ status: 'ok', path, url: page.url(), title: await page.title() });
        break;
      }

      case 'extract': {
        const selector = args[1];
        if (!selector) throw new Error('Usage: extract <css-selector>');
        const { browser, page } = await connect();
        const texts = await page.locator(selector).allTextContents();
        out({
          status: 'ok', selector,
          count: texts.length,
          texts: texts.map(t => t.trim()).filter(Boolean).slice(0, 50)
        });
        break;
      }

      // ── Interaction ──────────────────────────────────────────────
      case 'click': {
        const idx = parseInt(args[1]);
        if (isNaN(idx)) throw new Error('Usage: click <element-index>');
        const el = getElement(idx);
        const { browser, page } = await connect();

        let clicked = false;
        // Strategy 1: Playwright text selector (most stable for buttons/links)
        if ((el.tag === 'button' || el.tag === 'a') && el.text) {
          try {
            await page.getByRole(el.tag === 'a' ? 'link' : 'button', { name: el.text.substring(0, 40) }).first().click({ timeout: 3000 });
            clicked = true;
          } catch {}
        }
        // Strategy 2: CSS selector
        if (!clicked) {
          try {
            const sel = el.selector.includes(':text(') ? null : el.selector;
            if (sel) {
              await page.locator(sel).first().click({ timeout: 3000 });
              clicked = true;
            }
          } catch {}
        }
        // Strategy 3: Coordinate fallback
        if (!clicked && el.rect && el.rect.w > 0) {
          await page.mouse.click(el.rect.x + el.rect.w / 2, el.rect.y + el.rect.h / 2);
          clicked = true;
        }
        if (!clicked) throw new Error(`Could not click [${idx}]. Try 'read' to refresh elements.`);

        await page.waitForTimeout(800);
        const dom = await compressDOM(page);
        saveElementMap(dom.elementMap);
        out({
          status: 'ok',
          action: `clicked [${idx}] <${el.tag}> "${el.text}"`,
          url: dom.url, title: dom.title,
          stats: dom.stats,
          dom: truncateDOM(dom.body)
        });
        break;
      }

      case 'type': case 'input': {
        const idx = parseInt(args[1]);
        const text = args.slice(2).join(' ');
        if (isNaN(idx) || !text) throw new Error('Usage: type <element-index> <text>');
        const el = getElement(idx);
        const { browser, page } = await connect();

        const sel = el.selector.includes(':text(') ? null : el.selector;
        if (sel) {
          const loc = page.locator(sel).first();
          await loc.click({ timeout: 3000 });
          await loc.fill(text);
        } else if (el.rect && el.rect.w > 0) {
          await page.mouse.click(el.rect.x + el.rect.w / 2, el.rect.y + el.rect.h / 2);
          await page.keyboard.press('Control+a');
          await page.keyboard.type(text);
        } else {
          throw new Error(`Cannot type into [${idx}]. Try 'read' to refresh.`);
        }
        await page.waitForTimeout(300);
        out({ status: 'ok', action: `typed "${text}" into [${idx}] <${el.tag}>`, url: page.url() });
        break;
      }

      case 'submit': {
        const idx = parseInt(args[1]);
        if (isNaN(idx)) throw new Error('Usage: submit <form-element-index>');
        const el = getElement(idx);
        const { browser, page } = await connect();
        const sel = el.selector.includes(':text(') ? null : el.selector;
        if (sel) {
          await page.locator(sel).first().evaluate(form => {
            if (form.submit) form.submit();
            else form.closest('form')?.submit();
          });
        }
        await page.waitForTimeout(1000);
        out({ status: 'ok', action: `submitted form [${idx}]`, url: page.url() });
        break;
      }

      case 'select': {
        const idx = parseInt(args[1]);
        const value = args[2];
        if (isNaN(idx) || !value) throw new Error('Usage: select <index> <value>');
        const el = getElement(idx);
        const { browser, page } = await connect();
        const sel = el.selector.includes(':text(') ? null : el.selector;
        if (sel) await page.locator(sel).first().selectOption(value);
        out({ status: 'ok', action: `selected "${value}" in [${idx}]` });
        break;
      }

      case 'hover': {
        const idx = parseInt(args[1]);
        if (isNaN(idx)) throw new Error('Usage: hover <index>');
        const el = getElement(idx);
        const { browser, page } = await connect();
        const sel = el.selector.includes(':text(') ? null : el.selector;
        if (sel) {
          await page.locator(sel).first().hover({ timeout: 3000 });
        } else if (el.rect) {
          await page.mouse.move(el.rect.x + el.rect.w / 2, el.rect.y + el.rect.h / 2);
        }
        await page.waitForTimeout(500);
        const dom = await compressDOM(page);
        saveElementMap(dom.elementMap);
        out({
          status: 'ok', action: `hovered [${idx}] <${el.tag}> "${el.text}"`,
          dom: truncateDOM(dom.body)
        });
        break;
      }

      case 'press': case 'key': {
        const key = args[1];
        if (!key) throw new Error('Usage: press <key> (Enter, Tab, Escape, ArrowDown, etc.)');
        const { browser, page } = await connect();
        await page.keyboard.press(key);
        await page.waitForTimeout(300);
        out({ status: 'ok', action: `pressed ${key}` });
        break;
      }

      // ── Tabs ─────────────────────────────────────────────────────
      case 'tabs': {
        const { browser, context } = await connect();
        const pages = context.pages();
        const tabs = await Promise.all(pages.map(async (p, i) => ({
          index: i, url: p.url(), title: await p.title().catch(() => '')
        })));
        out({ status: 'ok', tabs, count: tabs.length });
        break;
      }

      case 'tab': {
        const tabIdx = parseInt(args[1]);
        if (isNaN(tabIdx)) throw new Error('Usage: tab <index>');
        const { browser, context } = await connect();
        const pages = context.pages();
        if (tabIdx < 0 || tabIdx >= pages.length) throw new Error(`Tab ${tabIdx} not found. Have ${pages.length} tabs.`);
        await pages[tabIdx].bringToFront();
        out({ status: 'ok', switchedTo: tabIdx, url: pages[tabIdx].url() });
        break;
      }

      case 'newtab': {
        const url = args[1];
        const { browser, context } = await connect();
        const newPage = await context.newPage();
        if (url) await newPage.goto(url.startsWith('http') ? url : `https://${url}`, {
          waitUntil: 'domcontentloaded', timeout: 30000
        });
        out({ status: 'ok', url: newPage.url(), tabCount: context.pages().length });
        break;
      }

      case 'closetab': {
        const { browser, context, page } = await connect();
        await page.close();
        out({ status: 'ok', tabsRemaining: context.pages().length });
        break;
      }

      // ── Advanced ─────────────────────────────────────────────────
      case 'eval': case 'js': {
        const expr = args.slice(1).join(' ');
        if (!expr) throw new Error('Usage: eval <js-expression>');
        const { browser, page } = await connect();
        const result = await page.evaluate(expr);
        out({ status: 'ok', result });
        break;
      }

      case 'wait': {
        const target = args[1];
        if (!target) throw new Error('Usage: wait <ms|css-selector>');
        const { browser, page } = await connect();
        if (/^\d+$/.test(target)) {
          await page.waitForTimeout(parseInt(target));
        } else {
          await page.waitForSelector(target, { timeout: 10000 });
        }
        out({ status: 'ok', waited: target });
        break;
      }

      case 'pdf': {
        const path = args[1] || '/tmp/browser-page.pdf';
        const { browser, page } = await connect();
        await page.pdf({ path, format: 'A4' });
        out({ status: 'ok', path });
        break;
      }

      default:
        console.error(`Unknown action: "${action}". Run without args for usage.`);
        process.exit(1);
    }
  } catch (err) {
    console.error(JSON.stringify({
      status: 'error', action,
      message: err.message,
      hint: err.message.includes('No browser') ? 'Run: node browser-agent.mjs launch' :
            err.message.includes('not found') ? 'Run: node browser-agent.mjs read' : undefined
    }, null, 2));
    process.exit(1);
  }

  // For non-launch commands, exit explicitly (WebSocket keeps process alive otherwise)
  if (action !== 'launch') process.exit(0);
}

main();
