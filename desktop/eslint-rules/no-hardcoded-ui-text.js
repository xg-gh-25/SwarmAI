/**
 * Local ESLint rule: no hardcoded UI text in components.
 *
 * Every word a person reads in the app goes through i18n (t('key') with an en and
 * a zh value in src/i18n/locales). This rule reports text written straight into
 * JSX instead:
 *   - JSX text with at least one letter, in any script (Latin, CJK, ...);
 *   - string literals and template literals with letters in the attributes a
 *     person reads or hears: title, placeholder, aria-label, label, alt;
 *   - string literals and template literals with letters placed directly as a JSX
 *     child, e.g. {open ? 'Cancel' : 'Set up'} or {isDir && ' and its contents'}.
 * Literals are followed through ?:, &&, ||, ?? and +; never into a call, so
 * t('key', 'English default') is fine.
 *
 * Not reported:
 *   - text without letters (numbers, punctuation, arrows, ⌘, ·);
 *   - text inside <code>, <kbd>, <pre>, <samp>, or an element whose className has
 *     "font-mono" (commands, paths, file names, identifiers);
 *   - text inside an icon element (className has "material-symbols"): those are
 *     icon ligature names, not words;
 *   - files under __tests__/, *.test.*, *.stories.* (not shipped UI);
 *   - the reviewed exceptions below. Add one only for text that is the same in
 *     every language, with its reason. Everything else moves to i18n.
 *
 * Wired into eslint.contract.config.js, which CI runs as `npm run lint:contract`.
 * That config ignores inline eslint-disable comments, so there is no per-line way
 * around this rule.
 *
 * Each report's message is `Hardcoded UI text "<text>" ...`, with the text trimmed
 * and its whitespace collapsed; a template literal shows each ${...} as ${…}.
 */

/** Exact texts allowed as they are (after trimming). Each with its reason. */
export const EXCEPTIONS = [
  { text: 'SwarmAI', reason: 'product name' },
  { text: 'SwarmWS', reason: 'product name (the workspace)' },
  { text: 'Swarm', reason: "the assistant's name" },
  { text: 'Hive', reason: 'product name (the cloud instance)' },
  { text: 'Slack', reason: 'product name' },
  { text: 'GitHub', reason: 'product name' },
  { text: 'Git Bash', reason: 'product name' },
  { text: 'git', reason: 'program name' },
  { text: 'Node.js', reason: 'product name' },
  { text: 'Python', reason: 'product name' },
  { text: 'AWS', reason: 'product name' },
  { text: 'Claude', reason: 'product name' },
  { text: 'MCP', reason: 'protocol name' },
  { text: 'Esc', reason: 'key cap, printed the same in every language' },
  { text: 'Enter', reason: 'key cap, printed the same in every language' },
  { text: 'Shift', reason: 'key cap, printed the same in every language' },
  { text: 'Tab', reason: 'key cap, printed the same in every language' },
  { text: 'Ctrl', reason: 'key cap, printed the same in every language' },
  { text: 's', reason: 'unit symbol (seconds) after a number' },
  { text: 'ms', reason: 'unit symbol (milliseconds) after a number' },
  { text: 'KB', reason: 'unit symbol after a number' },
  { text: 'MB', reason: 'unit symbol after a number' },
  { text: 'GB', reason: 'unit symbol after a number' },
  { text: 'v', reason: 'version prefix, as in v2.3.0' },
  { text: '~/SwarmAI-Repos', reason: 'a folder path (placeholder), the same in every language' },
];

/** Patterns allowed as they are. Each with its reason. */
export const EXCEPTION_PATTERNS = [
  { pattern: /^(https?:\/\/|mailto:)\S+$/, reason: 'a URL' },
  { pattern: /^[A-Z]$/, reason: 'a single key cap, as in the J / K shortcut hints' },
];

const READ_ATTRIBUTES = new Set(['title', 'placeholder', 'aria-label', 'label', 'alt']);
const CODE_ELEMENTS = new Set(['code', 'kbd', 'pre', 'samp', 'style', 'script']);
const LETTER = /\p{L}/u;
const IGNORED_FILE = /(^|[\\/])__tests__[\\/]|\.test\.[^\\/]+$|\.stories\.[^\\/]+$/;

const exactExceptions = new Set(EXCEPTIONS.map((e) => e.text));

function normalize(text) {
  return text.replace(/\s+/g, ' ').trim();
}

function isAllowed(text) {
  if (!LETTER.test(text)) return true;
  if (exactExceptions.has(text)) return true;
  return EXCEPTION_PATTERNS.some((e) => e.pattern.test(text));
}

function templateText(node) {
  return node.quasis
    .map((q, i) => (q.value.cooked ?? q.value.raw) + (i < node.expressions.length ? '${…}' : ''))
    .join('');
}

/** The string pieces a value expression can show: followed through ?:, logical, +. */
function collectStrings(node, out) {
  if (!node) return out;
  switch (node.type) {
    case 'Literal':
      if (typeof node.value === 'string') out.push({ node, text: node.value, letters: node.value });
      break;
    case 'TemplateLiteral':
      out.push({
        node,
        text: templateText(node),
        letters: node.quasis.map((q) => q.value.cooked ?? q.value.raw).join(' '),
      });
      break;
    case 'ConditionalExpression':
      collectStrings(node.consequent, out);
      collectStrings(node.alternate, out);
      break;
    case 'LogicalExpression':
      collectStrings(node.left, out);
      collectStrings(node.right, out);
      break;
    case 'BinaryExpression':
      if (node.operator === '+') {
        collectStrings(node.left, out);
        collectStrings(node.right, out);
      }
      break;
    case 'TSAsExpression':
    case 'TSNonNullExpression':
      collectStrings(node.expression, out);
      break;
    default:
      break;
  }
  return out;
}

function elementName(opening) {
  return opening.name.type === 'JSXIdentifier' ? opening.name.name : null;
}

/** Every string literal anywhere in *node* (className may be built by clsx(...) etc.). */
function allStrings(node, out) {
  if (!node || typeof node !== 'object') return out;
  if (node.type === 'Literal' && typeof node.value === 'string') out.push(node.value);
  else if (node.type === 'TemplateElement') out.push(node.value.cooked ?? node.value.raw);
  for (const [key, child] of Object.entries(node)) {
    if (key === 'parent' || key === 'loc' || key === 'range') continue;
    if (Array.isArray(child)) child.forEach((c) => allStrings(c, out));
    else if (child && typeof child === 'object' && typeof child.type === 'string') allStrings(child, out);
  }
  return out;
}

function classNameOf(opening) {
  for (const attr of opening.attributes) {
    if (attr.type !== 'JSXAttribute' || attr.name.name !== 'className' || !attr.value) continue;
    return allStrings(attr.value, []).join(' ');
  }
  return '';
}

/** True when text in this JSX element is not words: code, or an icon ligature. */
function isExemptParent(parent) {
  if (!parent || parent.type !== 'JSXElement') return false;
  const name = elementName(parent.openingElement);
  if (name && CODE_ELEMENTS.has(name)) return true;
  return /material-symbols|\bfont-mono\b/.test(classNameOf(parent.openingElement));
}

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: 'problem',
    docs: { description: 'Disallow hardcoded user-facing text in JSX; use i18n (t()) instead' },
    schema: [],
    messages: {
      hardcoded:
        'Hardcoded UI text "{{text}}": move it to i18n (t(\'key\') with en and zh values in src/i18n/locales), ' +
        'or, only if it reads the same in every language, add it with a reason to the exceptions in eslint-rules/no-hardcoded-ui-text.js.',
    },
  },
  create(context) {
    const filename = context.filename ?? context.getFilename();
    if (IGNORED_FILE.test(filename)) return {};

    const report = (node, raw, letters) => {
      const text = normalize(raw);
      if (isAllowed(normalize(letters ?? raw)) || isAllowed(text)) return;
      context.report({ node, messageId: 'hardcoded', data: { text } });
    };

    return {
      JSXText(node) {
        if (isExemptParent(node.parent)) return;
        report(node, node.value);
      },
      JSXExpressionContainer(node) {
        const parent = node.parent;
        if (!parent || (parent.type !== 'JSXElement' && parent.type !== 'JSXFragment')) return;
        if (isExemptParent(parent)) return;
        for (const s of collectStrings(node.expression, [])) report(s.node, s.text, s.letters);
      },
      JSXAttribute(node) {
        const name = node.name.type === 'JSXIdentifier' ? node.name.name : null;
        if (!name || !READ_ATTRIBUTES.has(name) || !node.value) return;
        if (node.value.type === 'Literal') {
          if (typeof node.value.value === 'string') report(node.value, node.value.value);
          return;
        }
        if (node.value.type === 'JSXExpressionContainer') {
          for (const s of collectStrings(node.value.expression, [])) report(s.node, s.text, s.letters);
        }
      },
    };
  },
};

export default rule;
