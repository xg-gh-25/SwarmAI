/**
 * OS Eval overlay — four tabs, one question each: Overview (can this version be
 * used, and what needs me), Cases (the golden set with each case's latest result and
 * the run history), Live sessions (the owner's real sessions, scored), Method (the
 * Eval-First guide and the judges). Design: docs/design/2026-10-26-eval-as-product.md
 * §8 (revision 2026-10-28: the offline results moved into Cases, the judges into Method).
 *
 * Context health and governance proposals live in the C&M overlay, not here.
 * "Let Swarm look" and "open the session" go through the overlay host's bridges
 * (OverlayRenderCtx.dispatchPrompt / resumeSession) and close the overlay.
 */
import { useCallback, useMemo, useState } from 'react';
import { chatService } from '../../services/chat';
import { useGoldenSet, useSessionQuality } from './api';
import { notPassing } from './derive';
import { CasesTab } from './CasesTab';
import { MethodTab } from './MethodTab';
import { OnlineTab } from './OnlineTab';
import { OverviewTab } from './OverviewTab';
import { EvalActionsContext, GoContext, rovingKey, useLang, type EvalActions, type TabId } from './ui';

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
  /** What a cross-link points at inside the tab it opens (an anchor id, or ``case:<id>``). */
  const [focus, setFocus] = useState<string | undefined>(undefined);
  const gs = useGoldenSet();
  const online = useSessionQuality();

  const go = useCallback((to: TabId, anchor?: string) => {
    setTab(to);
    setFocus(anchor);
    // The anchor's section may still be loading (Judges fetches its data on mount):
    // look for it on each frame for about a second, then stay at the top.
    const scroll = (framesLeft: number) => requestAnimationFrame(() => {
      if (anchor && !anchor.startsWith('case:')) {
        const target = document.getElementById(anchor);
        if (target) { target.scrollIntoView({ behavior: 'smooth' }); return; }
        if (framesLeft > 0) { scroll(framesLeft - 1); return; }
      }
      const el = document.getElementById('eval-scroll'); if (el) el.scrollTop = 0;
    });
    scroll(60);
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

  const failing = (gs.data?.cases ?? []).filter(notPassing).length;
  const low = online.data?.low_details?.length ?? 0;
  const tabs: { id: TabId; label: string; n?: number }[] = [
    { id: 'overview', label: t('Overview', '概览') },
    { id: 'cases', label: t('Cases', '考题'), n: failing },
    { id: 'online', label: t('Live sessions', '真实会话'), n: low },
    { id: 'method', label: t('Method', '方法') },
  ];

  return (
    <EvalActionsContext.Provider value={actions}>
      <GoContext.Provider value={go}>
        <div className="flex flex-col h-full" data-testid="eval-overlay">
          <div className="shrink-0 px-7 pt-3 border-b border-[var(--color-border)]">
            <nav className="flex gap-7" role="tablist" aria-label={t('OS Eval', 'OS Eval')}>
              {tabs.map((x) => (
                <button key={x.id} type="button" role="tab" aria-selected={tab === x.id} data-testid={`eval-tab-${x.id}`}
                        id={`eval-tab-${x.id}`} aria-controls="eval-scroll" tabIndex={tab === x.id ? 0 : -1}
                        onKeyDown={(e) => rovingKey(e, tabs.map((y) => y.id), tab, (id) => go(id))}
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
              <button type="button" aria-label={t('Dismiss', '关闭')} onClick={() => setNotice(null)} className="text-[var(--color-text-muted)]">✕</button>
            </div>
          )}
          <div id="eval-scroll" className="flex-1 min-h-0 overflow-y-auto outline-none" role="tabpanel" tabIndex={-1} aria-labelledby={`eval-tab-${tab}`}>
            {tab === 'overview' && <OverviewTab />}
            {tab === 'cases' && <CasesTab focus={focus} />}
            {tab === 'online' && <OnlineTab />}
            {tab === 'method' && <MethodTab />}
          </div>
        </div>
      </GoContext.Provider>
    </EvalActionsContext.Provider>
  );
}
