/**
 * Local ESLint rule: no hardcoded UI text.
 *
 * Every word a person reads in the app goes through i18n (t('key') with an en and
 * a zh value in src/i18n/locales). This rule reports text written straight into
 * the code instead:
 *   - JSX text with at least one letter, in any script (Latin, CJK, ...);
 *   - string literals and template literals with letters in the attributes a
 *     person reads or hears: title, placeholder, aria-label, label, alt;
 *   - string literals and template literals with letters placed directly as a JSX
 *     child, e.g. {open ? 'Cancel' : 'Set up'} or {isDir && ' and its contents'};
 *   - string literals and template literals with letters as the value of an object
 *     property whose key a person reads (USER_FACING_KEYS: label, title, message,
 *     ..., and any camelCase key whose last word is one of them, like emptyMessage
 *     or ariaLabel). A key ending in "Key" (labelKey) holds an i18n key and is never
 *     checked. This is what catches a table like { navLabel: 'Library' } rendered
 *     later as {item.navLabel};
 *   - the first argument of the calls that show text to a person (TEXT_CALLS:
 *     setError, set<Name>Error, setErrorMessage, alert, confirm, toast, toast.<x>,
 *     addToast, showToast). An object argument is covered by the property check.
 * Literals are followed through ?:, &&, ||, ?? and +; never into a call, so
 * t('key', 'English default') is fine. The rule runs on .tsx and .ts files.
 *
 * Not reported:
 *   - text without letters (numbers, punctuation, arrows, ⌘, ·);
 *   - a literal that is exactly a key of src/i18n/locales/en.json (a data table that
 *     already holds i18n keys, rendered through t());
 *   - text inside <code>, <kbd>, <pre>, <samp>, or an element whose className has
 *     "font-mono" (commands, paths, file names, identifiers);
 *   - text inside an icon element (className has "material-symbols"): those are
 *     icon ligature names, not words;
 *   - files under __tests__/, *.test.*, *.stories.* (not shipped UI);
 *   - the reviewed exceptions below. Add one only for text that is the same in
 *     every language, or that is never shown to a person, with its reason.
 *     Everything else moves to i18n.
 *
 * Wired into eslint.contract.config.js, which CI runs as `npm run lint:contract`.
 * That config ignores inline eslint-disable comments, so there is no per-line way
 * around this rule.
 *
 * Each report's message is `Hardcoded UI text "<text>" ...`, with the text trimmed
 * and its whitespace collapsed; a template literal shows each ${...} as ${…}.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Exact texts allowed as they are (after trimming). Each with its reason. */
export const EXCEPTIONS = [
  { text: 'SwarmAI', reason: 'product name' },
  { text: 'SwarmWS', reason: 'product name (the workspace)' },
  { text: 'Swarm', reason: "the assistant's name" },
  { text: 'Hive', reason: 'product name (the cloud instance)' },
  { text: 'Elastic Workers', reason: 'product name (the per-task machines), the same in every locale' },
  { text: 'AgentCore Runtime', reason: 'AWS product name (where Elastic Workers runs)' },
  { text: 'Slack', reason: 'product name' },
  { text: 'GitHub', reason: 'product name' },
  { text: 'Git Bash', reason: 'product name' },
  { text: 'git', reason: 'program name' },
  { text: 'Node.js', reason: 'product name' },
  { text: 'Python', reason: 'product name' },
  { text: 'AWS', reason: 'product name' },
  { text: 'Claude', reason: 'product name' },
  { text: 'MCP', reason: 'protocol name' },
  { text: 'HTTP', reason: 'protocol name' },
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
  { text: 'streamChat', reason: 'SSE log label in services/chat.ts (a function name in the console, never shown)' },
  { text: 'answer-question', reason: 'SSE log label in services/chat.ts (a route name in the console, never shown)' },
  { text: 'cmd-permission-continue', reason: 'SSE log label in services/chat.ts (a route name in the console, never shown)' },
  { text: 'User answers: ${…}', reason: 'the skill-creation message sent to the model, never shown as UI text' },
  {
    text: 'Workspace: SwarmWS (General)',
    reason: "TSCC scopeLabel default: mirrors the backend's scope_label and TSCCPanel compares it as a value",
  },
];

/** Patterns allowed as they are. Each with its reason. */
export const EXCEPTION_PATTERNS = [
  { pattern: /^(https?:\/\/|mailto:)\S+$/, reason: 'a URL' },
  { pattern: /^[A-Z]$/, reason: 'a single key cap, as in the J / K shortcut hints' },
  { pattern: /^[⌘⇧⌥⌃]+ ?[A-Z0-9]$/, reason: 'a modifier symbol plus one key cap (⇧D), the same in every language' },
  { pattern: /^[a-z]{2}(-gov)?-[a-z]+-\d$/, reason: 'an AWS region code (us-east-1)' },
];

/** Object property keys whose string value a person reads. */
export const USER_FACING_KEYS = [
  'label', 'title', 'subtitle', 'navLabel', 'desc', 'description', 'message',
  'detail', 'placeholder', 'tooltip', 'hint',
];

/** Calls whose first argument is text a person reads. */
const TEXT_CALL_NAMES = new Set(['setError', 'setErrorMessage', 'alert', 'confirm', 'toast', 'addToast', 'showToast']);
const SET_X_ERROR = /^set[A-Z]\w*Error$/;

const READ_ATTRIBUTES = new Set(['title', 'placeholder', 'aria-label', 'label', 'alt']);
const CODE_ELEMENTS = new Set(['code', 'kbd', 'pre', 'samp', 'style', 'script']);
const LETTER = /\p{L}/u;
const IGNORED_FILE = /(^|[\\/])__tests__[\\/]|\.test\.[^\\/]+$|\.stories\.[^\\/]+$/;

const exactExceptions = new Set(EXCEPTIONS.map((e) => e.text));
const userFacingExact = new Set(USER_FACING_KEYS);
const userFacingLastWord = new Set(USER_FACING_KEYS.map((k) => k[0].toUpperCase() + k.slice(1)));

/**
 * Every leaf key of en.json, and each plural family's base (x for x_one / x_other).
 * Read from the file next to this rule; a missing or broken locale file throws, so
 * a wrong path can never silently turn the key check off.
 */
function loadLocaleKeys() {
  const path = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'i18n', 'locales', 'en.json');
  const en = JSON.parse(readFileSync(path, 'utf8'));
  const keys = new Set();
  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      const key = `${prefix}${k}`;
      if (v && typeof v === 'object') walk(v, `${key}.`);
      else {
        keys.add(key);
        keys.add(key.replace(/_(one|other|zero|two|few|many)$/, ''));
      }
    }
  };
  walk(en, '');
  return keys;
}
const localeKeys = loadLocaleKeys();

/**
 * True when *name* is a key whose value a person reads: one of USER_FACING_KEYS, or
 * a camelCase name whose last word is one (emptyMessage, ariaLabel). A key holder
 * (labelKey, titleKey) ends in the word "Key", so it never matches.
 */
export function isUserFacingKey(name) {
  if (typeof name !== 'string') return false;
  if (userFacingExact.has(name)) return true;
  const m = /[a-z0-9]([A-Z][a-z]*)$/.exec(name);
  return !!m && userFacingLastWord.has(m[1]);
}

function normalize(text) {
  return text.replace(/\s+/g, ' ').trim();
}

function isAllowed(text) {
  if (!LETTER.test(text)) return true;
  if (exactExceptions.has(text)) return true;
  if (localeKeys.has(text)) return true;
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
    case 'TSSatisfiesExpression':
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

/** The name of an object property's key: `label:` and `'label':` alike. */
function propertyKeyName(prop) {
  if (prop.computed) return null;
  if (prop.key.type === 'Identifier') return prop.key.name;
  if (prop.key.type === 'Literal' && typeof prop.key.value === 'string') return prop.key.value;
  return null;
}

/** True when this call shows its first argument to a person. */
function isTextCall(callee) {
  if (callee.type === 'Identifier') return TEXT_CALL_NAMES.has(callee.name) || SET_X_ERROR.test(callee.name);
  if (callee.type === 'MemberExpression' && !callee.computed && callee.property.type === 'Identifier') {
    const prop = callee.property.name;
    if (callee.object.type === 'Identifier' && callee.object.name === 'toast') return true;
    return TEXT_CALL_NAMES.has(prop) || SET_X_ERROR.test(prop);
  }
  return false;
}

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: 'problem',
    docs: { description: 'Disallow hardcoded user-facing text; use i18n (t()) instead' },
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
    const reportValue = (expr) => {
      for (const s of collectStrings(expr, [])) report(s.node, s.text, s.letters);
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
        reportValue(node.expression);
      },
      JSXAttribute(node) {
        const name = node.name.type === 'JSXIdentifier' ? node.name.name : null;
        if (!name || !READ_ATTRIBUTES.has(name) || !node.value) return;
        if (node.value.type === 'Literal') {
          if (typeof node.value.value === 'string') report(node.value, node.value.value);
          return;
        }
        if (node.value.type === 'JSXExpressionContainer') reportValue(node.value.expression);
      },
      Property(node) {
        if (!node.parent || node.parent.type !== 'ObjectExpression') return;
        if (!isUserFacingKey(propertyKeyName(node))) return;
        reportValue(node.value);
      },
      CallExpression(node) {
        if (!isTextCall(node.callee) || node.arguments.length === 0) return;
        reportValue(node.arguments[0]);
      },
    };
  },
};

export default rule;
