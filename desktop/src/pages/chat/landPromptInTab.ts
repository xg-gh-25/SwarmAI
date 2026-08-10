/**
 * landPromptInTab — the PURE landing verdict shared by all overlay→chat-tab
 * dispatch handlers (NewBrain / Jobs / Pipeline / Pollinate → dispatchPrompt) AND
 * History's resume-in-tab. It unifies the ~40-line prefix the three ChatPage
 * handlers (handleDispatchTodo / handleDispatchJobPrompt / handleResumeSession)
 * each used to re-implement: build the tab list → resolveResumeTarget → decide
 * needs-close / reuse / newtab, plus the copy-pasted unsent-draft guard.
 *
 * It stays a PURE function (no toast, no tab mutation) — mirror of
 * resolveResumeTarget's testability. The caller (ChatPage) turns a `blocked`
 * verdict into a toast (landingToast) and a `land` verdict into the mode-specific
 * tab setup + its own body (inject / loadSession / todo-track). That body is where
 * the three handlers genuinely differ, so it stays OUT of here (a full-body helper
 * would be the 5-flag god-function Gate-1 rejected).
 *
 * The draft guard is OPT-IN (`applyDraftGuard`): the two dispatch handlers pass
 * true (a typed draft in the tab about to be reused would be silently lost); resume
 * passes false — it never had a draft guard, and adding one would be a behavior
 * change (Gate-1 finding). `hasDraft` is computed by the caller (it reads ChatPage
 * refs) so this function stays pure.
 *
 * @exports classifyLanding, LandingVerdict, LandingGuardOpts
 */
import { resolveResumeTarget, type ResumeTabInfo } from './resumeTarget';

/** The side-effect-free landing decision. `focus` carries the already-open tab id;
 *  `reuse` carries the active idle tab id; `newtab` needs no id (caller adds one).
 *  `occupied` = at cap and the only reuse candidate holds real history we refuse to
 *  wipe (dispatch convergence) → caller toasts "close a tab". */
export type LandingVerdict =
  | { kind: 'land'; mode: 'focus' | 'reuse'; tabId: string }
  | { kind: 'land'; mode: 'newtab' }
  | { kind: 'blocked'; reason: 'cap' | 'busy' | 'draft' | 'occupied'; busyStatus?: string };

/** Draft-guard inputs. `hasDraft` = the target-tab reuse would clobber an unsent
 *  draft (caller computes from input/attachment refs). `applyDraftGuard` = whether
 *  this caller wants the guard at all (dispatch=true, resume=false).
 *
 *  `allowReuseCurrent` gates the reuse-current branch (run: dispatch convergence).
 *  Resume passes true — it CLEARS then RELOADS the session, so reusing an idle tab
 *  with history loses nothing. The two dispatch handlers pass FALSE: dispatching is
 *  new work and clearing a history-bearing idle tab would silently destroy that
 *  conversation. When false we still reuse a genuinely EMPTY idle tab (no sessionId)
 *  so a chatMax===1 user is never deadlocked (Gate-1 CRITICAL parity, resumeTarget
 *  module doc); an idle tab WITH a sessionId becomes blocked:occupied → "close a
 *  tab". This is the "dispatch 收敛到只开新 tab,满了让用户关 tab" product decision. */
export interface LandingGuardOpts {
  hasDraft: boolean;
  applyDraftGuard: boolean;
  allowReuseCurrent: boolean;
}

/**
 * Decide where a prompt/session should land, purely. `key` is the session id for
 * resume (can match an open tab → focus) or a never-matching sentinel for dispatch
 * (new work, never focus). `activeTab` is the currently-selected tab (the only tab
 * eligible for reuse). Returns a verdict the caller executes.
 */
export function classifyLanding(
  key: string,
  tabs: ResumeTabInfo[],
  chatMax: number,
  activeTabId: string | undefined,
  guard: LandingGuardOpts,
): LandingVerdict {
  const decision = resolveResumeTarget(key, tabs, chatMax, activeTabId);

  switch (decision.action) {
    case 'focus':
      return { kind: 'land', mode: 'focus', tabId: decision.tabId };
    case 'newtab':
      return { kind: 'land', mode: 'newtab' };
    case 'reuse-current': {
      // Reuse clears the active idle tab. If it holds an unsent draft AND this
      // caller opted into the guard, refuse rather than destroy the draft.
      if (guard.applyDraftGuard && guard.hasDraft) return { kind: 'blocked', reason: 'draft' };
      // Dispatch convergence: dispatch (allowReuseCurrent=false) must NOT wipe a
      // history-bearing idle tab. Reuse only a genuinely EMPTY idle tab (no session)
      // — that loses nothing and keeps a chatMax===1 user unblocked (Gate-1). An
      // idle tab that already holds a conversation → blocked:occupied ("close a
      // tab"). Resume (allowReuseCurrent=true) always reuses: it reloads a session
      // right after clearing, so nothing is lost.
      if (!guard.allowReuseCurrent) {
        const target = tabs.find((t) => t.id === decision.tabId);
        if (target?.sessionId) return { kind: 'blocked', reason: 'occupied' };
      }
      return { kind: 'land', mode: 'reuse', tabId: decision.tabId };
    }
    case 'needs-close':
    default: {
      // No free slot and the active tab is not reusable. Distinguish "the active
      // tab is busy (streaming/waiting/…)" from "there is no active tab to reuse"
      // so the caller can show a status-specific toast (streaming gets its own).
      const active = activeTabId ? tabs.find((t) => t.id === activeTabId) : undefined;
      if (active) return { kind: 'blocked', reason: 'busy', busyStatus: active.status };
      return { kind: 'blocked', reason: 'cap' };
    }
  }
}
