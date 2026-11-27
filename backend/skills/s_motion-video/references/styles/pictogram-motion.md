# Style: pictogram-motion (Pictogram Motion)

Units: `u` = frame short side / 1080 (1u = 1px at 1920x1080). CSS `calc(N * var(--u))` (see `pictogram-motion.css`); script `const u = MV.u`. Tags: `(m)` measured from the source code, `(s)` seen in stills, `(i)` inferred.

## Identity

A beat-locked flash-card catalogue: N items, one card per item, each card one faceless geometric figure doing one verb, a huge heavy title, a second-language line under it and a small mono index "03 / 08". Identity rules, all mandatory: (1) the square grid pattern: big discs and half-discs on a 1/6-frame-height grid, about half the cells carrying a small geometric motif, all in five tones of ONE hue, cut into full-width rows that slide sideways; (2) the pictogram figure: round-capped bars and a separate disc head, far limbs mixed 42 % toward the background, standing on a large sun disc in a neighbouring tone; (3) type as architecture: a very heavy grotesk title, a heavy second line, small widely tracked mono text in the corners like a broadcast overlay, plus a tick progress bar of N items; (4) colour = chapter: one hue per card or chapter, flat colour only, one soft light sweep as the only gradient; (5) beat lock: every card starts on a beat and every key action lands on a beat.

Best for: event openers, "everything at a glance" lists, line-ups, feature lists, brand systems, step lists, countdowns. Not for: narrated argument, charts, UI walkthroughs, emotional stories, realism.

## Palette

Five tones per hue, `0` darkest .. `2` base .. `4` lightest (tokens `--pm-<hue>-<k>`, `--pm-<hue>-fg`); 1/3 sit ~12-14 % toward dark/light, 0/4 ~24-30 % (s, i).

| Hue | 0 / 1 / 2 base / 3 / 4 | Type + figure |
|---|---|---|
| ember | `#B14029 #CA472A #E34F2B #E76643 #EB805E` | cream |
| cobalt | `#293B92 #2D42A3 #3049B5 #4D61BA #6E7CBF` | cream |
| jade | `#11765F #108668 #109672 #31A380 #58B290` | cream |
| saffron (light) | `#B98A24 #D39D24 #EDB024 #F0B93D #F2C459` | dark `#241A06` |
| clay (light) | `#9B6D4D #B17B53 #C68A5A #CE996B #D7AA7F` | dark `#21150B` |
| slate (dark neutral) | `#10121B #161925 #1D2131 #2A2F40 #3A3F52` | cream; accent saffron |

Cream `#F9F4E8` and ink `#17141F` serve every hue. A card is one hue (ground tone 2; pattern, disc, panel, ghost type from its tones) plus its fg; multi-hue chapters rotate hues per card or per row. Light hues take dark type/figures and a tone-4 disc; dark hues a tone-1 disc; slate a tone-3 disc. Accent: one small element per screen may borrow another hue (a "NEW" chip in ember, or saffron on slate); two borrowed elements on one screen means one is wrong. Under type the pattern runs at low contrast (spread .42), on chapter cards at full contrast (.9-.95) (m). Alternatives: a topic set (coastal: navy, teal, coral, sand, sky; kitchen: tomato, basil, saffron, aubergine, flour) with the same tone rule and your own names.

## Type

- Title: `var(--pm-sans)` (SF Pro) weight 900, 170u (150-190; 150 in layout C), tracking -0.02em, line-height .9, one line, fitted at build to the column (820u A/M, 640u C) (m).
- Mega ghost title (layout B): 330-360u 900, tracking -0.03em, one line up to 1760u wide, tone 4 (dark hues) / 1 (light) (m).
- Second language: `.pm-second` 92u (84 in C, 80 in B, 66 on chapter cards); Chinese uses PingFang SC at its heaviest weight, Japanese add `.pm-ja` (Hiragino Sans W8-W9) (i).
- Meta and HUD: `var(--pm-mono)` 500, meta 22u tracking 3u at 72 %; HUD 17u tracking 3u at 80 %; tag chip mono 20u, 38u tall, fg fill (m).
- Numbers: outline index 300u 800, 2u stroke at 20 %; chapter numeral 330u 2.5u stroke at 85 %; count-up 480-520u 900 tracking -0.05em (m).
- Bilingual: one language is always the big title, the other the second line, chosen per film. `white-space: nowrap`.

## Layout grid

- Grid: square cell 180u (1/6 frame height). Bands, transitions and walls snap to it. Safe area 5 %; HUD corners at x 100u, top 58u (two lines: film / "CH.01  NAME"); ticks along the bottom 40u up, N = item count of the film, current tick 4x20u, done 75 %, rest 28 % (m).
- Per card: one figure, one title block, one index, HUD; text and figure never overlap (except B).
- **A**: text at x 130u (meta top ~385u, title, second +18u, tag +36u); disc r 380u at (1290, 540); figure 560u, hip at disc centre +20u; index bottom right (m).
- **M**: mirror of A (disc at x 640u, text right-aligned to 1790u).
- **B** (rapid runs): ghost title centred, top ~410u; disc r 250u at (960, 470), figure 500u over the title; info bar 150u up: second line 80u, small title 44u, meta right (m).
- **C**: `.pm-panel` 820u (tone 0 light hues / 4 dark) with 90u hairlines at 10 %, text at x 110u; disc r 360u at (1370, 530) (m).
- **Chapter card**: full-contrast pattern; a band two rows tall (y 270-630u) in tone 0/4 or ink across the full width (`.pm-band`); outline numeral at x 110u inside the band; meta, 170u title and 66u second line from x 564u; 2u hairlines 10u above and 8u below the band (m).
- **Count-up**: pattern visible only in the top and bottom half-rows; a solid tone-2 block covers y 90-990u; number right-aligned to x 1060u; label block from x 1110u (title 140-150u, second 64u) (m).
- Portrait (i): cell 180u (11 rows); disc+figure in the upper half (centre 0.36 H, r 330u), text below at x 90u; HUD top 110u.

## Camera grammar

Mostly a locked flat frame; energy comes from row shifts, transitions and figure actions. When the camera moves it moves along the grid (s, m).

| Shot | Expresses | How |
|---|---|---|
| locked card | clarity, a poster | default; rows drift 26u/s keep it alive |
| long take down a track | one family, momentum | stations one screen apart in a wide strip; drift 42u/s, whip one screen per cut `expo.inOut` from -0.14 to +0.24 s; background rows at parallax .28/.42 (m) |
| push into a motif cell | zoom into a part | scale `#camera` about one cell until it fills the frame, then the next card (i) |
| icon wall | the whole set | 9x5 tiles of 180u, 10u gutters, frozen pictograms; flip in, turn one hue, zoom to 0.9 (m) |

Never 3D moves, shake, depth of field or dissolves.

## Motion

150 BPM: beat 0.4 s (12 frames), bar 1.6 s (m). Card 4 beats; rapid run 2 beats (entrances x0.7); chapter card 8; count-up 4. Divide the beat for a second tempo, never change it. A 6 s scene holds 3-4 cards joined by in-scene grid transitions.

| Move | Start / duration | Ease |
|---|---|---|
| title mask slide-up (y 112 % -> 0) | 0 / 0.5 | `expo.out` |
| second line | +0.08 / 0.47 | `expo.out` |
| tag chip (scaleX + opacity) | +0.2 / 0.3 | `expo.out` |
| meta type-on (no caret) | +0.02 / 0.28 (min 0.25) | `none` |
| figure assembly (54u bands, offsets 90-250u alternating) | 0 / 0.42 (0.28 rapid) | `power2.out` |
| sun disc scale .82 -> 1 | 0 / 0.5 | `power4.out` |
| light sweep across the disc | +0.05 / 1.05 | `power2.out` |
| outline index rise 60u + fade to 20 % | +0.05 / 0.6 | `expo.out` |
| panel wipe (C) / chapter band from centre | 0 / 0.4 ; 0.05 / 0.45 | `power4.out` |
| chapter numeral rise 300u | +0.1 / 0.6 | `expo.out` |
| row jumps on chapter cards (quarter cell, beats 4-7) | each beat / 0.25 | `expo.out` |
| count-up: number rise 500u / count | 0 / 0.35 ; 0 / 0.5 | `expo.out` / `power2.out` |
| background row drift, alternate directions | whole scene, 26u/s (30 on chapters) + per-row offset sin(1.7 j) x 22u | `none` |

Source values under 0.25 s (row jump 0.14 s) are raised to the contract minimum. Key pose actions (release, contact, landing) arrive on integer beats with `expo.out`. One `back` ease per film at most.

## Materials and light

Flat fills, no outlines or shadows. One sweep per disc: white 0 -> 9 % -> 0 band, rotated 18 deg, clipped to the disc (m). Grain: fractal noise, 16 % overlay, re-offset every frame (texture 0.23 vs reference 0.29; 0.15 without). Reference stills: range 57 (one hue) to 93 (multi), glow ~0, no vignette, chroma 40-56 (m). Far limbs 42 % toward the disc tone; a partner behind at 60 %.

## Technique library

Paste this kit (system-only, deterministic) into the scene script; it reads the palette from the CSS tokens.

```js
// PM kit:paste once per scene,before building the timeline
const u=MV.u,W=MV.width,H=MV.height,CELL=180*u,BEAT=.4,NS="http://www.w3.org/2000/svg";
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const PM={
el(t,a={},p) {const e=document.createElementNS(NS,t); for (const k in a) e.setAttribute(k,a[k]); if (p) p.appendChild(e); return e;},
rgb:h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)),
mix(a,b,t) {const B=PM.rgb(b); return "#"+PM.rgb(a).map((v,i)=>Math.round(v+(B[i]-v)*t).toString(16).padStart(2,"0")).join("");},
tones(h) {const fg=css(`--pm-${h}-fg`); return {t:[0,1,2,3,4].map(k=>css(`--pm-${h}-${k}`)),fg,light:fg.toUpperCase()!==css("--pm-cream").toUpperCase()};},
hue(el,h) {const T=PM.tones(h); T.t.forEach((c,k)=>el.style.setProperty("--c"+k,c)); el.style.setProperty("--fg",T.fg); return T;},
svg(p) {return PM.el("svg",{class:"pm-svg",viewBox:`0 0 ${W} ${H}`},p);},
// grid pattern -> one {slide,drift} group pair per row (rows=clipped bands); o.only=[row indices]
pattern(svg,h,seed,o={}) {
  const T=PM.tones(h),r=MV.rand(h+seed),sp=o.spread??.42,den=o.density??.42,x0=-2.5*CELL;
  const tone=k=>PM.mix(T.t[2],T.t[k],sp),id="p"+h+seed,cols=Math.ceil(W/CELL)+5,nr=Math.ceil(H/CELL);
  const defs=PM.el("defs",{},svg),g=PM.el("g",{id},defs);
  PM.el("rect",{x:x0,width:cols*CELL,height:nr*CELL,fill:tone(1)},g);
  for (let j=0; j<=nr; j++) for (let i=0; i<=cols; i++) {const q=r(),cx=x0+i*CELL,cy=j*CELL;
    if (q<.34) PM.el("circle",{cx,cy,r:CELL,fill:tone(Math.floor(r()*4))},g);
    else if (q<.5) {PM.el("circle",{cx,cy,r:CELL,fill:tone(3)},g); PM.el("circle",{cx,cy,r:CELL/2,fill:tone(0)},g);}}
  for (let j=0; j<nr; j++) for (let i=0; i<cols; i++) if (r()<den) {const a=Math.floor(r()*5),b=(a+1+Math.floor(r()*4))%5;
    PM.motif(g,Math.floor(r()*11),x0+i*CELL,j*CELL,CELL,tone(a),tone(b),Math.floor(r()*4)*90);}
  return [...Array(nr).keys()].filter(j=>!o.only||o.only.includes(j)).map(j=>{
    PM.el("rect",{y:j*CELL,width:W,height:CELL+.5},PM.el("clipPath",{id:id+"r"+j},defs));
    const slide=PM.el("g",{},PM.el("g",{"clip-path":`url(#${id}r${j})`},svg)),drift=PM.el("g",{},slide);
    PM.el("use",{href:"#"+id},drift); return {slide,drift};});
},
motif(p,k,x,y,s,a,b,rot) {   // k: 0 quarter disc 1 half disc 2 disc+dot 3 striped half 4 arcs 5 dots 6 teeth 7 wheel 8 checker 9 crescent 10 ring scales
  const g=PM.el("g",{transform:`translate(${x} ${y}) rotate(${rot} ${s/2} ${s/2})`},p),h=s/2,E=(t,at)=>PM.el(t,at,g),F={fill:b};
  const q=(f,c=b)=>E("path",{d:`M0 0H${s*f}A${s*f} ${s*f} 0 0 1 0 ${s*f}Z`,fill:c});
  E("rect",{width:s,height:s,fill:a});
  if (k===0) q(1); else if (k===1) E("path",{d:`M0 ${s}A${h} ${h} 0 0 1 ${s} ${s}Z`,...F});
  else if (k===2) {E("circle",{cx:h,cy:h,r:h*.86,...F}); E("circle",{cx:h,cy:h,r:h*.3,fill:a});}
  else if (k===3) for (let i=0; i<6; i++) {const yc=s-(i+.5)*s/12,w=Math.sqrt(h*h-(s-yc) ** 2); E("rect",{x:h-w,y:yc-s/40,width:2*w,height:s/20,...F});}
  else if (k===4) {q(1); q(.72,a); q(.44);}
  else if (k===5||k===8) {for (let i=0; i<4; i++) for (let j=0; j<4; j++) if (k===5) E("circle",{cx:(i+.5)*s/4,cy:(j+.5)*s/4,r:s*.07,...F});
    else if ((i+j)%2) E("rect",{x:i*s/4,y:j*s/4,width:s/4,height:s/4,...F});}
  else if (k===6) for (let i=0; i<4; i++) for (let j=1; j<3; j++) E("path",{d:`M${i*s/4} ${j*h}l${s/8} ${-h}l${s/8} ${h}Z`,...F});
  else if (k===7) {E("circle",{cx:h,cy:h,r:s*.24,...F}); for (let i=0; i<12; i++) {const c=Math.cos(i*Math.PI/6),n=Math.sin(i*Math.PI/6);
    E("line",{x1:h+c*s*.32,y1:h+n*s*.32,x2:h+c*s*.44,y2:h+n*s*.44,stroke:b,"stroke-width":s*.045});}}
  else if (k===9) {E("circle",{cx:h,cy:h,r:s*.4,...F}); E("circle",{cx:h+s*.17,cy:h-s*.1,r:s*.36,fill:a});}
  else for (let j=0; j<3; j++) for (let i=0; i<5; i++) E("circle",{cx:i*s/4+(j%2)*s/8,cy:(j+.7)*s/3,r:s/9,fill:"none",stroke:b,"stroke-width":s*.035});
},
// pictogram:round-capped bars+disc head,s=body height px; returns set(pose) -> joints
figure(p,near,far,s) {
  const L=(c,w)=>PM.el("line",{stroke:c,"stroke-width":w*s,"stroke-linecap":"round"},p);
  const fl=[L(far,.095),L(far,.088)],fa=[L(far,.075),L(far,.07)],to=L(near,.145),nl=[L(near,.095),L(near,.088)],na=[L(near,.075),L(near,.07)];
  const hd=PM.el("circle",{r:.085*s,fill:near},p),D=Math.PI/180,dir=a=>[Math.sin(a*D),Math.cos(a*D)];
  const add=(q,v,k)=>[q[0]+v[0]*k*s,q[1]+v[1]*k*s],seg=(l,a,b)=>[a[0],a[1],b[0],b[1]].forEach((v,i)=>l.setAttribute(["x1","y1","x2","y2"][i],v));
  return P=>{
    const g=k=>P[k]||0,hip=[g("x"),g("y")],tf=g("rot")+g("torso"),sho=add(hip,dir(180-tf),.31),head=add(sho,dir(180-tf-g("head")),.13);
    const limb=(ls,a1,a2,base,l)=>{const e=add(base,dir(a1),l[0]),f=add(e,dir(a2),l[1]); seg(ls[0],base,e); seg(ls[1],e,f); return f;};
    const J={hip,sho,head,hL:limb(fa,g("aL1")-tf,g("aL2")-tf,sho,[.16,.15]),fL:limb(fl,g("lL1")-g("rot"),g("lL2")-g("rot"),hip,[.23,.23]),
      hR:limb(na,g("aR1")-tf,g("aR2")-tf,sho,[.16,.15]),fR:limb(nl,g("lR1")-g("rot"),g("lR2")-g("rot"),hip,[.23,.23])};
    seg(to,hip,sho); hd.setAttribute("cx",head[0]); hd.setAttribute("cy",head[1]); return J;};
},
keyPose(keys,b,loop) {
  if (loop) b=((b%loop)+loop)%loop;
  let i=0; while (i<keys.length-1&&keys[i+1].b<=b) i++;
  const A=keys[i],B=keys[Math.min(i+1,keys.length-1)],sp=B.b-A.b,t=sp>0?gsap.parseEase(B.e||"power2.inOut")(Math.min(1,(b-A.b)/sp)):0,o={};
  new Set([...Object.keys(A),...Object.keys(B)]).forEach(k=>{if (k!=="b"&&k!=="e") o[k]=(A[k]||0)+((B[k]||0)-(A[k]||0))*t;});
  return o;
},
// drive a figure from t0 for dur s; o={loop (beats),at:{x,y} added to the pose,extra(J,beat) moves props}
act(tl,set,keys,t0,dur,o={}) {
  const st={b:0},run=()=>{const P=PM.keyPose(keys,st.b,o.loop); for (const k in o.at||{}) P[k]=(P[k]||0)+o.at[k]; const J=set(P); if (o.extra) o.extra(J,st.b);};
  run(); tl.fromTo(st,{b:0},{b:dur/BEAT,duration:dur,ease:"none",onUpdate:run},t0);
},
// figure assembly:src=<g id> in defs; 54u bands slide in from alternating sides
assemble(tl,svg,src,box,t0,dur=.42) {
  const defs=PM.el("defs",{},svg),wrap=PM.el("g",{},svg),r=MV.rand(src.id),n=Math.ceil(box.h/(54*u));
  for (let k=0; k<n; k++) {const id=src.id+"b"+k;
    PM.el("rect",{x:box.x,y:box.y+k*54*u,width:box.w,height:54*u+.5},PM.el("clipPath",{id},defs));
    const sh=PM.el("g",{},PM.el("g",{"clip-path":`url(#${id})`},wrap)); PM.el("use",{href:"#"+src.id},sh);
    tl.fromTo(sh,{x:(k%2?1:-1)*(90+160*r())*u},{x:0,duration:dur,ease:"power2.out"},t0);}
  tl.fromTo(wrap,{opacity:0},{opacity:1,duration:.25,ease:"power1.out"},t0); return wrap;
},
// sun disc+one soft 9%light sweep
sun(tl,svg,cx,cy,r,col,t0,sweep=1.05) {
  const id="s"+Math.round(cx+cy*7+t0*1e3),defs=PM.el("defs",{},svg),gr=PM.el("linearGradient",{id:id+"g"},defs);
  [[0,0],[.5,.09],[1,0]].forEach(([o,a])=>PM.el("stop",{offset:o,"stop-color":"#fff","stop-opacity":a},gr));
  PM.el("circle",{cx,cy,r},PM.el("clipPath",{id:id+"c"},defs));
  const disc=PM.el("circle",{cx,cy,r,fill:col},svg);
  const sw=PM.el("rect",{x:cx-r/2,y:cy-1.2*r,width:r,height:2.4*r,fill:`url(#${id}g)`,transform:`rotate(18 ${cx} ${cy})`},PM.el("g",{"clip-path":`url(#${id}c)`},svg));
  tl.fromTo(disc,{scale:.82},{scale:1,duration:.5,ease:"power4.out",svgOrigin:`${cx} ${cy}`},t0);
  tl.fromTo(sw,{x:-1.6*r},{x:1.6*r,duration:sweep,ease:"power2.out"},t0+.05); return disc;
},
rise(tl,el,t0,dur=.5) {tl.fromTo(el,{yPercent:112},{yPercent:0,duration:dur,ease:"expo.out"},t0);},
type(tl,el,t0,dur=.3) {const f=el.textContent,st={n:0}; el.textContent="";
  tl.fromTo(st,{n:0},{n:f.length,duration:Math.max(.25,dur),ease:"none",onUpdate:()=>{el.textContent=f.slice(0,Math.round(st.n));}},t0);},
hud(card,left,right,n,cur) {
  card.insertAdjacentHTML("beforeend",`<div class="pm-hud pm-l">${left}</div><div class="pm-hud pm-r">${right}</div><div class="pm-ticks">` +
    [...Array(n).keys()].map(i=>`<i class="pm-tick${i+1<cur?" pm-done":i+1===cur?" pm-now":""}"></i>`).join("")+"</div>");
},
drift(tl,rows,T,seed=1,sp=26) {rows.forEach((r,j)=>{const a=Math.sin(j*1.7+seed)*22*u;
  tl.fromTo(r.drift,{x:a},{x:a+(j%2?1:-1)*sp*u*T,duration:T,ease:"none"},0);});},
rowsIn(tl,rows,t) {rows.forEach((r,j)=>tl.fromTo(r.slide,{x:(j%2?-1:1)*(W+6*CELL)},{x:0,duration:.3,ease:"expo.inOut"},t-.12+j*.025));},
beatJumps(tl,rows,t0,beats=[4,5,6,7]) {rows.forEach((r,j)=>beats.forEach((b,k)=>{const d=(j%2?1:-1)*CELL/4;
  tl.fromTo(r.slide,{x:k*d},{x:(k+1)*d,duration:.25,ease:"expo.out",immediateRender:false},t0+b*BEAT);}));},
grain(tl,T,a=.16) {  // light film grain,re-seeded every frame by stepped offsets
  const s=PM.el("svg",{class:"pm-svg",viewBox:`0 0 ${W} ${H}`,style:`mix-blend-mode:overlay;opacity:${a}`},document.getElementById("stage"));
  const f=PM.el("filter",{id:"pmgr",x:0,y:0,width:1,height:1},s);
  PM.el("feTurbulence",{type:"fractalNoise",baseFrequency:.85,numOctaves:2,seed:7},f); PM.el("feColorMatrix",{type:"saturate",values:0},f);
  const r=PM.el("rect",{x:-80,y:-80,width:W+160,height:H+160,filter:"url(#pmgr)"},s);
  tl.fromTo(r,{x:0,y:0},{x:75,y:45,duration:.5,ease:"steps(15)",repeat:Math.max(0,Math.floor(T/.5)-1)},0);
},
};
```

Building one card (layer order = paint order; later cards are appended above earlier ones):
1. `c = div.pm-card` in `#camera`; `Tn = PM.hue(c, "cobalt")`.
2. Background: `rows = PM.pattern(PM.svg(c), hue, n, {spread: .42})`; `PM.drift(tl, rows, MV.duration, n)`.
3. `fs = PM.svg(c)`; `PM.sun(...)`; `src` = `<g id>` in `<defs>` with a 2u ground line (fg 35 %), back props, `set = PM.figure(g, Tn.fg, PM.mix(Tn.fg, disc, .42), S)`, front props; `PM.act(tl, set, keys, t, MV.end() - t, {loop, at: {x: cx, y: cy + 20 * u}, extra})`; `PM.assemble(tl, fs, src, {x: cx - 1.4 * R, y: cy - 1.2 * R, w: 2.8 * R, h: 2.4 * R}, t)`. Later cards fade `fs` in (0.25 s) at their entrance time.
4. Text: `.pm-meta` (PM.type), `.pm-mask > .pm-line.pm-title` and `.pm-second` (PM.rise), optional `.pm-tag`, `.pm-index` (absolute; `right` or `left` 130u). 5. `PM.hud(c, left, right, N, n)`; later cards fade `.pm-hud`/`.pm-ticks` in at their entrance.

Poses: angles in degrees; limbs 0 = straight down, 90 = forward (+x), 180 = overhead, negative = backward; arms are in torso space, legs in body space; `aR2`/`lR2` (forearm, shin) are absolute in the same frame; `torso` leans forward, `rot` pitches the whole body, `head` nods; R = near side, L = far side. Keys `{b, ...angles, e}` in beats. Walk: legs swap `lR1 26, lR2 6, lL1 -22, lL2 -48` every half beat, `sine.inOut`. Lift: crouch (`torso 24`, thighs 60-74, shins -6..-18, hip +0.12 S) -> arms 150-170 on the beat with `expo.out`. Throw: wind (`aR1 -118`) -> release on the beat (`aR1 132`, `torso 26`). Props (discs, rings, capsules, lines) follow joints in `extra(J, b)` (J.hR, hL, fR, fL, head, sho, hip).

Also: **chapter card** = `PM.pattern(..., {spread: .95, density: .55})` + `PM.beatJumps` + `.pm-band`/`.pm-hair` scaleX 0 -> 1; **multi-hue rows** = `HUES.map((h, j) => PM.pattern(svg, h, s + j, {only: [j]})[0])`; **count-up** = counter fromTo whose `onUpdate` writes text; **icon wall** = frozen poses in `.pm-tile`s flipping by scaleX; **grain** = `PM.grain(tl, MV.duration)` last.

## Transitions

Between scene files: `cut` (each scene opens with its own grid entrance); `fade` 0.4 s only for a mood change, with no key text in the incoming 0.4 s. Between cards in a scene (window -0.12 .. +0.3 s around the beat; rotate them):
- **rows** (signature; into every chapter card): `PM.rowsIn(tl, rows, t)` slides the incoming card's pattern rows in from alternating sides, 0.3 s `expo.inOut`, 0.025 s stagger; the card's disc, figure, text and HUD start at +0.18 s, when the rows have landed (every cover transition); optional outgoing push 0.25 W the other way (second fromTo on the old `slide` groups with `immediateRender: false`).
- **iris** (from the previous figure): `tl.fromTo(card, {clipPath: "circle(0px at Xpx Ypx)"}, {clipPath: "circle(1700px at Xpx Ypx)", duration: .38, ease: "expo.inOut"}, t - .12)` plus a cream ring overlay whose `r` grows with it and `stroke-width` 22u -> 0.
- **cols** (after chapter cards, in rapid runs): eight 240u bars of tone 2 at the bottom of the incoming card drop/rise alternately (`y` from -/+H, 0.3 s `expo.inOut`, 0.027 s stagger); the pattern fades in over 0.3 s at +0.1; entrances at +0.18 s.
- **slide** (rapid runs): incoming card `x` W -> 0, outgoing 0 -> -0.3 W, 0.38 s `expo.inOut`; an 8u edge of black 15 % on the leading edge.
- **quarter** (into a finale): tone-2 quarter-discs grow from cell corners (stagger (i + j) x 0.035 s), then as cols.
Never crossfade cards.

## Captions

A mono line on a solid band, never over the title: ink (or the hue's tone 0 via `--cap-bg`), cream mono 32u, square, saffron tick. With captions on, keep the B info bar and the index above `var(--cap-band)`; the tick bar stays under the caption box.

```css
--cap-bg: rgba(23,20,31,.94); --cap-fg: #F9F4E8; --cap-font: var(--pm-mono); --cap-size: calc(32 * var(--u)); --cap-weight: 500;
--cap-radius: 0px; --cap-bottom: calc(92 * var(--u)); --cap-accent: #EDB024; --cap-mark: tick;   /* portrait: 36u, bottom 300u */
```

## Image-gen guidance

Rarely needed. For a motif plate: "flat geometric vector pattern, circles, half circles, quarter discs, five tones of one hue, square grid, no outlines, no gradients, no text, no watermark". Never figures, faces, mascots, logos or type. Quantise to the five tones.

## Taboos

- The reference never uses shape outlines, 3D, perspective, depth of field, shake, or crossfades between cards.
- Figures have no faces, hands, clothes or emotions; never trace an existing pictogram set. No photos, mascots, real emblems or event colour names.
- Text never crosses the figure (except the B ghost title); a single-hue card never shows a second hue in its pattern.

## Known gaps

- Titles use SF Pro 900 instead of a grotesk with tighter defaults; Chinese second lines stop at PingFang SC Semibold (600), lighter than the source's 900.
- Motion blur in transition windows (sub-frame averaging) is not reproduced; the tile-flip transition between two live cards needs both cards as tiles and is only approximated (icon wall flips frozen tiles).
- Figure environments (water, ramps) need a gradient mask before the text column; not in the kit. Portrait is inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
