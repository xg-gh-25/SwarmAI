// The browser half of work_render.py: drives the pinned Chromium of SwarmAI's browser
// runtime (core/feature_map/runtime.py) for one render.
//
// Started by work_render.py with the runtime's Node and environment; the pinned
// @playwright/test entry point comes in POLLINATE_PLAYWRIGHT (a file: URL). Protocol:
// JSON lines. The driver first prints {"ready": true, "version"} once Chromium runs, or
// {"ready": false, "error"} and exits 3. Then it reads one request per line on stdin,
// {"id", "op", ...}, runs them one at a time in order, and prints one reply per request,
// {"id", "ok": true, "value"} or {"id", "ok": false, "error"}. When stdin closes it closes
// Chromium and exits 0.
//
// Requests ("page" is the id that "open" returned):
//   open    {w, h, url, allowed, js}  new context (viewport w x h, scale 1, JavaScript on
//           unless js is false) that lets through only the URLs in allowed and data:,
//           blob: and about: URLs; every other request and every WebSocket is aborted
//           and remembered. Opens
//           url and waits for load. Value: {page}.
//   eval    {page, js, arg?}           calls the function source js with arg in the page.
//   box     {page, selector, nth?}     bounding box {x, y, width, height} or null.
//   count   {page, selector}           how many elements match.
//   attr    {page, selector, nth, name}  an attribute value or null.
//   style   {page, css}                adds a <style> with css.
//   media   {page, media}              emulates "print" or "screen".
//   shot    {page, selector?, nth?, full_page?, type?, quality?}  a screenshot of the
//           element (or the page), animations off, caret hidden. Value: base64.
//   pdf     {page, path}               prints the page to path (backgrounds, CSS page size).
//   close   {page}                     closes the page's context.
//   blocked {}                         the blocked URLs so far, first seen first.
import readline from 'node:readline';

const NAV_TIMEOUT_MS = 60000;

const firstLine = (err) => String((err && err.message) || err).split('\n')[0].slice(0, 300);
const send = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
process.stdout.on('error', () => process.exit(1));   // the caller went away

let chromium;
try {
  ({ chromium } = await import(process.env.POLLINATE_PLAYWRIGHT));
} catch (err) {
  send({ ready: false, error: `the pinned Playwright could not be loaded: ${firstLine(err)}` });
  process.exit(3);
}
let browser;
try {
  browser = await chromium.launch({ timeout: NAV_TIMEOUT_MS });
} catch (err) {
  send({ ready: false, error: firstLine(err) });
  process.exit(3);
}
send({ ready: true, version: browser.version() });

const pages = new Map();
const blocked = [];
let nextPage = 1;

function pageOf(req) {
  const entry = pages.get(req.page);
  if (!entry) throw new Error(`no open page ${req.page}`);
  return entry.page;
}

function target(req) {
  const page = pageOf(req);
  if (!req.selector) return page;
  const loc = page.locator(req.selector);
  return req.nth === undefined || req.nth === null ? loc : loc.nth(req.nth);
}

const OPS = {
  async open(req) {
    const ctx = await browser.newContext({
      viewport: { width: req.w, height: req.h }, deviceScaleFactor: 1,
      javaScriptEnabled: req.js !== false,
    });
    const allowed = new Set(req.allowed || []);
    await ctx.route('**/*', (route) => {
      const url = route.request().url();
      if (allowed.has(url) || /^(data|blob|about):/.test(url)) return route.continue().catch(() => {});
      if (!blocked.includes(url)) blocked.push(url);
      return route.abort().catch(() => {});
    });
    await ctx.routeWebSocket(/.*/, (ws) => {     // page.route does not see WebSockets
      const url = ws.url();
      if (!blocked.includes(url)) blocked.push(url);
      ws.close();
    });
    ctx.setDefaultTimeout(NAV_TIMEOUT_MS);
    try {
      const page = await ctx.newPage();
      await page.goto(req.url, { waitUntil: 'load' });
      const id = nextPage++;
      pages.set(id, { ctx, page });
      return { page: id };
    } catch (err) {
      await ctx.close().catch(() => {});
      throw err;
    }
  },
  async eval(req) {
    const call = 'arg' in req ? `(${req.js})(${JSON.stringify(req.arg)})` : `(${req.js})()`;
    return await pageOf(req).evaluate(call);
  },
  async box(req) {
    return await target(req).boundingBox();
  },
  async count(req) {
    return await pageOf(req).locator(req.selector).count();
  },
  async attr(req) {
    return await target(req).getAttribute(req.name);
  },
  async style(req) {
    await pageOf(req).addStyleTag({ content: req.css });
    return null;
  },
  async media(req) {
    await pageOf(req).emulateMedia({ media: req.media });
    return null;
  },
  async shot(req) {
    const opts = { type: req.type || 'png', animations: 'disabled', caret: 'hide' };
    if (req.quality !== undefined && req.quality !== null) opts.quality = req.quality;
    if (req.full_page && !req.selector) opts.fullPage = true;
    const buf = await target(req).screenshot(opts);
    return buf.toString('base64');
  },
  async pdf(req) {
    await pageOf(req).pdf({ path: req.path, printBackground: true, preferCSSPageSize: true });
    return null;
  },
  async close(req) {
    const entry = pages.get(req.page);
    pages.delete(req.page);
    if (entry) await entry.ctx.close();
    return null;
  },
  async blocked() {
    return blocked.slice();
  },
};

const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  let req;
  try {
    req = JSON.parse(line);
  } catch (err) {
    send({ id: null, ok: false, error: `not a JSON request: ${firstLine(err)}` });
    continue;
  }
  const op = OPS[req.op];
  if (!op) {
    send({ id: req.id, ok: false, error: `unknown request '${req.op}'` });
    continue;
  }
  try {
    const value = await op(req);
    send({ id: req.id, ok: true, value: value === undefined ? null : value });
  } catch (err) {
    send({ id: req.id, ok: false, error: String((err && err.message) || err).slice(0, 2000) });
  }
}
await browser.close().catch(() => {});
process.exit(0);
