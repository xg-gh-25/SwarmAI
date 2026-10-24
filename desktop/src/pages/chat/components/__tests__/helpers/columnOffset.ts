/**
 * columnOffset — the horizontal position of an element inside a turn, from the
 * Tailwind classes on the path between them (jsdom has no layout).
 *
 * Sums the left margins of the path, the left padding/border of each ancestor, and the
 * fixed-width siblings in front of it in a flex row (width + gap). A spacing class it
 * does not know THROWS. It cannot see stylesheet rules, inline styles, transforms,
 * positioned offsets or grid layouts (turnGrid.test.tsx's source guard covers inline
 * left offsets in TurnZones.tsx), and it measures only the elements a test asks for.
 *
 * Exports: columnOffset(el, root), turnRoot(container)
 */
const PX = 4; // one Tailwind spacing unit

/** px of one spacing token's value: `1.5`, `[22px]`; anything else throws. */
function tokenPx(v: string, cls: string): number {
  const arb = /^\[(-?\d+(?:\.\d+)?)px\]$/.exec(v);
  if (arb) return Number(arb[1]);
  if (/^\d+(\.\d+)?$/.test(v)) return Number(v) * PX;
  if (v === 'px') return 1;
  if (v === '0') return 0;
  throw new Error(`columnOffset: unknown spacing class ${cls}`);
}
const classes = (el: Element) => (el.getAttribute('class') || '').split(/\s+/).filter(Boolean);

function marginLeft(el: Element): number {
  let n = 0;
  for (const c of classes(el)) {
    const m = /^(-?)(?:ml|mx|m)-(.+)$/.exec(c);
    if (!m) continue;
    if (m[2] === 'auto') throw new Error(`columnOffset: ${c} on the path`);
    n += (m[1] ? -1 : 1) * tokenPx(m[2], c);
  }
  return n;
}

function contentLeft(el: Element): number {
  let n = 0;
  for (const c of classes(el)) {
    const p = /^(?:pl|px|p)-(.+)$/.exec(c);
    if (p) n += tokenPx(p[1], c);
    if (c === 'border-l' || c === 'border' || c === 'border-x') n += 1;
  }
  return n;
}

function widthOf(el: Element): number {
  for (const c of classes(el)) {
    const m = /^w-(.+)$/.exec(c);
    if (m) return tokenPx(m[1], c);
  }
  // a wrapper (the ▸ button) is as wide as its one child plus its own padding
  if (el.children.length === 1) return contentLeft(el) + widthOf(el.children[0]);
  throw new Error(`columnOffset: a flex sibling in front has no fixed width (${el.outerHTML.slice(0, 80)})`);
}

function gapOf(el: Element): number {
  for (const c of classes(el)) {
    const m = /^gap(?:-x)?-(.+)$/.exec(c);
    if (m) return tokenPx(m[1], c);
  }
  return 0;
}

const isRow = (el: Element) => {
  const cs = classes(el);
  return (cs.includes('flex') || cs.includes('inline-flex')) && !cs.includes('flex-col');
};

/** Horizontal offset of `el` inside `root` (where `el`'s own box starts). */
export function columnOffset(el: Element, root: Element): number {
  let x = 0;
  let node: Element = el;
  while (node !== root) {
    const parent = node.parentElement;
    if (!parent) throw new Error('columnOffset: root is not an ancestor');
    x += marginLeft(node);
    x += contentLeft(parent);
    if (isRow(parent)) {
      const before = [...parent.children].slice(0, [...parent.children].indexOf(node));
      for (const s of before) x += widthOf(s) + gapOf(parent);
    }
    node = parent;
  }
  return x;
}

/** Where `el`'s content starts (its box + its own left padding/border). */
export const contentOffset = (el: Element, root: Element) => columnOffset(el, root) + contentLeft(el);

/** The turn's content column (everything right of the avatar). */
export function turnRoot(container: HTMLElement): Element {
  const root = container.querySelector('.assistant-message > .flex-1');
  if (!root) throw new Error('no turn column');
  return root;
}
