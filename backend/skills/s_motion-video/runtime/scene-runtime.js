/* s_motion-video scene runtime (loaded by every scene page after gsap.min.js).
 *
 * The renderer (scripts/render.py) injects window.__MV_DATA before any page script runs:
 *   {id, width, height, fps, duration, frames, words:[{text,start,end}], captions:[{text,start,end}],
 *    showCaptions, narration, lang, captionStyle:{bg,fg,font,size,radius,bottom}}
 * and then, for every frame i, awaits window.__mvSeek(i / fps) and takes a screenshot.
 * The picture must therefore be a pure function of t. See references/scene-contract.md.
 */
(function () {
  "use strict";
  var D = window.__MV_DATA || {};
  var W = +D.width || 1920, H = +D.height || 1080, FPS = +D.fps || 30;
  var DUR = +D.duration || 5;
  var U = Math.min(W, H) / 1080;
  // Caption band: the bottom 16 % of the frame belongs to captions (keep author text out of it).
  var CAPTION_BAND = 0.16, SAFE = 0.05;
  var words = Array.isArray(D.words) ? D.words : [];
  var captions = Array.isArray(D.captions) ? D.captions : [];
  var showCaptions = D.showCaptions !== false;

  var root = document.documentElement;
  // --u is a LENGTH (one 1080p pixel scaled to the frame), same convention as references/styles/*.css:
  // write sizes as calc(N * var(--u)).
  root.style.setProperty("--u", U + "px");
  root.style.setProperty("--w", W + "px");
  root.style.setProperty("--h", H + "px");
  root.style.setProperty("--safe-x", Math.round(W * SAFE) + "px");
  root.style.setProperty("--safe-y", Math.round(H * SAFE) + "px");
  root.style.setProperty("--cap-band", Math.round(H * (showCaptions ? CAPTION_BAND : SAFE)) + "px");
  var cs = D.captionStyle || {};
  var capVars = { bg: "--cap-bg", fg: "--cap-fg", font: "--cap-font", size: "--cap-size", radius: "--cap-radius", bottom: "--cap-bottom",
                  weight: "--cap-weight", shadow: "--cap-shadow", border: "--cap-border" };
  Object.keys(capVars).forEach(function (k) { if (cs[k] != null && cs[k] !== "") root.style.setProperty(capVars[k], String(cs[k])); });

  var base = document.createElement("style");
  base.id = "mv-base";
  base.textContent = [
    "html,body{margin:0;padding:0;width:" + W + "px;height:" + H + "px;overflow:hidden;}",
    "body{background:var(--bg,#0f172a);caret-color:transparent;}",
    "#stage{position:relative;width:" + W + "px;height:" + H + "px;overflow:hidden;}",
    "#camera{position:absolute;inset:0;transform-origin:50% 50%;}",
    // safety net: CSS animations/transitions are not seekable (check.py also forbids them)
    "*,*::before,*::after{animation:none!important;transition:none!important;}",
    "#mv-caption{position:absolute;left:50%;bottom:var(--cap-bottom,calc(72*var(--u)));transform:translateX(-50%);",
    "max-width:84%;z-index:2147483000;pointer-events:none;box-sizing:border-box;",
    "padding:calc(10*var(--u)) calc(28*var(--u));border-radius:var(--cap-radius,calc(12*var(--u)));",
    "border:var(--cap-border,none);box-shadow:var(--cap-shadow,none);",
    "background:var(--cap-bg,rgba(10,12,20,0.66));color:var(--cap-fg,#fff);",
    "font-family:var(--cap-font,-apple-system,'PingFang SC','Helvetica Neue',Helvetica,Arial,sans-serif);",
    "font-size:var(--cap-size,calc(42*var(--u)));font-weight:var(--cap-weight,600);line-height:1.35;white-space:nowrap;text-align:center;visibility:hidden;}",
    // optional accent mark on the left of the box: the style sets --cap-mark: tick | dot (colour --cap-accent)
    "#mv-caption.mv-mark-tick,#mv-caption.mv-mark-dot{padding-left:1.4em;}",
    "#mv-caption.mv-mark-tick::before,#mv-caption.mv-mark-dot::before{content:'';position:absolute;top:50%;transform:translateY(-50%);background:var(--cap-accent,currentColor);}",
    "#mv-caption.mv-mark-tick::before{left:0.8em;width:0.1em;height:0.64em;border-radius:0.05em;}",
    "#mv-caption.mv-mark-dot::before{left:0.85em;width:0.28em;height:0.28em;border-radius:50%;}"
  ].join("\n");
  (document.head || root).appendChild(base);

  // ---- seeded PRNG (mulberry32): MV.rand(seed) -> function () -> [0, 1)
  function rand(seed) {
    var a = 0;
    var s = String(seed == null ? 1 : seed);
    for (var i = 0; i < s.length; i++) a = (Math.imul(a ^ s.charCodeAt(i), 2654435761) + i) | 0;
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---- cue points
  function norm(s) { return String(s).toLowerCase().replace(/[\s　.,!?;:'"()\[\]{}<>\-_/\\|`~@#$%^&*+=，。！？；：、“”‘’（）《》【】…—·]/g, ""); }
  function findIn(list, kw) {
    // concatenate normalised texts; map each char back to its item so phrases spanning items match
    var joined = "", owner = [];
    for (var i = 0; i < list.length; i++) {
      var t = norm(list[i].text || "");
      for (var j = 0; j < t.length; j++) owner.push(i);
      joined += t;
    }
    var at = joined.indexOf(kw);
    return at < 0 ? null : list[owner[at]];
  }
  function cue(keyword, lead) {
    lead = lead == null ? 0.1 : +lead;
    var kw = norm(keyword);
    if (!kw) throw new Error("MV.cue: empty keyword");
    var hit = findIn(words, kw) || findIn(captions, kw);
    if (hit) return Math.max(0, +hit.start - lead);
    if (!words.length && !captions.length && D.narration) {
      // no voice yet (before tts.py): estimate from the keyword's position in the narration
      var nar = norm(D.narration), at = nar.indexOf(kw);
      if (at >= 0) {
        MV.estimated = true;
        return Math.max(0, (at / Math.max(1, nar.length)) * Math.max(0, DUR - 0.4) - lead);
      }
    }
    throw new Error('MV.cue: keyword "' + keyword + '" is not in the narration of ' + (D.id || "this scene") +
      " (use a word the voice actually says)");
  }

  // ---- captions: one persistent box, only the text swaps; a line lasts until the next starts
  var capEl = null, capText = null, capShown = -2;
  function ensureCaption() {
    if (capEl || !showCaptions || !document.body) return;
    capEl = document.createElement("div");
    capEl.id = "mv-caption";
    capText = document.createElement("span");
    capText.id = "mv-caption-text";
    capEl.appendChild(capText);
    (document.getElementById("stage") || document.body).appendChild(capEl);
  }
  function captionAt(t) {
    var idx = -1;
    for (var i = 0; i < captions.length; i++) if (+captions[i].start <= t + 1e-6) idx = i;
    return idx;
  }
  function renderCaption(t, override) {
    if (!showCaptions) return;
    ensureCaption();
    if (!capEl) return;
    var text = null, key;
    if (override != null) { text = String(override); key = "o:" + text; }
    else { var i = captionAt(t); key = i; text = i >= 0 ? String(captions[i].text) : null; }
    if (key === capShown) return;
    capShown = key;
    // stylesheets may load after this script, so read --cap-mark when a line is shown
    var mark = getComputedStyle(document.documentElement).getPropertyValue("--cap-mark").trim();
    capEl.classList.toggle("mv-mark-tick", mark === "tick");
    capEl.classList.toggle("mv-mark-dot", mark === "dot");
    if (text) { capText.textContent = text; capEl.style.visibility = "visible"; }
    else { capText.textContent = ""; capEl.style.visibility = "hidden"; }
  }

  // the caption band starts at the top of the real caption box (style may raise it, e.g. portrait), at least 16 % of H
  function measureCaptionBand() {
    if (!capEl) return;
    capText.textContent = "Ag\u56fd";
    var top = capEl.getBoundingClientRect().top;
    capText.textContent = "";
    capShown = -2;
    if (top > 0) MV.captionTop = Math.min(MV.captionTop, Math.floor(top - H * 0.01));
    root.style.setProperty("--cap-band", (H - MV.captionTop) + "px");
  }

  // ---- timeline registration and seeking
  var tl = null;
  function register(timeline) {
    if (!timeline || typeof timeline.seek !== "function") throw new Error("MV.register: pass the scene's gsap.timeline({paused:true})");
    if (tl) throw new Error("MV.register: called twice; one paused timeline per scene");
    tl = timeline;
    tl.pause();
    if (tl.duration() > DUR + 1e-3) console.warn("MV: timeline is " + tl.duration().toFixed(2) + " s but the scene is " + DUR + " s; the end is cut");
    tl.seek(0, false);
    MV.registered = true;
  }
  function nextFrame() { return new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(function () { r(); }); }); }); }
  window.__mvSeek = function (t, captionOverride) {
    if (!tl) return Promise.reject(new Error("scene did not call MV.register(tl)"));
    tl.seek(Math.min(+t, tl.duration()), false);
    renderCaption(+t, captionOverride);
    return nextFrame();
  };

  function nameOf(el) {
    if (!el || el.nodeType !== 1) return "object";
    var cls = typeof el.className === "string" ? el.className : (el.getAttribute("class") || "");   // SVG: SVGAnimatedString
    return el.id ? "#" + el.id : (el.tagName.toLowerCase() + (cls.trim() ? "." + cls.trim().split(/\s+/)[0] : ""));
  }

  // Every tween of the registered timeline, for check.py's move-length and settle checks (exact, not pixel-guessed).
  // A tween whose landing makes a block of author text readable also carries
  //   reveals: [{el, text, at}]   at = seconds at which the block is readable (the last tween that reveals it, on the
  //                               block or on an ancestor, has landed); text = the block's visible text at the scene end
  // for check.py's key-text hold check. Measured by seeking this timeline; the playhead is put back at 0 afterwards.
  window.__mvTweens = function () {
    if (!tl) return [];
    var kids = tl.getChildren(true, true, false);
    var recs = kids.map(function (x) {
      var tg = (x.targets && x.targets()) || [];
      return { start: +x.startTime().toFixed(3), duration: +x.duration().toFixed(3), target: nameOf(tg[0]), count: tg.length };
    });
    try { textReveals(kids, recs); }
    catch (e) { console.warn("MV: key-text hold not measured: " + (e && e.message)); }
    finally { tl.seek(0, false); }
    return recs;
  };

  // ---- key text reveals (read by check.py through __mvTweens)
  var READABLE = 0.5;   // effective opacity from which text counts as readable; dimmed / ghost states stay below it
  var VIS_KEY = /^(opacity|autoAlpha|visibility|display|clipPath|webkitClipPath|clip-path)$/;
  function touchesVisibility(v) {
    if (!v) return false;
    return [v, v.startAt, v.css].some(function (o) {
      return o && typeof o === "object" && Object.keys(o).some(function (k) { return VIS_KEY.test(k); });
    });
  }
  function absStart(x) {   // start in the registered timeline's time, also for a tween inside a nested timeline
    var t = x.startTime(), p = x.parent;
    while (p && p !== tl) { t += p.startTime(); p = p.parent; }
    return t;
  }
  function clipHidden(el, cs) {   // true when clip-path hides at least half of the box on one axis
    var c = cs.clipPath || cs.webkitClipPath || "none";
    var m = /^inset\(([^)]*)\)/.exec(c);
    if (m) {
      var p = m[1].split(/\s+round\s+/)[0].trim().split(/\s+/);
      var side = [p[0], p[1] || p[0], p[2] || p[0], p[3] || p[1] || p[0]];   // top right bottom left
      var r = el.getBoundingClientRect(), w = el.offsetWidth || r.width || 1, h = el.offsetHeight || r.height || 1;
      var f = side.map(function (s, i) { var n = parseFloat(s) || 0; return /%$/.test(s) ? n / 100 : n / (i % 2 ? w : h); });
      return f[0] + f[2] >= 0.5 || f[1] + f[3] >= 0.5;
    }
    m = /^circle\(\s*([\d.]+)/.exec(c);
    return !!(m && parseFloat(m[1]) === 0);
  }
  function visibility(el) {   // effective opacity: the element's and every ancestor's opacity, display and clip
    if (getComputedStyle(el).visibility !== "visible") return 0;
    var op = 1;
    for (var e = el; e && e.nodeType === 1; e = e.parentElement) {
      var cs = getComputedStyle(e);
      if (cs.display === "none" || clipHidden(e, cs)) return 0;
      op *= parseFloat(cs.opacity);
    }
    return op;
  }
  function blockText(block, owners) {   // visible on-screen text of a block, without the text of a nested block
    var out = [], walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT), n;
    while ((n = walker.nextNode())) {
      var txt = n.textContent.replace(/\s+/g, " ").trim(), el = n.parentElement;
      if (!txt || !el || el.closest("#mv-caption, script, style")) continue;
      var own = el;
      while (own && own !== block && !owners.has(own)) own = own.parentElement;
      if (own !== block || visibility(el) < READABLE) continue;
      var rg = document.createRange();
      rg.selectNodeContents(n);
      var on = Array.prototype.some.call(rg.getClientRects(), function (b) {
        return b.width >= 1 && b.height >= 1 && b.right > 0 && b.left < W && b.bottom > 0 && b.top < H;
      });
      if (on) out.push(txt);
    }
    return out.join(" ");
  }
  function textReveals(kids, recs) {
    var events = [];   // {el, start, end, rec}: one per element target of a tween that touches visibility
    kids.forEach(function (x, i) {
      if (!x.targets || !touchesVisibility(x.vars)) return;
      var s = absStart(x), subs = x.timeline ? x.timeline.getChildren(true, true, false) : [];   // stagger: a child per target
      var parts = subs.length ? subs.map(function (c) { return [c, s + c.startTime()]; }) : [[x, s]];
      parts.forEach(function (p) {
        ((p[0].targets && p[0].targets()) || []).forEach(function (el) {
          if (el && el.nodeType === 1 && el.isConnected) events.push({ el: el, start: p[1], end: p[1] + p[0].duration(), rec: i });
        });
      });
    });
    if (!events.length) return;
    // visibility just before each tween starts and just after it lands: one seek per distinct time
    var at = {};
    events.forEach(function (ev) {
      var t0 = Math.max(0, ev.start - 1e-3), t1 = ev.end + 1e-4;
      (at[t0] = at[t0] || []).push([ev, "v0"]);
      (at[t1] = at[t1] || []).push([ev, "v1"]);
    });
    Object.keys(at).map(Number).sort(function (a, b) { return a - b; }).forEach(function (t) {
      tl.seek(Math.min(t, tl.duration()), false);
      at[t].forEach(function (q) { q[0][q[1]] = visibility(q[0].el); });
    });
    var reveals = events.filter(function (ev) { return ev.v0 < READABLE && ev.v1 >= READABLE; });
    if (!reveals.length) return;
    var owners = new Set(reveals.map(function (ev) { return ev.el; }));
    tl.seek(Math.min(DUR, tl.duration()), false);   // the scene end: what is still on screen, with its final text
    owners.forEach(function (block) {
      if (visibility(block) < READABLE) return;
      var text = blockText(block, owners);
      if (!text) return;
      var last = null;   // the latest reveal of the block or of an ancestor decides when it is readable
      reveals.forEach(function (ev) {
        if ((ev.el === block || ev.el.contains(block)) && (!last || ev.end > last.end)) last = ev;
      });
      var rec = recs[last.rec];
      (rec.reveals = rec.reveals || []).push({ el: nameOf(block), text: text.slice(0, 400), at: +last.end.toFixed(3) });
    });
  }

  // ---- readiness: DOM loaded, fonts ready, all <img> decoded
  window.__mvReady = false;
  function whenLoaded() {
    return new Promise(function (r) { if (document.readyState === "complete") r(); else window.addEventListener("load", function () { r(); }); });
  }
  whenLoaded().then(function () {
    var imgs = Array.prototype.slice.call(document.images).map(function (im) {
      return im.decode ? im.decode().catch(function () {}) : Promise.resolve();
    });
    return Promise.all([document.fonts ? document.fonts.ready : Promise.resolve()].concat(imgs));
  }).then(function () {
    ensureCaption();
    measureCaptionBand();
    renderCaption(0);
    return nextFrame();
  }).then(function () { window.__mvReady = true; });

  var MV = {
    id: D.id || null, fps: FPS, width: W, height: H, duration: DUR, frames: Math.round(DUR * FPS), u: U,
    words: words, captions: captions, showCaptions: showCaptions, estimated: false, registered: false,
    // geometry the checks use: author text must stay above captionTop and inside the safe box
    safe: { x: Math.round(W * SAFE), y: Math.round(H * SAFE) },
    captionTop: Math.round(H * (1 - (showCaptions ? CAPTION_BAND : SAFE))),
    cue: cue, rand: rand, register: register,
    end: function (settle) { return DUR - (settle == null ? 0.3 : +settle); }
  };
  window.MV = MV;
})();
