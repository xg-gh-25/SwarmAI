/**
 * OS Eval overlay — six tabs: Overview (can this version be released, what needs
 * me), Golden Cases (manage the cases), Online Eval (real sessions), Offline Eval
 * (the checks before a release), LLM Judges (where the L2 scores come from), Guide
 * (the Eval-First method). Design: docs/design/2026-10-26-eval-as-product.md §8 and
 * the mock Knowledge/Mockups/2026-10-26-eval-overlay-redesign.html.
 *
 * Context health and governance proposals live in the C&M overlay, not here.
 * "Let Swarm look" and "open the session" go through the overlay host's bridges
 * (OverlayRenderCtx.dispatchPrompt / resumeSession) and close the overlay.
 */
import { useCallback, useMemo, useState } from 'react';
import { chatService } from '../../services/chat';
import { useGoldenSet, useSessionQuality } from './api';
import { GoldenCasesTab } from './GoldenCasesTab';
import { GuideTab } from './GuideTab';
import { JudgesTab } from './JudgesTab';
import { OfflineTab } from './OfflineTab';
import { OnlineTab } from './OnlineTab';
import { OverviewTab } from './OverviewTab';
import { EvalActionsContext, GoContext, useLang, type EvalActions, type TabId } from './ui';

export interface EvalOverlayProps {
  /** Land a prompt in a chat tab (returns false when it could not). */
  dispatchPrompt?: (prompt: string) => boolean;
  /** Resume a session in a chat tab. */
  resumeSession?: (session: unknown) => boolean;
  close?: () => void;
  /** The tab it opens on (a deep link); Overview by default. */
  initialTab?: TabId;
}

export default function EvalOverlay({ dispatchPrompt, resumeSession, close, initialTab }: EvalOverlayProps) {
  const { t } = useLang();
  const [tab, setTab] = useState<TabId>(initialTab ?? 'overview');
  const [notice, setNotice] = useState<string | null>(null);
  const gs = useGoldenSet();
  const online = useSessionQuality();

  const go = useCallback((to: TabId, anchor?: string) => {
    setTab(to);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (anchor) document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth' });
      else { const el = document.getElementById('eval-scroll'); if (el) el.scrollTop = 0; }
    }));
  }, []);

  const actions = useMemo<EvalActions>(() => ({
    askSwarm: (prompt) => {
      if (dispatchPrompt?.(prompt)) close?.();
      else setNotice(t('No chat tab could take it right now: every tab is busy.', '现在没有能接的聊天标签：都在忙。'));
    },
    openSession: (sessionId) => {
      void (async () => {
        try {
          const session = await chatService.getSession(sessionId);
          if (session && resumeSession?.(session)) { close?.(); return; }
        } catch { /* deleted or unknown session */ }
        setNotice(t(`Session ${sessionId} could not be opened (it may have been deleted).`, `会话 ${sessionId} 打不开（可能已删除）。`));
      })();
    },
  }), [dispatchPrompt, resumeSession, close, t]);

  const failing = (gs.data?.cases ?? []).filter((c) => c.tier !== 'archived' && c.last_result && c.last_result.status !== 'passed').length;
  const low = online.data?.low_details?.length ?? 0;
  const tabs: { id: TabId; label: string; n?: number }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'cases', label: 'Golden Cases' },
    { id: 'online', label: 'Online Eval', n: low },
    { id: 'offline', label: 'Offline Eval', n: failing },
    { id: 'judges', label: 'LLM Judges' },
    { id: 'guide', label: 'Guide' },
  ];

  return (
    <EvalActionsContext.Provider value={actions}>
      <GoContext.Provider value={go}>
        <div className="flex flex-col h-full" data-testid="eval-overlay">
          <div className="shrink-0 px-7 pt-3 border-b border-[var(--color-border)]">
            <nav className="flex gap-7" role="tablist">
              {tabs.map((x) => (
                <button key={x.id} type="button" role="tab" aria-selected={tab === x.id} data-testid={`eval-tab-${x.id}`}
                        onClick={() => go(x.id)}
                        className={`py-2.5 text-body border-b-2 -mb-px whitespace-nowrap transition-colors ${tab === x.id
                          ? 'text-[var(--color-text)] border-[var(--color-brand)]'
                          : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)]'}`}>
                  {x.label}{x.n ? <span className="ml-1.5 text-meta tabular-nums text-[var(--color-brand)]">{x.n}</span> : null}
                </button>
              ))}
            </nav>
          </div>
          {notice && (
            <div className="shrink-0 px-7 py-2 text-small text-[var(--color-status-warning)] flex gap-3" role="status">
              <span className="flex-1">{notice}</span>
              <button type="button" onClick={() => setNotice(null)} className="text-[var(--color-text-muted)]">✕</button>
            </div>
          )}
          <div id="eval-scroll" className="flex-1 min-h-0 overflow-y-auto">
            {tab === 'overview' && <OverviewTab />}
            {tab === 'cases' && <GoldenCasesTab />}
            {tab === 'online' && <OnlineTab />}
            {tab === 'offline' && <OfflineTab />}
            {tab === 'judges' && <JudgesTab />}
            {tab === 'guide' && <GuideTab />}
          </div>
        </div>
      </GoContext.Provider>
    </EvalActionsContext.Provider>
  );
}
