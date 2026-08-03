/**
 * newBrainDispatch — pure helpers for the New Brain launcher.
 *
 * The launcher is a one-shot birth gate: it COLLECTS (name + what-it-governs +
 * starter material) and DISPATCHES a single categorized-manifest prompt into a
 * chat tab, then closes. It never reads/fetches the material and never tracks
 * progress (that runs later in chat via s_project-manager; status lives in
 * Brain Hub). These two helpers are the whole non-UI logic, kept pure so they
 * are unit-testable without React.
 *
 * @exports classifyStarterItem, buildBrainManifest, StarterRole, StarterItem,
 *          GovernsKind, detectKind
 */

/** The three roles a starter item can play — sorted BY TYPE (a rule, no LLM). */
export type StarterRole = 'GOVERN' | 'DISTILL' | 'SHELF';

/** What the whole brain governs (the load-bearing P0 decision, single-select). */
export type GovernsKind = 'codebase' | 'data' | 'documents' | 'service' | 'idea';

/** A raw item the user dropped/pasted, before or after role assignment. */
export interface StarterItem {
  /** The literal the user provided: a URL, a local path, or pasted text. */
  value: string;
  /** Optional caller-supplied hint from the drop event (e.g. a dropped OS
   *  folder → 'folder', a dropped file → 'file'). When absent we detect from
   *  the value. */
  kind?: StarterKind;
  /** The assigned role — auto by classifyStarterItem, then user-correctable. */
  role: StarterRole;
}

/** The item's TYPE, either supplied by the drop or detected from the value. */
export type StarterKind = 'repo' | 'folder' | 'file' | 'link' | 'text';

const GIT_URL_RE = /(^git@)|(\.git\/?$)|(github\.com|gitlab\.com|bitbucket\.org)/i;
const URL_RE = /^(https?:\/\/|www\.)/i;
// A path that ends in a slash, or is a bare dir name, reads as a folder.
const FOLDER_RE = /\/$/;
// Common single-document extensions that DISTILL (read → judgment).
const DOC_EXT_RE = /\.(md|markdown|txt|pdf|docx?|rtf|html?|csv|json|ya?ml|rst|org|tex)$/i;

/**
 * Detect an item's TYPE from its raw value (used when the drop gave no hint).
 * Pure + deterministic — this is the whole "by type" rule, no content reading.
 */
export function detectKind(value: string): StarterKind {
  const v = value.trim();
  if (GIT_URL_RE.test(v)) return 'repo';
  // A local .git directory or a path literally ending in .git → a repo.
  if (/\.git\/?$/.test(v)) return 'repo';
  if (URL_RE.test(v)) return 'link';
  if (FOLDER_RE.test(v)) return 'folder';
  // A local-ish path (POSIX slash/~/./ OR a Windows path: backslash or C:\ drive)
  // — Gate-2 #1: without backslash awareness a Windows folder fell through to
  // 'text' (→ DISTILL), the exact "local paths" case the launcher advertises.
  const looksLikePath =
    /[/\\~]/.test(v) || v.startsWith('./') || /^[a-zA-Z]:[\\/]/.test(v);
  if (looksLikePath && DOC_EXT_RE.test(v)) return 'file';
  if (DOC_EXT_RE.test(v)) return 'file';
  // A path with no doc extension and no trailing slash: if it has slashes it's a
  // local path we can't prove is a file → treat as folder (SHELF, the safe default).
  if (looksLikePath) return 'folder';
  // Anything else — a phrase, a note — is pasted text.
  return 'text';
}

/**
 * Classify a starter item into a role, PURELY by type (never by content).
 *
 * The rule (XG-confirmed defaults):
 *   - repo (git URL / .git dir)        → GOVERN  (an asset the brain manages)
 *   - single doc file / link / text    → DISTILL (read → becomes judgment)
 *   - folder / anything else           → SHELF   (kept for reference, on-demand)
 *
 * folder defaults to SHELF (safe default — the launcher can't see contents; chat
 * may later suggest DISTILL after reading). The returned role is a DEFAULT the
 * user can override via the pill.
 */
export function classifyStarterItem(input: { value: string; kind?: StarterKind }): StarterRole {
  const kind = input.kind ?? detectKind(input.value);
  switch (kind) {
    case 'repo':
      return 'GOVERN';
    case 'file':
    case 'link':
    case 'text':
      return 'DISTILL';
    case 'folder':
    default:
      return 'SHELF';
  }
}

const GOVERNS_LABEL: Record<GovernsKind, string> = {
  codebase: 'a codebase',
  data: 'a data source',
  documents: 'documents',
  service: 'a service / process',
  idea: 'just an idea (nothing yet)',
};

/**
 * Build the ONE categorized-manifest prompt dispatched into the chat tab.
 *
 * This is what "Create Brain" hands to the agent — a self-contained instruction
 * to run the s_project-manager 6-phase setup, with the collected material grouped
 * by role. It reads as a normal user prompt (autoSend:false — the user reviews +
 * sends it), so it must be legible, not a JSON blob.
 *
 * Items are grouped GOVERN → DISTILL → SHELF; an empty group is omitted. A brain
 * with no starter material still produces a valid prompt (name + governs only).
 */
export function buildBrainManifest(
  name: string,
  governs: GovernsKind,
  items: StarterItem[],
): string {
  const trimmedName = name.trim() || 'Untitled Brain';
  const lines: string[] = [];
  lines.push(`Create a new Brain "${trimmedName}" — it governs ${GOVERNS_LABEL[governs]}.`);
  lines.push('');

  const order: StarterRole[] = ['GOVERN', 'DISTILL', 'SHELF'];
  const roleNote: Record<StarterRole, string> = {
    GOVERN: 'GOVERN (the asset the brain manages — bind it, don\'t store as text)',
    DISTILL: 'DISTILL (read → distill into the DDD docs; keep a source pointer)',
    SHELF: 'SHELF (keep for reference in the Library, fetch on demand)',
  };
  const grouped = order
    .map((role) => ({ role, entries: items.filter((it) => it.role === role) }))
    .filter((g) => g.entries.length > 0);

  if (grouped.length > 0) {
    lines.push('Starter material:');
    for (const { role, entries } of grouped) {
      lines.push(`  ${roleNote[role]}:`);
      for (const it of entries) lines.push(`    - ${it.value.trim()}`);
    }
    lines.push('');
  }

  lines.push(
    'Set it up with me: run the s_project-manager 6-phase setup (P0 define assets → ' +
      'P2 read & distill the material into PRODUCT/TECH/IMPROVEMENT/PROJECT → bind/CodeGraph ' +
      'if there is a repo → shelve references → P6 verify). Confirm the governed assets first, ' +
      'and surface anything you cannot reach (login-walled links, missing paths) or any ' +
      'conflicts between sources for me to resolve.',
  );
  return lines.join('\n');
}
