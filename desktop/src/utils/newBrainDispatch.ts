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
 * What the brain governs is not asked: it is the GOVERN items (none = a
 * pure-knowledge brain). The Project Agent is not asked either: the agent
 * generates it from the DDD at P3b and the person reviews it.
 *
 * @exports classifyStarterItem, buildBrainManifest, previewSections, StarterRole,
 *          StarterItem, StarterKind, detectKind, PreviewSection
 */

/** The three roles a starter item can play — sorted BY TYPE (a rule, no LLM). */
export type StarterRole = 'GOVERN' | 'DISTILL' | 'SHELF';

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

// The backend's project-name rule (swarm_workspace_manager._PROJECT_NAME_RE +
// reserved names), checked BEFORE dispatch so a bad name fails in the form, not
// minutes later in chat when create_project returns 400/409.
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,99}$/;
const RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL',
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`), ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`)]);

/** Why this Brain name cannot be used, or null when it can. Empty → null (the
 *  Create button is disabled for an empty name instead of showing an error). */
export function validateBrainName(raw: string, existing: string[] = []): string | null {
  const name = raw.trim();
  if (!name) return null;
  if (!NAME_RE.test(name)) {
    return 'Use letters, numbers, spaces, - _ or . (start with a letter or number, at most 100).';
  }
  if (RESERVED.has(name.split('.')[0].toUpperCase())) return `"${name}" is a reserved name on Windows.`;
  // the built-in General Brain (Knowledge/Library); the backend refuses it too
  // (swarm_workspace_manager.reject_builtin_brain_name)
  if (name.toLowerCase() === 'general') return `"${name}" is the built-in General Brain (Knowledge/Library); choose another name.`;
  if (existing.some((e) => e.toLowerCase() === name.toLowerCase())) return `A Brain named "${name}" already exists.`;
  return null;
}

export type PreviewSection =
  | 'identity' | 'knowledge' | 'gates' | 'capabilities' | 'delivery' | 'refresher';

/**
 * Which of the six sections this material fills at creation (true), versus grows
 * later in use (false). Identity, Capabilities and Delivery (the Project Agent) are
 * always created; Knowledge needs something to read; Gates and the Refresher come
 * from a governed asset (a repo's CI, its code-intel).
 */
export function previewSections(items: Pick<StarterItem, 'role'>[]): Record<PreviewSection, boolean> {
  const gov = items.some((i) => i.role === 'GOVERN');
  const read = items.some((i) => i.role === 'DISTILL');
  return { identity: true, knowledge: gov || read, gates: gov, capabilities: true, delivery: true, refresher: gov };
}

/**
 * Build the ONE categorized-manifest prompt dispatched into the chat tab.
 *
 * A self-contained instruction to run the s_project-manager setup (P0..P6 with
 * P3b, the Project Agent), with the material grouped by role. It reads as a normal
 * user prompt, so it must be legible, not a JSON blob. Items are grouped
 * GOVERN → DISTILL → SHELF; an empty group is omitted. No material is valid: a
 * pure-knowledge brain.
 */
export function buildBrainManifest(name: string, items: StarterItem[]): string {
  const trimmedName = name.trim() || 'Untitled Brain';
  const assets = items.filter((it) => it.role === 'GOVERN').length;
  const lines: string[] = [];
  lines.push(`Create a new Brain "${trimmedName}" — ${assets === 0
    ? 'a pure-knowledge brain (it governs no asset yet)'
    : `it governs ${assets} asset${assets === 1 ? '' : 's'} (listed below)`}.`);
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
    'Set it up with me: run the s_project-manager setup (P0 confirm the governed assets, ' +
      'artifact_cli classify-starter for a folder whose role is unclear → P1 create → P3 bind each repo ' +
      'first, which also surveys it → P2 read the survey and the material whole, write the first typed ' +
      'entries section by section into bootstrap-entries.json, write them with artifact_cli ' +
      'bootstrap-apply (fix what it rejects), ask me only about the PRODUCT sections it reports without ' +
      'evidence (with a "not sure yet" choice) and record the rest as gaps → ' +
      'P3b generate the Project Agent (project-agent.yaml) from this knowledge, ' +
      'using only the pipeline profiles, and show it to me to confirm or change → P4 code graph ' +
      'for a repo → P5 skills → P6 verify). Surface anything you cannot reach (login-walled links, ' +
      'missing paths) or any conflicts between sources for me to resolve. If the name is taken ' +
      '(create returns 409), stop and ask me for another name. Run P6 with --record. Whether or not it ' +
      'passes, tell me what was written, which sections are gaps and what is still open; when it passes, ' +
      'open Brain Hub for me and tell me the brain is ready.',
  );
  return lines.join('\n');
}

/** The material lines, grouped GOVERN → DISTILL → SHELF (shared by both prompts). */
function materialLines(items: StarterItem[], notes: Record<StarterRole, string>): string[] {
  const out: string[] = [];
  for (const role of ['GOVERN', 'DISTILL', 'SHELF'] as StarterRole[]) {
    const entries = items.filter((it) => it.role === role);
    if (!entries.length) continue;
    out.push(`  ${notes[role]}:`);
    for (const it of entries) out.push(`    - ${it.value.trim()}`);
  }
  return out;
}

/**
 * The prompt that adds material to an EXISTING Brain. It routes each role to the
 * skill that already owns that write, with that skill's own discipline:
 * DISTILL → s_ddd-persist (admission gate: most facts are not persisted; additive,
 * locked writes, a source pointer per entry; a rule change for Gates / Delivery is
 * proposed, not written), links → s_learn-content cards first, SHELF → s_library,
 * GOVERN → bind it as a new asset (P3 + P4) and revise the Project Agent if it
 * changes how the Brain delivers. Ends with P6 --record.
 */
export function buildAddKnowledgePrompt(name: string, items: StarterItem[]): string {
  const lines = [`Add knowledge to the "${name.trim()}" Brain (s_project-manager: Add knowledge to an existing Brain).`, '', 'Material:'];
  lines.push(...materialLines(items, {
    GOVERN: 'GOVERN (a new asset: bind it in bindings.yaml, build the code graph for a repo)',
    DISTILL: 'DISTILL (read it, then sediment what is worth keeping into PRODUCT/TECH/IMPROVEMENT/PROJECT)',
    SHELF: 'SHELF (mount for reference in the Library; do not copy it into the docs)',
  }));
  lines.push('');
  lines.push(
    'Use the skills that own each write, with their rules: this Brain\'s s_ddd-persist for every doc write (its admission ' +
      'gate decides what is worth keeping; additive, with a source pointer on each entry; never ' +
      'overwrite existing judgment), s_learn-content for a link before distilling it, s_library to ' +
      'shelve a folder, and s_project-manager P3/P4 for a new asset. A change to Gates or to how the ' +
      'Brain delivers is a proposal for me to review, not a direct write; if a new asset changes the ' +
      'Project Agent, show me the revised project-agent.yaml to confirm. Tell me what you added, ' +
      'what you skipped and why, and anything you could not reach. Finish with P6 (--record).',
  );
  return lines.join('\n');
}

/** The prompt that resumes an unfinished setup: verify first, then do only what is missing. */
export function buildResumeSetupPrompt(name: string): string {
  return [
    `Continue setting up the "${name.trim()}" Brain.`,
    '',
    'Run the P6 check first (s_project-manager verify_ddd_complete.py --project ' +
      `"${name.trim()}") to see which phases are missing, then finish only those, in order: ` +
      'P2 knowledge (survey, entries, bootstrap-apply), P3 bind, P3b the Project Agent (show it to me ' +
      'to confirm), P4 code graph, ' +
      'P5 skills. Do not redo or overwrite what is already written. Finish with P6 --record, ' +
      'then open Brain Hub for me and tell me it is ready.',
  ].join('\n');
}
