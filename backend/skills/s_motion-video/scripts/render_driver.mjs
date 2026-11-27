// The browser half of render.py: drives the pinned Chromium of SwarmAI's browser runtime
// (backend/core/feature_map/runtime.py) for one render. There is no Python Playwright.
//
// Started by render.py with the runtime's Node and an allowlisted environment; the pinned
// @playwright/test entry point comes in MV_PLAYWRIGHT (a file: URL) and the launch options as
// argv[2], a JSON object {args, timeout}.
//
// Protocol. stdin: one JSON request per line, {"id", "op", ...}. stdout: messages, each one
// JSON header line followed by exactly `bytes` raw bytes (0 for most replies; the JPEG of a
// frame or a shot). The first message is {"ready": true, "version"} once Chromium runs, or
// {"ready": false, "error"} and exit 3. Then every request gets one reply
// {"id", "ok": true, "value", "events"?, "bytes"} or {"id", "ok": false, "error", "events"?, "bytes": 0}.
// Requests run concurrently (one per scene page at a time; render.py renders --jobs scenes at
// once), so replies can come in any order: match them by id. When stdin closes, the driver
// finishes the requests in flight, closes Chromium and exits 0.
//
// "events" are what a page reported since the previous reply for that page, each a list:
// errors (uncaught page errors, first line), missing ("<status> <path>" of responses >= 400),
// blocked (URLs aborted because they are not under the page's base) and console (warnings and
// errors, "<type>: <text>"). An error reply carries the events too.
//
// Requests ("page" is the id that "open" returned):
//   open  {w, h, base, url, init, timeout}  new context (viewport w x h, scale 1) whose every
//         request must start with base (the local server) or is aborted and listed as blocked;
//         every WebSocket is closed (one not under base is listed as blocked); init runs before
//         any page script; opens url and waits for load. Value: {page}.
//   eval  {page, expr}                  evaluates the expression (a promise is awaited). Value: its result.
//   seek  {page, t, caption}            awaits window.__mvSeek(t), or __mvSeek(t, caption) when caption is not null.
//   shot  {page, quality}               JPEG screenshot of the viewport (CDP, as the frames). Bytes: the JPEG.
//   frame {page, t, caption, quality}   seek, then shot, in one request. Bytes: the JPEG.
//   cdp   {page, method, params}        one Chrome DevTools Protocol command on the page. Value: its result.
//   close {page}                        closes the page's context.
import readline from 'node:readline';

const firstLine = (err) => String((err && err.message) || err).split('\n')[0];
process.stdout.on('error', () => process.exit(1));   // the caller went away

// Ends the driver once everything written has reached the pipe (a pipe write can be asynchronous, on macOS, and
// process.exit right after it would drop the last line: the reason render.py reports).
const exitAfterFlush = (code) => process.stdout.write('', () => process.exit(code));

function send(head, payload) {
  const body = payload && payload.length ? payload : null;
  head.bytes = body ? body.length : 0;
  const line = Buffer.from(JSON.stringify(head) + '\n', 'utf8');
  process.stdout.write(body ? Buffer.concat([line, body]) : line);   // one write: a message is never split
}

const opts = (() => { try { return JSON.parse(process.argv[2] || '{}'); } catch { return {}; } })();
let chromium;
try {
  ({ chromium } = await import(process.env.MV_PLAYWRIGHT));
} catch (err) {
  send({ ready: false, error: `the pinned Playwright could not be loaded: ${firstLine(err)}` });
  await new Promise(() => exitAfterFlush(3));
}
let browser;
try {
  browser = await chromium.launch({ headless: true, args: opts.args || [], timeout: opts.timeout || 60000 });
} catch (err) {
  send({ ready: false, error: firstLine(err) });
  await new Promise(() => exitAfterFlush(3));
}
send({ ready: true, version: browser.version() });

const pages = new Map();
let nextPage = 1;
const freshEvents = () => ({ errors: [], missing: [], blocked: [], console: [] });

function takeEvents(entry) {
  if (!entry) return undefined;
  const ev = entry.ev;
  entry.ev = freshEvents();
  return Object.values(ev).some((v) => v.length) ? ev : undefined;
}

function entryOf(req) {
  const entry = pages.get(req.page);
  if (!entry) throw new Error(`no open page ${req.page}`);
  return entry;
}

async function seek(entry, req) {
  if (req.caption === null || req.caption === undefined) {
    await entry.page.evaluate((t) => window.__mvSeek(t), req.t);
  } else {
    await entry.page.evaluate(([t, c]) => window.__mvSeek(t, c), [req.t, req.caption]);
  }
}

async function shot(entry, req) {
  if (!entry.cdp) entry.cdp = await entry.ctx.newCDPSession(entry.page);
  const r = await entry.cdp.send('Page.captureScreenshot', {
    format: 'jpeg', quality: req.quality, optimizeForSpeed: true, captureBeyondViewport: false, fromSurface: true,
  });
  return Buffer.from(r.data, 'base64');
}

const OPS = {
  async open(req) {
    const base = String(req.base);
    const wsBase = base.replace(/^http/, 'ws');
    const ctx = await browser.newContext({
      viewport: { width: req.w, height: req.h }, deviceScaleFactor: 1, javaScriptEnabled: true, bypassCSP: false,
    });
    const entry = { ctx, page: null, cdp: null, ev: freshEvents() };
    const failed = (err) => Object.assign(err instanceof Error ? err : new Error(String(err)), { entry });
    try {
      await ctx.addInitScript(String(req.init || ''));
      await ctx.route('**/*', (route) => {
        const url = route.request().url();
        if (url.startsWith(base)) return route.continue().catch(() => {});
        entry.ev.blocked.push(url.slice(0, 200));
        return route.abort().catch(() => {});
      });
      await ctx.routeWebSocket(/.*/, (ws) => {     // ctx.route does not see WebSockets
        const url = ws.url();
        if (!url.startsWith(wsBase)) entry.ev.blocked.push(url.slice(0, 200));
        ws.close();
      });
      const page = await ctx.newPage();
      entry.page = page;
      const origin = base.replace(/\/$/, '');
      page.on('pageerror', (err) => entry.ev.errors.push(firstLine(err).slice(0, 300)));
      page.on('response', (r) => {
        if (r.status() >= 400) entry.ev.missing.push(`${r.status()} ${r.url().split(origin).join('')}`);
      });
      page.on('console', (m) => {
        const type = m.type(), text = m.text();
        if ((type === 'warning' || type === 'error') && !text.startsWith('Failed to load resource')) {
          entry.ev.console.push(`${type}: ${text.slice(0, 300)}`);
        }
      });
      await page.goto(req.url, { waitUntil: 'load', timeout: req.timeout || 20000 });
    } catch (err) {
      await ctx.close().catch(() => {});
      throw failed(err);
    }
    const id = nextPage++;
    pages.set(id, entry);
    return { value: { page: id }, entry };
  },
  async eval(req) {
    const entry = entryOf(req);
    return { value: await entry.page.evaluate(String(req.expr)), entry };
  },
  async seek(req) {
    const entry = entryOf(req);
    await seek(entry, req);
    return { value: null, entry };
  },
  async shot(req) {
    const entry = entryOf(req);
    return { value: null, entry, payload: await shot(entry, req) };
  },
  async frame(req) {
    const entry = entryOf(req);
    await seek(entry, req);
    return { value: null, entry, payload: await shot(entry, req) };
  },
  async cdp(req) {
    const entry = entryOf(req);
    if (!entry.cdp) entry.cdp = await entry.ctx.newCDPSession(entry.page);
    return { value: await entry.cdp.send(String(req.method), req.params || {}), entry };
  },
  async close(req) {
    const entry = pages.get(req.page);
    pages.delete(req.page);
    if (entry) await entry.ctx.close().catch(() => {});
    return { value: null, entry };
  },
};

async function handle(req) {
  const op = OPS[req.op];
  if (!op) {
    send({ id: req.id, ok: false, error: `unknown request '${req.op}'` });
    return;
  }
  try {
    const r = await op(req);
    const head = { id: req.id, ok: true, value: r.value === undefined ? null : r.value };
    const events = takeEvents(r.entry);
    if (events) head.events = events;
    send(head, r.payload);
  } catch (err) {
    const head = { id: req.id, ok: false, error: String((err && err.message) || err).slice(0, 2000) };
    const events = takeEvents(err && err.entry ? err.entry : pages.get(req.page));
    if (events) head.events = events;
    send(head);
  }
}

const inFlight = new Set();
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
  const p = handle(req).finally(() => inFlight.delete(p));
  inFlight.add(p);
}
await Promise.allSettled([...inFlight]);
await browser.close().catch(() => {});
exitAfterFlush(0);
