/**
 * AssistantMessageView — renders assistant messages with branded layout.
 *
 * This sub-component handles the full rendering of assistant-role messages:
 * - Branded header via AssistantHeader (🐝 SwarmAI · timestamp)
 * - Content from the ONE turn projection (`deriveTurnView`, pages/chat/turnView.ts).
 *   One `assistant-message` prose scope is shared by streaming notes and RESULT,
 *   so turn completion never swaps typography or list geometry. The content is
 *   laid out as the turn's zones (`turnZones`, pages/chat/turnZones.ts) and rendered
 *   by TurnZoneList (chat turn spec v6, Knowledge/Designs/2026-10-19-chat-turn-display-
 *   spec.md): THOUGHTS → restatement → WORKED → note → WORKED → … → RESULT; a pipeline
 *   turn keeps the work before its run above `Pipeline started · <profile>`, then one
 *   group per started stage. A direct run (profile `direct`) is a chat turn.
 * - Needs-you cards (question / permission / escalation, a final failure) in place
 * - The ending: RESULT → compact machine run status → immediate action → ONE output
 *   card → folded assumptions beside Next steps. Terminal errors append one local
 *   structured notice without replacing or recoloring the streamed turn above it.
 * - Streaming uses the established two-level status design: header pulse for the
 *   message state, and the tail bee for activity/rest; no third progress signal.
 * - min-w-0 overflow constraint on content (prevents flex overflow)
 * - Save-to-Memory button on the last assistant message (hover-to-reveal,
 *   next to the Copy button) with per-session status tracking
 * - Compact Context button on the last assistant message (hover-to-reveal,
 *   after Save-to-Memory) — conditionally visible when contextWarning
 *   level is 'warn' or 'critical', with urgency coloring at critical level
 *
 * Content and header share the flexible column beside the compact 24px avatar.
 *
 * @exports AssistantMessageView      — The view React component
 * @exports AssistantMessageViewProps  — Props interface
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8,
 *            3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
 */

import React, { useState, useCallback, useMemo, useEffect, useLayoutEffect, useRef } from 'react';
import clsx from 'clsx';
import type { Message, ContentBlock } from '../../../types';
import type { ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { ContentBlockRenderer } from './ContentBlockRenderer';
import { ChatErrorMessage } from '../../../components/chat';
import { AssumptionsDisclosure, isVerificationAttentionItem, OutputCard, ProductList, remoteProducts, RunLine } from './TurnCards';
import { NextSteps } from './NextSteps';
import { TurnZoneList, ZONE_GAP } from './TurnZones';
import type { ZoneContext } from './TurnZones';
import { deriveTurnView, extractNextSteps, hasTurnChanges } from '../turnView';
import type { TurnView } from '../turnView';
import { hasOpenQuestion, phaseSegments, stripView, turnZones } from '../turnZones';
import type { PhaseMember, Zone } from '../turnZones';
import { ANCHOR_PROPS } from '../scrollFocus';
import { FOCUS_AGENT_EVENT, focusAgent } from '../agentFocus';
import type { FocusAgentDetail } from '../agentFocus';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import { ASSISTANT_NAME } from '../assistantName';
import { ReadOnlyContext } from '../readOnlyContext';
import { AssistantHeader } from './AssistantHeader';
import { StreamingBee } from './StreamingBee';
import { useMemorySave } from '../../../hooks/useMemorySave';
import { chatService } from '../../../services/chat';
import type { MemorySaveStatus } from '../../../hooks/useMemorySave';
import { copyToClipboard } from '../../../utils/clipboard';
import { isOt01DiagEnabled } from '../../../utils/diagFlags';
import { useLinger } from '../../../hooks/useLinger';

/** A step's file path (absolute, or relative to wherever the tool ran) names the
 *  deliverable at `rel` / `abs`. */
/** The compact handoff at the top of a turn the CLI started because background
 *  agents finished. It shares the Agents panel's visual language without taking
 *  the result's visual weight. Its shape is stable from the first persisted block:
 *  no mount animation and no content-dependent header replacement. */
function BackgroundFinishedCard({ agents, sessionId }: {
  agents: NonNullable<TurnView['backgroundFinished']>;
  sessionId?: string;
}) {
  const { t } = useTranslation();
  const [openResults, setOpenResults] = useState<ReadonlySet<string>>(new Set());
  const fmtS = (ms?: number) => {
    if (ms === undefined) return '';
    const s = Math.round(ms / 1000);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  };
  const toggleResult = (key: string) => {
    setOpenResults((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  return (
    <div data-testid="background-finished" className="space-y-1.5">
      {agents.map((a, i) => {
        const key = a.toolUseId ?? `background-${i}`;
        const description = a.description || tf(t, 'chat.turn.bgAgent', 'Background agent');
        const how = a.status === 'completed' ? tf(t, 'chat.turn.bgFinished', 'finished')
          : a.status === 'failed' ? tf(t, 'chat.turn.bgFailed', 'failed')
            : tf(t, 'chat.turn.bgStopped', 'stopped');
        const mark = a.status === 'completed' ? '✓' : a.status === 'failed' ? '✕' : '■';
        const time = fmtS(a.durationMs);
        const meta = [how, time, a.toolUses ? tf(t, 'chat.turn.zone.tools', a.toolUses === 1 ? '{{count}} tool' : '{{count}} tools', { count: a.toolUses }) : '']
          .filter(Boolean).join(' · ');
        const result = a.summary?.trim() ?? '';
        const resultOpen = openResults.has(key);
        return (
          <section key={key} data-testid="background-finished-card"
            className="rounded-[10px] border border-purple-400/20 bg-purple-500/[0.04] px-2.5 py-1.5"
            {...ANCHOR_PROPS}>
            <div className="flex items-center gap-2 min-w-0">
              <span className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-purple-400">
                {tf(t, 'chat.turn.bgAgentLabel', 'Agent')}
              </span>
              <span aria-hidden="true" className="w-[12px] shrink-0 text-[12px] text-purple-400">{mark}</span>
              {a.toolUseId ? (
                <button type="button" data-testid="background-finished-origin"
                  onClick={() => focusAgent(a.toolUseId!, sessionId)}
                  title={tf(t, 'chat.turn.viewOriginalAgent', 'View original agent')}
                  aria-label={tf(t, 'chat.turn.viewOriginalAgentNamed', 'View original agent: {{name}}', { name: description })}
                  className="min-w-0 flex-1 truncate text-left text-[12.5px] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
                  <span aria-hidden="true" className="mr-1 text-[var(--color-text-dim)]">↖</span>{description}
                </button>
              ) : (
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--color-text-secondary)]">{description}</span>
              )}
              <span className="shrink-0 text-[12px] tabular-nums text-[var(--color-text-dim)]">{meta}</span>
            </div>
            {result && (
              <div className="ml-[52px] mt-1">
                <button type="button" data-testid="background-finished-result-toggle"
                  aria-expanded={resultOpen} onClick={() => toggleResult(key)}
                  className="flex items-center gap-1 rounded text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
                  <span aria-hidden="true">{resultOpen ? '▾' : '▸'}</span>
                  {tf(t, 'chat.turn.bgResult', 'Result')}
                </button>
                {resultOpen && (
                  <div data-testid="background-finished-result" className="mt-1 whitespace-pre-wrap break-words text-[12px] text-[var(--color-text-muted)]">
                    {result}
                  </div>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

const phaseMemberHasAgent = (member: PhaseMember, toolUseId: string): boolean =>
  member.kind === 'process' && member.steps.some((step) => step.block.id === toolUseId);

/** The fold keys that must be open before an Agent row can exist in the DOM. */
function agentFocusKeys(zones: Zone[], toolUseId: string): string[] {
  for (const zone of zones) {
    if (zone.kind === 'phase') {
      const { lead, segments } = phaseSegments(zone.members);
      if (lead.some((member) => phaseMemberHasAgent(member, toolUseId))) return [zone.id];
      const segment = segments.find((candidate) => candidate.members.some((member) => phaseMemberHasAgent(member, toolUseId)));
      if (segment) return [zone.id, `seg-tx-${segment.note.textOrdinal}`];
    }
    if (zone.kind === 'stage') {
      const nested = agentFocusKeys(zone.body, toolUseId);
      if (nested.length) return [zone.id, ...nested];
    }
  }
  return [];
}

function isSameFile(p: string, rel: string, abs: string): boolean {
  const q = p.replace(/^\.\//, '');
  return q === abs || q === rel || abs.endsWith(`/${q}`);
}

export interface AssistantMessageViewProps {
  /** The assistant message to render */
  message: Message;
  /** Its list key (TabView `bubbleKeys`), unique per row and stable across the
   *  reconcile: scopes the view memory (durations, merges). */
  viewKey?: string;
  /** Callback when the user answers an ask_user_question block */
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** Callback when the user approves/denies a permission request */
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  /** Callback when user clicks an escalation option — sends choice as chat message */
  onEscalationSelect?: (escalationId: string, optionLabel: string) => void;
  /** The tool_use ID currently awaiting a user answer */
  pendingToolUseId?: string;
  /** The request ID of the currently pending permission */
  pendingPermissionRequestId?: string;
  /** Whether the assistant message is still streaming */
  isStreaming?: boolean;
  /** The current session ID for the save API call */
  sessionId?: string;
  /** Whether this is the last assistant message in the session */
  isLastAssistant?: boolean;
  /** A steer continued the turn after this segment (pages/chat/steerChain): it is
   *  not the turn's end -- no RESULT, no ending cards, no next steps. */
  continued?: boolean;
  /** This segment goes on after a read steer: no avatar, no header. */
  afterSteer?: boolean;
  /** The chain's first segment while a later one streams: the header keeps its pulse. */
  headerLive?: boolean;
  /** Context warning from the backend context monitor (per-session, display mirror) */
  contextWarning?: ContextWarning | null;
  /** Callback to send a continuation prompt when model stopped prematurely */
  onContinue?: () => void;
  /** Callback for a terminal error whose existing action can be retried. */
  onRetryError?: () => void;
  /** Read-only render (History preview) — forces interactive blocks inert. */
  readOnly?: boolean;
}

/** Map of memory save status to Material Symbols icon names. */
const MEMORY_ICON_MAP: Record<MemorySaveStatus, string> = {
  idle: 'neurology',
  loading: 'progress_activity',
  saved: 'check_circle',
  empty: 'neurology',
  error: 'error',
};

export const AssistantMessageView: React.FC<AssistantMessageViewProps> = ({
  message,
  viewKey,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  sessionId,
  isLastAssistant,
  continued = false,
  afterSteer = false,
  headerLive = false,
  contextWarning,
  onContinue,
  onRetryError,
  readOnly,
}) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  // The user's open/closed CHOICE per zone (turnZones ids: `thinking`, `ph-…`,
  // `stage-<name>`) and thought (`th-<ordinal>`), keyed by ids that are stable while the turn
  // streams AND across the stream-end reconcile that drops live-only tool_result
  // blocks. Lifted here so it survives remounts. No entry = automatic.
  const [processChoice, setProcessChoice] = useState<ReadonlyMap<string, boolean>>(new Map());
  const [focusedAgentId, setFocusedAgentId] = useState<string>();
  const messageRef = useRef<HTMLDivElement | null>(null);
  // True while streaming and for FOLD_DELAY_MS after it (the work keeps its layout).
  // The turn's ONE ending clock: when it turns false, the WORKING fold, the RESULT
  // and every ending card land in the same frame, instead of one jump per timer.
  const settling = useLinger(!!isStreaming);
  const ended = !settling;
  const chooseProcess = useCallback((id: string, open: boolean) => {
    setProcessChoice((prev) => new Map(prev).set(id, open));
  }, []);
  // Result of this message's own actions (Compact / Save), shown under the
  // message — it belongs to this chat, not to the app-wide toast stack.
  const [actionNote, setActionNote] = useState<{ severity: 'success' | 'error' | 'info'; text: string } | null>(null);
  const noteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showNote = useCallback((severity: 'success' | 'error' | 'info', text: string) => {
    if (noteTimerRef.current) clearTimeout(noteTimerRef.current);
    setActionNote({ severity, text });
    noteTimerRef.current = setTimeout(() => setActionNote(null), 4000);
  }, []);
  useEffect(() => () => { if (noteTimerRef.current) clearTimeout(noteTimerRef.current); }, []);

  // Per-session memory save state
  const { statusMap, toastMap, save: saveMemory, reset: resetMemory } = useMemorySave();
  const memorySaveStatus: MemorySaveStatus = sessionId ? (statusMap[sessionId] || 'idle') : 'idle';
  const memoryToastMessage = sessionId ? (toastMap[sessionId] || null) : null;

  // Compact button local state
  const [compactStatus, setCompactStatus] = useState<'idle' | 'loading' | 'done'>('idle');

  const handleCompact = useCallback(async () => {
    if (!sessionId || compactStatus === 'loading') return;
    setCompactStatus('loading');
    try {
      const result = await chatService.compactSession(sessionId);
      setCompactStatus('done');
      showNote('success', result.status === 'compacted'
        ? tf(t, 'chat.message.compactDone', 'Context compacted successfully')
        : result.message);
      setTimeout(() => setCompactStatus('idle'), 3000);
    } catch {
      setCompactStatus('idle');
      showNote('error', tf(t, 'chat.message.compactFailed', 'Failed to compact session'));
    }
  }, [sessionId, compactStatus, showNote, t]);

  // Error handlers append one final text block to the assistant message. Keep that
  // terminal notice out of the turn projection so it cannot become RESULT or change
  // the process grouping; the structured error card renders it after the preserved turn.
  const { turnContent, terminalErrorText } = useMemo(() => {
    if (!message.isError) return { turnContent: message.content, terminalErrorText: undefined };
    const last = message.content[message.content.length - 1];
    if (last?.type !== 'text') return { turnContent: message.content, terminalErrorText: undefined };
    return { turnContent: message.content.slice(0, -1), terminalErrorText: last.text };
  }, [message.content, message.isError]);

  // The ONE display projection of this turn (pages/chat/turnView.ts): ordered
  // units (process segments / landmarks / outcome / interrupts), the single
  // done rule, the lifted next-steps block and what Copy copies.
  const view = useMemo(
    () => deriveTurnView(turnContent, !!isStreaming, message.subAgentProgress),
    [turnContent, isStreaming, message.subAgentProgress],
  );

  /** All non-error text the message carries — gates the action row exactly as before
   *  the projection. The terminal notice is never copied as part of the answer. */
  const allText = useMemo(
    () => turnContent
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('\n'),
    [turnContent],
  );
  /** Copy copies the answer (outcome text) — not process notes, not next-steps;
   *  a turn with no answer falls back to all of its text (still without a
   *  trailing next-steps block). */
  const extractMessageText = useCallback(
    (): string => view.copyText || extractNextSteps(allText, false).text,
    [view, allText],
  );

  const handleCopy = useCallback(() => {
    copyToClipboard(extractMessageText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [extractMessageText]);

  const handleSaveMemory = useCallback(() => {
    if (sessionId && memorySaveStatus !== 'loading') {
      saveMemory(sessionId);
    }
  }, [sessionId, memorySaveStatus, saveMemory]);

  // Show the memory-save result under this message when it completes.
  useEffect(() => {
    if (!isLastAssistant || !sessionId || !memoryToastMessage) return;
    const severity = memorySaveStatus === 'saved' ? 'success' : memorySaveStatus === 'error' ? 'error' : 'info';
    showNote(severity, memoryToastMessage);
    resetMemory(sessionId);
  }, [memoryToastMessage, isLastAssistant, sessionId, memorySaveStatus, showNote, resetMemory]);

  // Compact button visibility: only on last assistant, not streaming, with active warning
  const showCompactButton = isLastAssistant
    && ended
    && sessionId
    && contextWarning
    && (contextWarning.level === 'warn' || contextWarning.level === 'critical');

  // ── OT01 render-loss diagnostic (opt-in via isOt01DiagEnabled; run_3451bbd1) — RENDER side ─
  // Gated on isOt01DiagEnabled() (DEV, or localStorage SWARM_OT01_DIAG=1 in
  // prod) — NOT import.meta.env.DEV, which is tree-shaken dead in the prod .app
  // where the bug actually happens (run_3451bbd1).
  // The RENDER half of the store-vs-render pair (store half: [OT01-diag]
  // store→React sync in useChatStreamingLifecycle). Logs the block-count this
  // view ACTUALLY renders for this msgId. In the live console, line up the two
  // by msgId: storeTextBlocks > renderedTextBlocks ⇒ content reached the store
  // but the view rendered fewer ⇒ loss is in the React prop/render path (the
  // single-tab layer headless tests can't expose). Equal counts ⇒ the loss is
  // NOT here (look at MarkdownRenderer/CSS/viewport). Removed once located.
  if (isOt01DiagEnabled()) {
    console.debug('[OT01-diag] render', {
      msgId: message.id,
      renderedBlocks: message.content.length,
      renderedTextBlocks: message.content.filter((b) => b.type === 'text').length,
      isStreaming: !!isStreaming,
    });
  }

  // Render ONE block via ContentBlockRenderer (markdown text + the interactive
  // interrupt widgets). Tool calls never come through here — they are light strips in
  // a phase group (TurnZones), which takes its status from the projection.
  const renderBlock = (block: ContentBlock, key: string) => (
    <ContentBlockRenderer
      key={key}
      block={block}
      onAnswerQuestion={onAnswerQuestion}
      onPermissionDecision={onPermissionDecision}
      onEscalationSelect={onEscalationSelect}
      pendingToolUseId={pendingToolUseId}
      pendingPermissionRequestId={pendingPermissionRequestId}
      isStreaming={isStreaming}
      readOnly={readOnly}
    />
  );

  // ── The turn's zones (chat turn spec v6) ──────────────────────────────────────
  // turnZones decides the first level (THOUGHTS · restatement · WORKED · note · … ·
  // pipeline · RESULT); TurnZoneList renders it with the two spacing steps. Every zone
  // keys by an id that only grows while streaming and survives the reconcile.
  const zones = useMemo(() => turnZones(view, !!isStreaming, settling, continued), [view, isStreaming, settling, continued]);

  useEffect(() => {
    const onFocusAgent = (event: Event) => {
      const detail = (event as CustomEvent<FocusAgentDetail>).detail;
      if (!detail?.toolUseId || (detail.sessionId && sessionId && detail.sessionId !== sessionId)) return;
      const keys = agentFocusKeys(zones, detail.toolUseId);
      if (!keys.length) return;
      setFocusedAgentId(detail.toolUseId);
      setProcessChoice((prev) => {
        const next = new Map(prev);
        keys.forEach((key) => next.set(key, true));
        return next;
      });
    };
    window.addEventListener(FOCUS_AGENT_EVENT, onFocusAgent);
    return () => window.removeEventListener(FOCUS_AGENT_EVENT, onFocusAgent);
  }, [zones, sessionId]);

  useLayoutEffect(() => {
    if (!focusedAgentId) return;
    const row = [...(messageRef.current?.querySelectorAll<HTMLElement>('[data-agent-id]') ?? [])]
      .find((candidate) => candidate.dataset.agentId === focusedAgentId);
    if (row) {
      // Every fold, including the Agents panel and row, is open in this commit.
      // Scroll before paint; release the temporary focus on the next frame after
      // their layout effects have persisted that open state.
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    const raf = requestAnimationFrame(() => setFocusedAgentId(undefined));
    return () => cancelAnimationFrame(raf);
  }, [focusedAgentId]);

  // The view memory's key must survive the stream-end reconcile, which swaps the live
  // bubble's client id for the DB row's id; the row carries the client id in its
  // metadata, so the client id is the key on both sides. Two rows sharing a client id
  // (a continued turn) get different list keys, so the list key — when given — wins.
  const memoryScope = viewKey ?? message.metadata?.client_id ?? message.id;
  const renderNeeds = (z: Extract<Zone, { kind: 'needs' | 'failure' }>): React.ReactNode => {
    if (z.kind === 'needs') return renderBlock(z.block, `${z.id}-b`);
    const v = stripView(z.step.block);
    return (
      <section data-testid="needs-failure" className="rounded-lg border border-[var(--color-border)] px-3 py-2">
        {/* attention 'know' (attention.ts): nothing to act on here, so no act colour
            and no "Needs you" (fixing spec H1) */}
        <div data-testid="needs-failure-label" className="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">{tf(t, 'chat.turn.zone.failed', 'Failed')}</div>
        <div className="text-[14px] text-[var(--color-text-secondary)]">
          {tf(t, 'chat.turn.zone.failedCall', 'This call failed and the turn ended there:')}{' '}
          <span className="font-mono text-[12.5px]">{v.tool} {v.object}</span>
        </div>
        {z.step.result?.outcome && <div className="mt-1 font-mono text-[12px] text-[var(--color-text-muted)] whitespace-pre-wrap break-words">{z.step.result.outcome}</div>}
      </section>
    );
  };
  const ctx: ZoneContext = {
    scope: memoryScope,
    isStreaming: !!isStreaming,
    settling,
    sessionId,
    readOnly,
    choice: (id) => processChoice.get(id),
    choose: chooseProcess,
    focusedAgentId,
    renderText: (text, key) => <div className="text-[14px]">{renderBlock({ type: 'text', text }, key)}</div>,
    renderNeeds,
    renderProducts: (z) => <ProductList blocks={z.blocks} />,
  };
  // The ending (spec §8): one output card. Code changes come only from turn_changes;
  // a deliverable is not listed again as a changed file; a pipeline run's documents are
  // in the deliverables section only. A direct run has its summary too (XG 2026-10-20).
  // the turn's end (chat-steer: not in a segment a steer continued)
  const ending = ended && !continued;
  const summary = ending ? view.summary : undefined;
  const summaryDocs = summary?.docs ?? [];
  // A document already shown as a card on its stage row is not a deliverable tile too.
  const stageDocPaths = new Set(turnContent.flatMap((b) => (b.type === 'pipeline_stage'
    ? [...(b.docs ?? []), ...(b.doc ? [b.doc] : [])].map((d) => d.absolutePath) : [])));
  const deliverables = view.deliverables.filter((d) => !stageDocPaths.has(d.absolutePath)
    && !summaryDocs.some((x) => x.absolutePath === d.absolutePath));
  // The turn's remote products (session-products design §3.2): on the card beside the files.
  const remote = remoteProducts(view.products);
  const changes = useMemo(() => {
    if (!ending || !view.changes) return undefined;
    const files = view.changes.files.filter(
      (f) => !view.deliverables.some((d) => isSameFile(f.absolutePath, d.path, d.absolutePath))
        && !summaryDocs.some((x) => !!x.absolutePath && isSameFile(f.absolutePath, x.path ?? '', x.absolutePath)),
    );
    const next = { ...view.changes, files };
    return hasTurnChanges(next) ? next : undefined;
  }, [view, ending, summaryDocs]);
  const attention = !continued && view.attention && view.attention.items.length > 0 ? view.attention : undefined;
  const hasVerificationAttention = !!attention
    && (attention.items.some(isVerificationAttentionItem) || (attention.more ?? 0) > 0);
  const hasResult = zones.some((z) => z.kind === 'text' && z.result);
  const contentBlocks: React.ReactNode[] = [];
  // A turn the CLI started to answer background agents that finished: say which, first.
  // The card carries its own gap: wrapping the zone list only when it exists would
  // swap the list's parent and remount the whole turn when a late card arrives.
  if (view.backgroundFinished?.length) {
    contentBlocks.push(
      <div key="bg-finished" className="mb-2">
        <BackgroundFinishedCard agents={view.backgroundFinished} sessionId={sessionId} />
      </div>,
    );
  }
  contentBlocks.push(<TurnZoneList key="zones" zones={zones} ctx={ctx} hasOpenQuestionIn={(z) => hasOpenQuestion(z.body)} />);
  // Completion hierarchy: model Result, the run's grey line (no run-result and no
  // attention card: design 2026-10-26-turn-cards-unified.md §5), produced output,
  // then optional assumptions beside the proposed next steps.
  if (summary) {
    contentBlocks.push(
      <div key="result" className={zones.length ? ZONE_GAP : ''}>
        <RunLine summary={summary} />
      </div>,
    );
  }
  if (ending && (changes || deliverables.length || summaryDocs.length || remote.length)) {
    contentBlocks.push(
      <div key="output" className={hasResult ? 'mt-5' : ZONE_GAP}>
        <OutputCard changes={changes} deliverables={deliverables} summary={summary}
          remote={remote} omitted={view.deliverablesOmitted} />
      </div>,
    );
  }
  if (ending && attention && hasVerificationAttention) {
    contentBlocks.push(
      <div key="assumptions" className={ZONE_GAP}>
        <AssumptionsDisclosure block={attention} />
      </div>,
    );
  }
  if (message.isError) {
    const errorCode = message.errorCode;
    contentBlocks.push(
      <div key="terminal-error" className={contentBlocks.length ? ZONE_GAP : ''}>
        <ChatErrorMessage
          error={{
            code: errorCode,
            message: terminalErrorText ?? tf(t, 'chat.list.errorFallback', 'An error occurred'),
            detail: message.errorDetail,
            suggestedAction: message.suggestedAction,
            retryAfter: (message as Message & { retryAfter?: number }).retryAfter,
          }}
          onRetry={errorCode === 'QUEUE_TIMEOUT' ? onRetryError : undefined}
        />
      </div>,
    );
  }

  const copiedLabel = tf(t, 'chat.message.copied', 'Copied!');
  const saveTitle = tf(t, 'chat.message.saveTitle', 'Save to Memory');
  const savedLabel = tf(t, 'chat.message.saved', 'Saved!');
  const savingLabel = tf(t, 'chat.message.saving', 'Saving...');

  return (
    <ReadOnlyContext.Provider value={!!readOnly}>
    <div ref={messageRef} className="assistant-message group/msg min-w-0 flex gap-2.5 items-start">
      {afterSteer ? (
        /* the reply goes on under the steer: same column, no avatar (chat-steer §7) */
        <div className="w-6 flex-shrink-0" aria-hidden="true" />
      ) : (
        /* Agent avatar — 24px bronze gradient circle matching mockup & nav logo */
        <div
          className="w-6 h-6 rounded-[6px] flex items-center justify-center flex-shrink-0 mt-0.5 text-[13px]"
          style={{
            background: 'linear-gradient(180deg, #d4a537, #a67c20)',
            boxShadow: '0 1px 2px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.15)',
          }}
          aria-hidden="true"
        >
          <span role="img" aria-label={ASSISTANT_NAME}>&#x1F41D;</span>
        </div>
      )}
      <div className="flex-1 min-w-0">
        {!afterSteer && (
          <AssistantHeader
            timestamp={message.timestamp}
            isStreaming={isStreaming || headerLive}
          />
        )}

      <div className="text-[var(--color-text-secondary)]">
        {contentBlocks}
        {/* Streaming indicator — the bee at the end of the streaming content
            (it flies while the turn works; after 45s quiet a line says what it waits on). */}
        {isStreaming && <StreamingBee content={message.content} sessionId={sessionId} />}
        {/* A running sub-agent shows in its Agents panel row (TurnZones,
            fed by subagent_progress) — no separate timer banner. */}
      </div>

      {/* Next steps — the reply's trailing ```next-steps block as clickable chips.
          Only on the latest successful answer (older or interrupted suggestions are stale). */}
      {!readOnly && ending && !message.isError && isLastAssistant && <NextSteps steps={view.nextSteps} />}

      {/* Action buttons — appear on hover below content, hidden while streaming.
          Hidden entirely in read-only (History preview) — Copy/Save/Compact/
          Continue are all side-effecting controls that don't belong there. */}
      {!readOnly && ended && allText.length > 0 && (
        <div className="opacity-0 group-hover/msg:opacity-100 transition-opacity mt-1 flex items-center gap-2">
          {/* Copy button (unchanged) */}
          <button
            type="button"
            onClick={handleCopy}
            className="btn-sm btn-ghost transition-colors"
            title={copied ? copiedLabel : tf(t, 'chat.message.copyTitle', 'Copy message')}
          >
            <span className="material-symbols-outlined icon-xs">
              {copied ? 'check' : 'content_copy'}
            </span>
            {copied ? copiedLabel : tf(t, 'chat.message.copy', 'Copy')}
          </button>

          {/* Save to Memory button — only on last assistant message */}
          {isLastAssistant && sessionId && (
            <button
              type="button"
              onClick={handleSaveMemory}
              disabled={memorySaveStatus === 'loading'}
              className={clsx(
                'btn-sm transition-colors hover:bg-[var(--color-hover)]',
                memorySaveStatus === 'saved'
                  ? 'text-green-500 hover:text-green-400'
                  : memorySaveStatus === 'error'
                    ? 'text-red-500 hover:text-red-400'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                memorySaveStatus === 'loading' && 'opacity-50 cursor-not-allowed'
              )}
              title={memorySaveStatus === 'saved' ? savedLabel : memorySaveStatus === 'loading' ? savingLabel : saveTitle}
              aria-label={saveTitle}
            >
              <span className={clsx(
                'material-symbols-outlined icon-xs',
                memorySaveStatus === 'loading' && 'animate-spin'
              )}>
                {MEMORY_ICON_MAP[memorySaveStatus]}
              </span>
              {memorySaveStatus === 'saved' ? savedLabel : memorySaveStatus === 'loading' ? savingLabel : tf(t, 'chat.message.save', 'Save')}
            </button>
          )}

          {/* Compact Context button — only when context warning is active */}
          {showCompactButton && (
            <button
              type="button"
              onClick={handleCompact}
              disabled={compactStatus === 'loading'}
              className={clsx(
                'btn-sm transition-colors hover:bg-[var(--color-hover)]',
                contextWarning.level === 'critical'
                  ? 'text-red-500 hover:text-red-400'
                  : compactStatus === 'done'
                    ? 'text-green-500 hover:text-green-400'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                compactStatus === 'loading' && 'opacity-50 cursor-not-allowed'
              )}
              title={tf(t, 'chat.message.compactTitle', 'Compact Context ({{pct}}% used)', { pct: contextWarning.pct ?? '' })}
              aria-label={tf(t, 'chat.message.compactAria', 'Compact Context')}
            >
              <span className={clsx(
                'material-symbols-outlined icon-xs',
                compactStatus === 'loading' && 'animate-spin'
              )}>
                {compactStatus === 'loading' ? 'progress_activity'
                  : compactStatus === 'done' ? 'check_circle'
                  : 'compress'}
              </span>
              {compactStatus === 'loading' ? tf(t, 'chat.message.compacting', 'Compacting...')
                : compactStatus === 'done' ? tf(t, 'chat.message.compacted', 'Compacted!')
                : tf(t, 'chat.message.compact', 'Compact')}
            </button>
          )}

          {/* Continue button — allows user to request continuation when model stopped prematurely.
              Only shown on the last assistant message when not an error. */}
          {isLastAssistant && onContinue && !message.isError && (
            <button
              type="button"
              onClick={onContinue}
              className="btn-sm btn-ghost transition-colors"
              title={tf(t, 'chat.message.continueTitle', 'Continue — ask {{name}} to continue its response', { name: ASSISTANT_NAME })}
              aria-label={tf(t, 'chat.message.continueAria', 'Continue response')}
              data-testid="continue-button"
            >
              <span className="material-symbols-outlined icon-xs">
                play_arrow
              </span>
              {tf(t, 'chat.message.continue', 'Continue')}
            </button>
          )}
        </div>
      )}

      {/* Result of this message's Compact / Save action (always visible, not hover-gated). */}
      {actionNote && (
        <div
          role={actionNote.severity === 'error' ? 'alert' : 'status'}
          data-testid="message-action-note"
          className={clsx(
            'mt-1 text-xs',
            actionNote.severity === 'error' ? 'text-[var(--color-error,#ef4444)]'
              : actionNote.severity === 'success' ? 'text-[var(--color-success,#22c55e)]'
              : 'text-[var(--color-text-muted)]',
          )}
        >
          {actionNote.text}
        </div>
      )}

    </div>
    </div>
    </ReadOnlyContext.Provider>
  );
};

export default AssistantMessageView;
