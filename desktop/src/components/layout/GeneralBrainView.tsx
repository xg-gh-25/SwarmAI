/**
 * GeneralBrainView — the page of the built-in General Brain (design
 * docs/design/2026-10-29-default-brains-and-project-agent-standard.md §3.2).
 *
 * General is not a Projects/ DDD: its knowledge is the Markdown in Knowledge/Library and
 * its Project Agent is the Runner Agent, for tasks that belong to no Brain. The page
 * answers one decision: can such a task go out (is the Runner Agent ready)? So it
 * has four blocks — what it is, its knowledge, the Runner Agent's definition, then what it
 * learned (the CDF entries in Knowledge/Library, with Withdraw) — and
 * none of a DDD's tabs, and no delete or rename: it is built in.
 *
 * Opened by its own view (BrainHub `openGeneral`), never by the name, so a Projects/
 * Brain that carries the same name keeps its own page.
 *
 * @exports GeneralBrainView
 */
import { useTranslation } from 'react-i18next';
import { useRunnerAgent, type GeneralBrain, type ProjectAgent } from '../../services/ddd';
import { ProjectAgentView } from './ProjectAgentView';
import { BackToHubButton } from './BackToHubButton';
import { BrainLearnedView } from './BrainLearnedView';

export function GeneralBrainView({ general, onBack, onDispatch, onOpenLibrary }: {
  general: GeneralBrain | null;
  onBack: () => void;
  /** switch to the Library overlay (Library has no show-event; the host passes openOverlay) */
  onOpenLibrary?: () => void;
  /** sends a prompt to chat (the Runner Agent's "Revise in chat") */
  onDispatch?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const { t } = useTranslation();
  const { data: raw, isLoading, isError, refetch } = useRunnerAgent();
  const agent = raw && typeof raw === 'object' && 'problems' in raw ? raw as ProjectAgent : null;
  const files = general?.library.files ?? null;

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]" data-testid="general-brain">
      <div className="px-6 pt-4 pb-3 border-b border-[var(--color-border)]">
        <BackToHubButton testid="general-back" onClick={onBack} />
        <div className="mt-1 flex items-center gap-2">
          <span className="material-symbols-outlined text-[var(--color-primary)]" aria-hidden="true">local_library</span>
          <h2 className="text-title font-semibold">{t('ui.GeneralBrainView.title')}</h2>
          <span className="text-meta font-semibold px-1.5 rounded-full text-[var(--color-text-muted)] bg-[var(--color-hover)]"
            data-testid="general-builtin-tag">{t('ui.GeneralBrainView.builtinTag')}</span>
        </div>
        <div className="mt-0.5 text-small text-[var(--color-text-muted)]">
          {general?.description || agent?.summary || t('ui.GeneralBrainView.defaultDescription')}
        </div>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-8 max-w-[1180px]">
        <section data-testid="general-what">
          <h3 className="text-heading font-semibold">{t('ui.GeneralBrainView.whatTitle')}</h3>
          <p className="mt-1 text-body">{t('ui.GeneralBrainView.whatBody')}</p>
        </section>

        <section data-testid="general-knowledge">
          <h3 className="text-heading font-semibold">{t('ui.GeneralBrainView.knowledgeTitle')}</h3>
          <div className="mt-1 flex items-center gap-3">
            <span className="text-body" data-testid="general-library-count">
              {files === null ? t('ui.GeneralBrainView.libraryPath') : t('ui.GeneralBrainView.libraryCount', { count: files })}
            </span>
            <button className="btn-sm btn-secondary" data-testid="general-open-library"
              onClick={() => onOpenLibrary?.()}>{t('ui.GeneralBrainView.openLibrary')}</button>
          </div>
        </section>

        <section data-testid="general-agent">
          <h3 className="text-heading font-semibold">{t('ui.GeneralBrainView.agentTitle')}</h3>
          <div className="mt-2">
            {isLoading
              ? <div className="text-small text-[var(--color-text-muted)]" data-testid="general-agent-loading">{t('ui.GeneralBrainView.agentLoading')}</div>
              : isError
              ? <div className="flex items-center gap-3 text-small text-[var(--color-status-error)]" data-testid="general-agent-error">
                  {t('ui.GeneralBrainView.agentError')}
                  <button className="btn-sm btn-secondary" onClick={() => void refetch()} data-testid="general-agent-retry">{t('common.button.retry')}</button>
                </div>
              : agent
              ? <ProjectAgentView key="runner" brain={null} agent={agent}
                  onRevise={(note) => { onDispatch?.(`Change the Runner Agent (Knowledge/Library/project-agent.yaml): ${note}\nEdit it, check it with project_agent.validate, and tell me what you changed.`, { autoSend: true }); }} />
              : <div className="text-small text-[var(--color-text-muted)]" data-testid="general-no-agent">
                  {t('ui.GeneralBrainView.noAgent')}
                </div>}
          </div>
        </section>

        {/* what the AI wrote into Knowledge/Library while it worked (CDF's records) */}
        <section data-testid="general-learned" className="-mx-6">
          <BrainLearnedView name="General" />
        </section>
      </div>
    </div>
  );
}
