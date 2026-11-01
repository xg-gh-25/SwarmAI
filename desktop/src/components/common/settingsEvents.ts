/**
 * Window events that cross from a component outside the app shell (or deep in a panel)
 * to the shell that owns the surface (ThreeColumnLayout). One neutral home, so a banner,
 * a Need-You row or a settings page can open them without importing each other.
 *
 * - OPEN_SETTINGS_EVENT `{tab?}`: open Settings on that tab.
 * - FIX_SIGN_IN_EVENT: a chat error card's "fix the sign-in" button. The chat's sign-in
 *   line (CredentialBanner) takes it: it re-verifies, shows itself and runs its one action,
 *   so the card and the line never disagree about what to do.
 * - TERMINAL_RUN_EVENT `{command, title}`: open a new SwarmAI terminal at the current
 *   terminal location and type *command* into it, without Enter (the person runs it;
 *   design docs/design/2026-11-01-settings-account-merge.md §4.3). The command always
 *   comes from the backend (core/auth_remediation `action`) or is a fixed string (the
 *   account page's Midway wait: `mwinit -f`); never built from input here.
 */
export const OPEN_SETTINGS_EVENT = 'swarm:open-settings';
export const TERMINAL_RUN_EVENT = 'swarm:terminal-run';
export const FIX_SIGN_IN_EVENT = 'swarm:fix-sign-in';

export function fixSignIn(): void {
  window.dispatchEvent(new CustomEvent(FIX_SIGN_IN_EVENT));
}

export function openSettings(tab?: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab } }));
}

export function runInTerminal(command: string, title: string): void {
  window.dispatchEvent(new CustomEvent(TERMINAL_RUN_EVENT, { detail: { command, title } }));
}

// ── hand-offs between the chat's sign-in line and the account page ──────────
// Module state, not an event: the page may mount after the line acted.

const TERMINAL_FRESH_MS = 5 * 60_000;
let terminalRunAt = 0;
let signInRequested = false;

/** The sign-in command was typed into a terminal now (the line or the page did it). */
export function markTerminalRun(): void { terminalRunAt = Date.now(); }
/** Whether that happened in the last 5 minutes: the page then offers "Verify again" first. */
export function terminalRunRecently(): boolean { return Date.now() - terminalRunAt < TERMINAL_FRESH_MS; }

/** Open the account page straight into its in-app sign-in (design §4.2 `sign_in`). */
export function openSignIn(): void { signInRequested = true; openSettings('aws-account'); }
/** The account page takes the request once, when it has loaded. */
export function takeSignInRequest(): boolean { const r = signInRequested; signInRequested = false; return r; }
