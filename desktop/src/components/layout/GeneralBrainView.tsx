/**
 * GeneralBrainView — the page of the built-in General Brain (design
 * docs/design/2026-10-29-default-brains-and-project-agent-standard.md §3.2).
 *
 * General is not a Projects/ DDD: its knowledge is the Markdown in Knowledge/Library and
 * its Project Agent is the Runner Agent, for tasks that belong to no Brain. The page
 * answers one decision: can such a task go out (is the Runner Agent confirmed)? So it
 * has three blocks — what it is, its knowledge, the Runner Agent's review card — and
 * none of a DDD's tabs, and no delete or rename: it is built in.
 *
 * Opened by its own view (BrainHub `openGeneral`), never by the name, so a Projects/
 * Brain that carries the same name keeps its own page.
 *
 * @exports GeneralBrainView
 */
import { useRunnerAgent, type GeneralBrain, type ProjectAgent } from '../../services/ddd';
import { ProjectAgentCard } from './ProjectAgentCard';

export function GeneralBrainView({ general, onBack, onDispatch, onOpenLibrary }: {
  general: GeneralBrain | null;
  onBack: () => void;
  /** switch to the Library overlay (Library has no show-event; the host passes openOverlay) */
  onOpenLibrary?: () => void;
  /** sends a prompt to chat (the Runner Agent's "Revise in chat") */
  onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const { data: raw, isLoading, isError, refetch } = useRunnerAgent();
  const agent = raw && typeof raw === 'object' && 'problems' in raw ? raw as ProjectAgent : null;
  const files = general?.library.files ?? null;
  const notFor = agent?.identity?.outOfScope ?? [];

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="general-brain">
      <div className="px-6 pt-4 pb-3 border-b border-[var(--color-border)]">
        <button className="text-meta text-[var(--color-text-muted)] hover:text-[var(--color-text)]" onClick={onBack}
          aria-label="Back to all brains" data-testid="general-back">‹ All brains</button>
        <div className="mt-1 flex items-center gap-2">
          <span className="material-symbols-outlined text-[var(--color-primary)]" aria-hidden="true">local_library</span>
          <h2 className="text-title font-semibold">General</h2>
          <span className="text-meta font-semibold px-1.5 rounded-full text-[var(--color-text-muted)] bg-[var(--color-hover)]"
            data-testid="general-builtin-tag">BUILT-IN</span>
        </div>
        <div className="mt-0.5 text-small text-[var(--color-text-muted)]">
          {general?.description || agent?.summary || 'For tasks that belong to no Brain.'}
        </div>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-8 max-w-[900px]">
        <section data-testid="general-what">
          <h3 className="text-heading font-semibold">What it is</h3>
          <p className="mt-1 text-body">
            Research, reports and answers that belong to no Brain, with the general knowledge in
            the Library. It is built in: it cannot be deleted or renamed.
          </p>
          {notFor.length > 0 && (
            <p className="mt-1 text-small text-[var(--color-text-muted)]">Not for: {notFor.join(' · ')}</p>
          )}
        </section>

        <section data-testid="general-knowledge">
          <h3 className="text-heading font-semibold">Knowledge</h3>
          <div className="mt-1 flex items-center gap-3">
            <span className="text-body" data-testid="general-library-count">
              Knowledge/Library{files === null ? '' : ` · ${files} Markdown ${files === 1 ? 'document' : 'documents'} taken to a Hive`}
            </span>
            <button className="btn-sm btn-secondary" data-testid="general-open-library"
              onClick={() => onOpenLibrary?.()}>Open Library</button>
          </div>
        </section>

        <section data-testid="general-agent">
          <h3 className="text-heading font-semibold">Project Agent · Runner Agent</h3>
          <div className="mt-2">
            {isLoading
              ? <div className="text-small text-[var(--color-text-muted)]" data-testid="general-agent-loading">Loading the Runner Agent…</div>
              : isError
              ? <div className="flex items-center gap-3 text-small text-[var(--color-status-error)]" data-testid="general-agent-error">
                  The Runner Agent could not be read.
                  <button className="btn-sm btn-secondary" onClick={() => void refetch()} data-testid="general-agent-retry">Retry</button>
                </div>
              : agent
              ? <ProjectAgentCard key={`runner:${agent.revision ?? ''}`} brain={null} agent={agent}
                  onRevise={(note) => { onDispatch?.(`Revise the Runner Agent (Knowledge/Library/project-agent.yaml): ${note}\nKeep it a draft and tell me when it is on the review card.`, { autoSend: true }); }} />
              : <div className="text-small text-[var(--color-text-muted)]" data-testid="general-no-agent">
                  No Runner Agent definition here yet: it is written when the workspace starts.
                </div>}
          </div>
        </section>
      </div>
    </div>
  );
}
