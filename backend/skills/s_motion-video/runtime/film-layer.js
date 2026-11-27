/* s_motion-video film layer: the parts every scene of one film shares.
 *
 * Load after scene-runtime.js, together with the project's generated data:
 *   <script src="/skill/runtime/film-layer.js"></script>
 *   <script src="/project/scenes/film-data.js"></script>     (written by scripts/film.py; re-run after tts.py)
 * and call FL.setup(tl, opts) right after creating the scene's timeline, before its own tweens.
 *
 * Parts (each optional; switched on for the film in film.json, see templates/film.json):
 *   plate     background plate behind #camera (an image from the project's assets/ and/or a class), slow drift
 *   mask      directional shade over the plate (left | right | center | warm)
 *   vignette  edge darkening over the plate
 *   kicker    chapter number, chapter name and section title, top left, static
 *   progress  film-wide progress bar on the bottom edge with chapter marks; neighbours meet exactly
 *   push      shot-long camera push of #camera in log space (constant perceived speed)
 * opts (per scene, all optional): { plate: false | {class, drift}, mask: false | "left" | ..., vignette: false |
 *   true | "classes", kicker: false, progress: false, push: false | 1.02, origin: "50% 40%" }.
 * kicker and progress can only be switched off per scene: they need data that film.py writes only when they
 * are on in film.json.
 *
 * The look belongs to the style. Each part gets a neutral class (fl-*) whose colours and font are --fl-*
 * variables pointing at the style's own tokens, plus the style's own class when it has one
 * (runtime/film-styles.json, film.json "classes"); the neutral rules have zero specificity (:where), so
 * the style's rules always win. The layer never brings a colour or font of its own.
 *
 * Determinism: every move is a timeline tween at time 0 spanning the scene; nothing reads the clock.
 * The layer reads only window.MV_FILM.layer and window.MV_FILM.scenes[MV.id] (never the film totals), so a
 * scene's picture depends only on its own entry; scripts/derive.py relies on that.
 */
(function () {
  "use strict";
  var CSS = [
    ":where(.fl-plate){position:absolute;inset:-3%;overflow:hidden;pointer-events:none;}",
    ":where(.fl-plate)>:where(img){display:block;width:100%;height:100%;object-fit:cover;}",
    ":where(.fl-mask),:where(.fl-vignette){position:absolute;inset:0;pointer-events:none;}",
    ":where(.fl-mask-left){background:linear-gradient(90deg,var(--fl-shade,rgba(0,0,0,.62)) 0%,transparent 75%);}",
    ":where(.fl-mask-right){background:linear-gradient(270deg,var(--fl-shade,rgba(0,0,0,.62)) 0%,transparent 75%);}",
    ":where(.fl-mask-center){background:radial-gradient(ellipse at 50% 45%,transparent 35%,var(--fl-shade,rgba(0,0,0,.5)) 100%);}",
    ":where(.fl-mask-warm){background:radial-gradient(ellipse at 50% 42%,transparent 10%,var(--fl-shade,rgba(0,0,0,.7)) 90%);}",
    ":where(.fl-vignette){box-shadow:inset 0 0 calc(260 * var(--u)) var(--fl-vignette,rgba(0,0,0,.35));}",
    ":where(.fl-kicker){position:absolute;left:var(--safe-x);top:var(--safe-y);display:flex;align-items:baseline;",
    "gap:calc(18 * var(--u));white-space:nowrap;font-family:var(--fl-font,inherit);font-size:var(--fl-kicker-size,calc(26 * var(--u)));",
    "letter-spacing:calc(3 * var(--u));color:var(--fl-ink,currentColor);}",
    ":where(.fl-kicker) :where(.no){color:var(--fl-accent,var(--fl-ink,currentColor));font-weight:700;}",
    ":where(.fl-kicker) :where(.ch){color:var(--fl-muted,var(--fl-ink,currentColor));}",
    ":where(.fl-progress){position:absolute;left:0;right:0;bottom:0;height:var(--fl-progress-h,calc(5 * var(--u)));",
    "background:var(--fl-rule,transparent);}",
    ":where(.fl-progress-fill){position:absolute;inset:0;transform-origin:0 50%;background:var(--fl-accent,var(--fl-ink,currentColor));}",
    ":where(.fl-progress-mark){position:absolute;top:calc(-1 * var(--fl-progress-h,calc(5 * var(--u))));width:calc(2 * var(--u));",
    "height:calc(3 * var(--fl-progress-h,calc(5 * var(--u))));background:var(--fl-muted,var(--fl-ink,currentColor));}"
  ].join("\n");
  var PER_SCENE = { plate: 1, mask: 1, vignette: 1, kicker: 1, progress: 1, push: 1, origin: 1 };
  var MASKS = { left: 1, right: 1, center: 1, warm: 1 };
  var done = false;

  function fail(msg) { throw new Error("FL: " + msg); }
  function injectCss() {
    if (document.getElementById("fl-base")) return;
    var st = document.createElement("style");
    st.id = "fl-base";
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }
  injectCss();

  function el(tag, cls, parent, before) {
    var e = document.createElement(tag);
    e.className = cls.replace(/\s+/g, " ").trim();
    if (before) before.parentNode.insertBefore(e, before); else parent.appendChild(e);
    return e;
  }
  function film() {
    var F = window.MV_FILM;
    if (!F || !F.scenes || !F.layer) fail("no film data: run python3 scripts/film.py --project <project> and load " +
      "/project/scenes/film-data.js in the page's <head>");
    return F;
  }
  function sceneEntry(F) {
    var s = F.scenes[MV.id];
    if (!s) fail("scene " + MV.id + " is not in scenes/film-data.js: re-run scripts/film.py --project <project>");
    if (Math.abs(+s.duration - MV.duration) > 0.5 / MV.fps + 1e-6)
      fail("scenes/film-data.js is stale: it has " + MV.id + " at " + s.duration + " s, the storyboard says " + MV.duration +
        " s; re-run scripts/film.py --project <project> after tts.py");
    return s;
  }
  function options(base, o) {
    var r = {}, k;
    for (k in base) if (Object.prototype.hasOwnProperty.call(base, k)) r[k] = base[k];
    o = o || {};
    for (k in o) {
      if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
      if (!PER_SCENE[k]) fail("unknown option '" + k + "' (known: " + Object.keys(PER_SCENE).join(", ") + ")");
      if ((k === "kicker" || k === "progress") && o[k] && !base[k]) fail(k + " is off in film.json; switch it on there (then re-run film.py)");
      if (k === "plate" && o[k] && typeof o[k] === "object") {
        if (!base.plate) fail("plate is off in film.json; switch it on there (then re-run film.py)");
        var p = {}, j;
        for (j in base.plate) p[j] = base.plate[j];
        for (j in o[k]) { if (j !== "class" && j !== "drift") fail("plate option '" + j + "' (per scene: class, drift; images are set in film.json)"); p[j] = o[k][j]; }
        r.plate = p;
      } else if (k === "plate" && o[k] === true) {
        if (!base.plate) fail("plate is off in film.json; switch it on there (then re-run film.py)");
      } else r[k] = o[k];
    }
    if (r.mask && !MASKS[r.mask]) fail("mask '" + r.mask + "' (left | right | center | warm)");
    return r;
  }

  function setup(tl, o) {
    if (!window.MV) fail("load /skill/runtime/scene-runtime.js before the film layer");
    if (!tl || typeof tl.fromTo !== "function") fail("FL.setup(tl, opts): pass the scene's paused timeline");
    if (done) fail("FL.setup was called twice; once per scene");
    var F = film(), s = sceneEntry(F), L = F.layer, C = L.classes || {}, opt = options(L.options || {}, o);
    var stage = document.getElementById("stage"), cam = document.getElementById("camera");
    if (!stage || !cam) fail("the page needs #stage with #camera inside (see templates/scene.html)");
    done = true;
    var T = MV.duration, W = MV.width, H = {};
    var tk = L.tokens || {};
    Object.keys(tk).forEach(function (k) { stage.style.setProperty("--fl-" + k, "var(" + tk[k] + ")"); });

    if (opt.plate) {
      H.plate = el("div", "fl-plate " + (C.plate || "") + " " + (opt.plate["class"] || ""), stage, cam);
      if (s.plate_image) { var img = document.createElement("img"); img.alt = ""; img.src = s.plate_image.src; H.plate.appendChild(img); }
      var d = +opt.plate.drift || 0;
      if (d > 0) {   // slow Ken Burns, alternating direction scene by scene
        var sg = s.parity ? 1 : -1;
        tl.fromTo(H.plate, { scale: 1 + d, x: sg * 0.01 * W }, { scale: 1, x: -sg * 0.01 * W, duration: T, ease: "none" }, 0);
      }
    }
    if (opt.mask) H.mask = el("div", "fl-mask fl-mask-" + opt.mask + " " + (C.mask || "").replace(/\{mask\}/g, opt.mask), stage, cam);
    if (opt.vignette) H.vignette = el("div", "fl-vignette " + (C.vignette || "") + " " + (typeof opt.vignette === "string" ? opt.vignette : ""), stage, cam);

    if (opt.kicker && s.kicker) {
      H.kicker = el("div", "fl-kicker " + (C.kicker || ""), stage);
      [["no", s.kicker.no], ["ch", s.kicker.name], ["sec", s.kicker.section]].forEach(function (p) {
        if (!p[1]) return;
        var sp = document.createElement("span"); sp.className = p[0]; sp.textContent = p[1]; H.kicker.appendChild(sp);
      });
    }
    if (opt.progress && s.progress) {
      H.progress = el("div", "fl-progress " + (C.progress || ""), stage);
      var fill = el("div", "fl-progress-fill " + (C.progress_fill || ""), H.progress);
      (s.progress.marks || []).forEach(function (m) {
        el("div", "fl-progress-mark " + (C.progress_mark || ""), H.progress).style.left = (100 * m) + "%";
      });
      tl.fromTo(fill, { scaleX: s.progress.from }, { scaleX: s.progress.to, duration: T, ease: "none" }, 0);
      H.progressFill = fill;
    }
    if (opt.push && +opt.push !== 1) {   // log space: the same perceived speed from start to end
      var z = { v: 0 }, zEnd = Math.log(+opt.push);
      gsap.set(cam, { scale: 1, transformOrigin: opt.origin || "50% 45%" });
      tl.fromTo(z, { v: 0 }, { v: zEnd, duration: T, ease: "none",
        onUpdate: function () { gsap.set(cam, { scale: Math.exp(z.v) }); } }, 0);
    }
    H.camera = cam;
    return H;
  }

  window.FL = {
    setup: setup,
    // this scene's entry in the film data (title, chapter, section, next, kicker, progress); read-only
    get scene() { return sceneEntry(film()); },
    // a cue clamped so a move of `move` seconds still settles by MV.end()
    at: function (keyword, move) { return Math.max(0, Math.min(MV.cue(keyword), MV.end() - (move == null ? 0.6 : +move))); }
  };
})();
