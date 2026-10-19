/**
 * AssistantMessageView — renders assistant messages with branded layout.
 *
 * This sub-component handles the full rendering of assistant-role messages:
 * - Branded header via AssistantHeader (🐝 SwarmAI · timestamp)
 * - Content rendered from the ONE turn projection (`deriveTurnView`,
 *   pages/chat/turnView.ts) in two levels (chat display v8). FIRST level: the
 *   restatement (the text written before any work — plain text, no label; none →
 *   nothing), WORKING / WORKED blocks (`groupWork`: everything between two
 *   first-level items), the pipeline block, interrupts (question / permission /
 *   escalation) and the RESULT. SECOND level, inside a block or an opened stage
 *   line, in stream order: thoughts, notes, landmarks and tool rows (verb + object)
 * - The process lane: everything that is process (blocks, the pipeline block) sits
 *   behind one left rule, a size smaller and dimmer; the answer, the restatement,
 *   interrupts and cards sit outside it at full size
 * - A pipeline turn is ONE PipelineBlock: its lines are the run's checklist
 *   (`pipelineChecklist` over `sectionUnits`, the stage rows the backend writes from
 *   run.json), each a PipelineStageRow; a stage that ran again is the same line,
 *   its work split by round. The run's stored requirement is never shown
 * - Thoughts written before the restatement stay where they streamed until the turn
 *   has ended and settled (`settling`, FOLD_DELAY_MS), then join the work as it folds
 * - Backend cards (TurnCards): "needs your look" right after the Result divider (with
 *   the DELIVER line while the run is going); the turn's products after the outcome
 * - Next-step chips (NextSteps) from a trailing ```next-steps block, last
 *   assistant message only; Copy copies the answer, not the process
 * - Red border error wrapper when message.isError is true
 * - min-w-0 overflow constraint on content (prevents flex overflow)
 * - Save-to-Memory button on the last assistant message (hover-to-reveal,
 *   next to the Copy button) with per-session status tracking
 * - Compact Context button on the last assistant message (hover-to-reveal,
 *   after Save-to-Memory) — conditionally visible when contextWarning
 *   level is 'warn' or 'critical', with urgency coloring at critical level
 *
 * Key layout change: content starts at the left margin directly below the
 * header line — no avatar column or indentation gap.
 *
 * @exports AssistantMessageView      — The view React component
 * @exports AssistantMessageViewProps  — Props interface
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8,
 *            3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import clsx from 'clsx';
import type { Message, ContentBlock } from '../../../types';
import type { ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { ContentBlockRenderer } from './ContentBlockRenderer';
import { WorkGroupView } from './WorkGroup';
import { PipelineStageRow } from './PipelineStageRow';
import { PipelineBlock } from './PipelineBlock';
import { FileChip } from './FileChip';
import { AttentionCard, DeliverablesCard, PipelineSummaryCard } from './TurnCards';
import { ChangesCard } from './ChangesCard';
import { NextSteps } from './NextSteps';
import { deriveTurnView, extractNextSteps, groupWork, isGateStage, isUnanswered, pipelineChecklist, sectionUnits, stageCounts, turnChangedFiles } from '../turnView';
import type { NoteUnit, ProcessUnit, TurnUnit, WorkItem } from '../turnView';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import { ASSISTANT_NAME } from '../assistantName';
import { ReadOnlyContext } from '../readOnlyContext';
import { ANCHOR_PROPS } from '../scrollFocus';
import { AssistantHeader } from './AssistantHeader';
import { StreamingBee } from './StreamingBee';
import { useMemorySave } from '../../../hooks/useMemorySave';
import { chatService } from '../../../services/chat';
import type { MemorySaveStatus } from '../../../hooks/useMemorySave';
import { useToast } from '../../../contexts/ToastContext';
import { copyToClipboard } from '../../../utils/clipboard';
import { isOt01DiagEnabled } from '../../../utils/diagFlags';
import { useLinger } from '../../../hooks/useLinger';

/** A step's file path (absolute, or relative to wherever the tool ran) names the
 *  deliverable at `rel` / `abs`. */
function isSameFile(p: string, rel: string, abs: string): boolean {
  const q = p.replace(/^\.\//, '');
  return q === abs || q === rel || abs.endsWith(`/${q}`);
}

/** Process content: one left rule, a size smaller and dimmer than the answer. */
const LANE_CLASS = 'flow-root pl-3 border-l-2 border-[var(--color-border)] text-[12.5px] text-[var(--color-text-muted)]';
/** The answer's size and contrast, restored for a non-process item inside a stage. */
const FULL_CLASS = 'text-[15px] text-[var(--color-text-secondary)]';

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
  /** Streaming but no new content for a while (TabView's useStreamingRest) —
   *  the streaming bee lands and shows the "mulling it over" line. */
  isResting?: boolean;
  /** The current session ID for the save API call */
  sessionId?: string;
  /** Whether this is the last assistant message in the session */
  isLastAssistant?: boolean;
  /** Context warning from the backend context monitor (per-session, display mirror) */
  contextWarning?: ContextWarning | null;
  /** Callback to send a continuation prompt when model stopped prematurely */
  onContinue?: () => void;
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
  isResting,
  sessionId,
  isLastAssistant,
  contextWarning,
  onContinue,
  readOnly,
}) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  // The user's open/closed CHOICE per work group (`grp-<first member key>`), thought
  // (`th-<ordinal>`) and stage section, keyed by ids that are stable while the turn
  // streams AND across the stream-end reconcile that drops live-only tool_result
  // blocks. Lifted here so it survives remounts. No entry = automatic.
  const [processChoice, setProcessChoice] = useState<ReadonlyMap<string, boolean>>(new Map());
  // True for FOLD_DELAY_MS after the turn stops streaming (the work keeps its layout).
  const settling = useLinger(!!isStreaming);
  const chooseProcess = useCallback((id: string, open: boolean) => {
    setProcessChoice((prev) => new Map(prev).set(id, open));
  }, []);
  const { addToast } = useToast();

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
      addToast({
        severity: 'success',
        message: result.status === 'compacted'
          ? tf(t, 'chat.message.compactDone', 'Context compacted successfully')
          : result.message,
        autoDismiss: true,
      });
      setTimeout(() => setCompactStatus('idle'), 3000);
    } catch {
      setCompactStatus('idle');
      addToast({
        severity: 'error',
        message: tf(t, 'chat.message.compactFailed', 'Failed to compact session'),
        autoDismiss: true,
      });
    }
  }, [sessionId, compactStatus, addToast, t]);

  // The ONE display projection of this turn (pages/chat/turnView.ts): ordered
  // units (process segments / landmarks / outcome / interrupts), the single
  // done rule, the lifted next-steps block and what Copy copies.
  const view = useMemo(
    () => deriveTurnView(message.content, !!isStreaming, message.subAgentProgress),
    [message.content, isStreaming, message.subAgentProgress],
  );

  /** All text the message carries — gates the action row exactly as before the
   *  projection (a turn that ended mid-process, with no answer yet, still gets
   *  Continue / Save). */
  const allText = useMemo(
    () => message.content
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('\n'),
    [message.content],
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

  // Fire toast when memory save completes (replaces inline <Toast> JSX)
  useEffect(() => {
    if (!isLastAssistant || !sessionId || !memoryToastMessage) return;
    const severity = memorySaveStatus === 'saved' ? 'success' : memorySaveStatus === 'error' ? 'error' : 'info';
    addToast({ severity, message: memoryToastMessage, autoDismiss: true, durationMs: 4000 });
    resetMemory(sessionId);
  }, [memoryToastMessage, isLastAssistant, sessionId, memorySaveStatus, addToast, resetMemory]);

  // Compact button visibility: only on last assistant, not streaming, with active warning
  const showCompactButton = isLastAssistant
    && !isStreaming
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
  // interrupt widgets). Tool calls never come through here — they are strips in a
  // WorkGroup, which takes its status from the projection.
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

  // ── Attention layers (design-judgment Surface 6; chat display v8) ────────────
  // Process (groups, notes, landmarks, the pipeline block) sits in one lane behind a
  // left rule, a size smaller and dimmer; the restatement, the result, interrupts and
  // cards sit outside it at full size. Within the lane, spacing groups, never boxes.
  const hasWork = view.units.some((u) => u.kind === 'process' && u.steps.length > 0);
  const firstWork = view.units.findIndex((u) => u.kind === 'process' && u.steps.length > 0);
  const lastUnit = view.units[view.units.length - 1];
  const showResult = !isStreaming && hasWork && lastUnit?.kind === 'outcome';
  const isPipeline = view.units.some((u) => u.kind === 'stage');
  // The backend's code changes (turn_changes) are the card. A product the turn wrote
  // shows in the Produced card, not again as a changed file. Tool-derived names the
  // card does not cover (older history; a file outside the code repos) stay chips.
  const changes = useMemo(() => {
    if (isStreaming || !view.changes) return undefined;
    const files = view.changes.files.filter(
      (f) => !view.deliverables.some((d) => isSameFile(f.absolutePath, d.path, d.absolutePath)),
    );
    return files.length ? { ...view.changes, files } : undefined;
  }, [view, isStreaming]);
  const turnFiles = useMemo(
    () => (!isStreaming && hasWork && !isPipeline ? turnChangedFiles(view.units) : [])
      .filter((p) => !view.deliverables.some((d) => isSameFile(p, d.path, d.absolutePath)))
      .filter((p) => !view.changes?.files.some((f) => isSameFile(p, f.path, f.absolutePath))),
    [view, isStreaming, hasWork, isPipeline],
  );
  const attentionCard = view.attention && view.attention.items.length > 0
    ? <AttentionCard block={view.attention} />
    : null;
  // The opening restatement — how the ask was read — is plain text at the top (no
  // label, chat display v8): the text written before any work, in a chat turn and in
  // a pipeline turn alike. No restatement → no lead item: never a thought, never the
  // run's requirement. A turn that starts with a tool result is the tail of a
  // compacted / resumed one: nothing in it is how the ask was read.
  const { midWork } = view;
  const beforeWork = firstWork < 0 ? [] : view.units.slice(0, firstWork);
  const alignText = hasWork && !midWork
    ? beforeWork.find((u) => u.kind === 'note')
    : undefined;
  // The thoughts before the restatement join the work they led to — once the turn has
  // ended AND settled (FOLD_DELAY_MS), so they move in the same moment the work folds,
  // never under the reader while it is still open.
  const mergeLead = !isStreaming && !settling;
  // The thoughts before the restatement: once they move into the first stage (a
  // pipeline turn, after the settle) they do not change the id of the block they join
  // (groupWork `joined`, stage bodies only — the top level keeps ids by assigning them
  // before its own lead merge).
  // Exactly the ones sectionUnits / mergeLead move: thoughts before the first call or
  // stage row (not a thought inside a stage that has no call yet).
  const leadEnd = view.units.findIndex((u) => u.kind === 'process' || u.kind === 'stage');
  // The view memory's key must survive the stream-end reconcile, which swaps the live
  // bubble's client id for the DB row's id; the row carries the client id in its
  // metadata, so the client id is the key on both sides.
  // Two rows sharing a client id (a continued turn) get different list keys, so the
  // list key — when the list gives one — keeps their memories apart.
  const memoryScope = viewKey ?? message.metadata?.client_id ?? message.id;
  const leadThoughts = new Set((leadEnd < 0 ? [] : view.units.slice(0, leadEnd)).filter((u) => u.kind === 'thought'));
  // While the answer-so-far streams, the block of work above it stays open (groupWork
  // `holdLiveAboveAnswer`): it does not fold and re-open if a tool call follows.
  const holdLiveAboveAnswer = !!isStreaming;
  // A pipeline turn: the leading restatement stays at the top; everything else is in
  // the ONE pipeline block (thoughts before the first stage go into it).
  const sections = sectionUnits(view.units, !!isStreaming, view.liveStage, { keepLeadText: true, keepLeadThoughts: !mergeLead });
  // The live / where-it-stopped section is the last STAGE section: a gate row holds no
  // work (GATE 0 sits after EVALUATE, whose section is still collecting work).
  const isGateSec = (s: (typeof sections)[number]) => s.kind === 'stage' && isGateStage(s.block.stage);
  const lastSection = sections.reduce((at, s, k) => (s.kind === 'unit' || isGateSec(s) ? at : k), -1);
  const onlyTailAfter = (k: number) => sections.slice(k + 1).every((s) => isGateSec(s) || (s.kind === 'unit' && s.unit.kind === 'outcome'));
  // A finished turn with no result would show nothing but folded blocks: its last
  // note (not the restatement) shows under the folded line of the block holding it; with
  // no such note, the turn's last block starts open. Both are chosen among the units
  // at TOP level only (outside every stage section): a note folded inside a stage row
  // would not be visible, so it never counts as the turn's last words.
  const noResult = !isStreaming && hasWork && lastUnit?.kind !== 'outcome';
  const topUnits = noResult ? sections.flatMap((sec) => (sec.kind === 'unit' ? [sec.unit] : [])) : [];
  let tailNote: NoteUnit | undefined;
  let lastWork: ProcessUnit | undefined;
  for (let i = topUnits.length - 1; i >= 0 && !(tailNote && lastWork); i -= 1) {
    const u = topUnits[i];
    if (!lastWork && u.kind === 'process' && u.steps.length > 0) lastWork = u;
    if (!tailNote && u.kind === 'note' && u !== alignText && view.units.indexOf(u) > firstWork) tailNote = u;
  }
  // Once there is a result, what needs the user leads it (right after the divider);
  // until then it stays where it arrived: with the DELIVER row.
  let attentionPending = !!attentionCard && !showResult;

  type Placed = { key: string; el: React.ReactNode; lane: boolean };
  const renderItem = (item: WorkItem, prev: WorkItem | undefined, inStage: boolean): Placed | null => {
    const narration = item.kind === 'note' || item.kind === 'landmark';
    const gap = !prev ? '' : narration && prev.kind === 'group' ? 'mt-1' : 'mt-2';
    if (item.kind === 'group') {
      return {
        key: `g-${item.id}`,
        lane: true,
        el: (
          <div key={`g-${item.id}`} className={gap} {...ANCHOR_PROPS}>
            <WorkGroupView
              scope={memoryScope}
              group={item}
              tail={!inStage && tailNote && item.members.includes(tailNote) ? tailNote : undefined}
              defaultOpen={!inStage && noResult && !tailNote && !!lastWork && item.members.includes(lastWork)}
              expanded={processChoice.get(item.id)}
              flat={inStage}
              onToggle={(open) => chooseProcess(item.id, open)}
              thoughtOpen={(k) => processChoice.get(k)}
              onThoughtToggle={chooseProcess}
              renderNote={(text, k) => renderBlock({ type: 'text', text }, k)}
            />
          </div>
        ),
      };
    }
    if (item.kind === 'stage') return null; // rendered as its section's header
    if (item.kind === 'interrupt') {
      const key = `i-${item.index}`;
      return { key, lane: false, el: <div key={key} className="mt-3" {...ANCHOR_PROPS}>{renderBlock(item.block, `ib-${item.index}`)}</div> };
    }
    // A first-level text: the restatement, the answer-so-far / result, or a note / landmark
    // between blocks. Keyed by its text ordinal, which a stored copy replacing the live
    // message (tool_result blocks dropped) does not shift.
    const isAlignment = item === alignText;
    const isResult = showResult && item === lastUnit;
    const inLane = !isAlignment && item.kind !== 'outcome';
    const key = `tx-${item.textOrdinal}`;
    return {
      key,
      lane: inLane,
      el: (
        <div
          key={key}
          {...ANCHOR_PROPS}
          data-testid={isAlignment ? 'turn-restatement' : isResult ? 'turn-result' : undefined}
          className={`${inLane ? gap : prev ? 'mt-3' : ''} ${inLane ? 'text-[12.5px] [&_p]:text-[12.5px] [&_li]:text-[12.5px] [&_p]:text-[var(--color-text-secondary)] [&_li]:text-[var(--color-text-secondary)]' : ''}`}
        >
          {isResult && (
            <div className="flex items-center gap-2.5 mt-5 mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.result', 'Result')}
              <span className="flex-1 border-t border-[var(--color-border)]" />
            </div>
          )}
          {isResult && attentionCard && <div className="mb-3">{attentionCard}</div>}
          {renderBlock({ type: 'text', text: item.text }, `${key}-b`)}
        </div>
      ),
    };
  };
  const renderItems = (items: WorkItem[], inStage = false): Placed[] =>
    items.map((it, i) => renderItem(it, items[i - 1], inStage)).filter((x): x is Placed => !!x);

  const placed: Placed[] = [];
  let buffer: TurnUnit[] = [];
  const flush = (isLast: boolean) => {
    if (buffer.length) placed.push(...renderItems(groupWork(buffer, !!isStreaming && isLast, { exclude: alignText, mergeLead, holdLiveAboveAnswer })));
    buffer = [];
  };
  // A pipeline turn: everything between the leading text and the trailing answer is
  // ONE PipelineBlock — a special WORKED whose header is the run's checklist
  // (chat display v8). Its lines are the plan's stages and gates (pipelineChecklist);
  // a stage that ran again is the same line, its work split by round.
  const firstRow = sections.findIndex((s) => s.kind !== 'unit');
  const lastRow = sections.reduce((at, s, k) => (s.kind === 'unit' ? at : k), -1);
  const lines = firstRow >= 0 ? pipelineChecklist(sections, view.run) : [];
  const indexOf = new Map(sections.map((sec, k) => [sec, k] as const));
  // Units between two rows (rare: older end-based sessions) stay with the row before.
  const between = new Map<number, TurnUnit[]>();
  let lastSeen = -1;
  sections.forEach((sec, k) => {
    if (k < firstRow || k > lastRow) return;
    if (sec.kind === 'unit') { between.set(lastSeen, [...(between.get(lastSeen) ?? []), sec.unit]); return; }
    lastSeen = k;
  });
  type RowSec = Exclude<(typeof sections)[number], { kind: 'unit' }>;
  const childrenOf = (sec: RowSec): TurnUnit[] => [...sec.children, ...(between.get(indexOf.get(sec) ?? -1) ?? [])];
  const isLiveSec = (sec: RowSec) => {
    const k = indexOf.get(sec) ?? -1;
    return !!isStreaming && k === lastSection && onlyTailAfter(k);
  };
  const roundBody = (sec: RowSec) => {
    const children = childrenOf(sec);
    const liveSec = isLiveSec(sec);
    // Inside a stage (a lane item) a question / permission / the answer-so-far is
    // still not process: it is reset to the answer's size and contrast.
    return children.length || liveSec
      ? renderItems(groupWork(children, liveSec, { exclude: alignText, mergeLead, holdLiveAboveAnswer, joined: leadThoughts }), true).map((x) => (x.lane ? x.el : (
        <div key={`full-${x.key}`} className={FULL_CLASS}>{x.el}</div>
      )))
      : [];
  };
  sections.forEach((sec, k) => { if (sec.kind === 'unit' && k < firstRow) buffer.push(sec.unit); });
  if (firstRow < 0) sections.forEach((sec) => { if (sec.kind === 'unit') buffer.push(sec.unit); });
  flush(firstRow < 0);
  if (firstRow >= 0) {
    const allWork = lines.flatMap((l) => l.rounds.flatMap(childrenOf));
    const lineEls = lines.map((line) => {
      const key = `line-${line.stage}`;
      const wrap = (el: React.ReactNode) => (
        <div key={key} data-testid="checklist-line" data-stage={line.stage} data-status={line.status} {...ANCHOR_PROPS}>{el}</div>
      );
      if (!line.rounds.length) {
        return wrap(line.earlier || (line.status === 'done')
          ? <PipelineStageRow earlier={line.stage} onChoose={() => {}} />
          : <PipelineStageRow pending={line.stage} onChoose={() => {}} />);
      }
      const last = line.rounds[line.rounds.length - 1];
      const children = line.rounds.flatMap(childrenOf);
      const live = isLiveSec(last);
      const rounds = line.rounds.map((sec, r) => {
        const body = roundBody(sec);
        // Every round is the same wrapper from its first frame, so when a second round
        // starts the first round's work stays the same elements (only a label appears).
        const multi = line.rounds.length > 1;
        return (
          <div key={`r${r}`} data-testid={multi ? 'stage-round' : undefined} className={r > 0 ? 'mt-1.5' : ''}>
            {multi ? <div className="text-[11px] text-[var(--color-text-dim)]">{tf(t, 'chat.turn.stage.round', 'round {{n}}', { n: r + 1 })}</div> : null}
            {body}
          </div>
        );
      });
      const gates = line.extraGates.map((g) => (
        <PipelineStageRow key={`xg-${g.block.stage}`} block={g.block} onChoose={() => {}} />
      ));
      const body = [...gates, ...rounds];
      const choiceKey = `sec-${line.stage}`;
      return wrap(
        <PipelineStageRow
          block={last.kind === 'stage' ? last.block : undefined}
          live={last.kind === 'liveStage' ? last.stage : undefined}
          position={last.kind === 'liveStage' ? last.position : undefined}
          running={live}
          round={line.rounds.length > 1 && !line.gate ? line.rounds.length : undefined}
          // A line that ran several rounds says its LATEST round's time (its label says
          // "round N"); spanning first start → last end would count the stages between.
          startedAt={last.kind === 'stage' ? (last.startedAt ?? last.prevAt ?? message.timestamp) : undefined}
          counts={stageCounts(children)}
          failed={children.some((u) => u.kind === 'process' && !!u.unrecoveredFailure)}
          mustOpen={children.some((u) => u.kind === 'interrupt' && isUnanswered(u.block))}
          defaultOpen={noResult && indexOf.get(last) === lastSection}
          choice={processChoice.get(choiceKey)}
          onChoose={(open) => chooseProcess(choiceKey, open)}
        >
          {line.gate && !gates.length && !children.length ? null : body}
        </PipelineStageRow>,
      );
    });
    // The block is live until the answer starts AND the plan is through (it then folds
    // FOLD_DELAY_MS later): text after a stage's end row mid-run is narration the next
    // call will turn into a note — it must not fold the checklist and re-open it. A turn
    // that stops mid-plan keeps the block open above its answer until the stream ends,
    // as a chat turn's WORKED block does (groupWork holdLiveAboveAnswer); its clock stops
    // when the answer starts.
    const answerStarted = sections.slice(lastRow + 1).some((s) => s.kind === 'unit' && s.unit.kind === 'outcome');
    const planDone = lines.every((l) => l.status === 'done' || l.status === 'skipped');
    // The block's time: earliest start to latest end over every round, by time — a
    // re-entered stage can end after a later line in the plan.
    const rounds = lines.flatMap((l) => l.rounds).filter((r): r is Extract<RowSec, { kind: 'stage' }> => r.kind === 'stage');
    const ms = (iso?: string) => (iso ? Date.parse(iso) : NaN);
    const starts = rounds.map((r) => ms(r.startedAt ?? r.prevAt ?? message.timestamp)).filter(Number.isFinite);
    const endMs = rounds.filter((r) => r.block.status !== 'in_progress').map((r) => ms(r.block.at)).filter(Number.isFinite);
    const startedAt = starts.length ? new Date(Math.min(...starts)).toISOString() : message.timestamp;
    const endedAt = endMs.length ? new Date(Math.max(...endMs)).toISOString() : undefined;
    // One pipeline block per turn, keyed without the run id: a run block that arrives
    // after the first stage row must not remount the block.
    const blockKey = 'pipeline';
    placed.push({
      key: blockKey,
      lane: true,
      el: (
        <div key={blockKey} {...ANCHOR_PROPS}>
          <PipelineBlock
            lines={lines}
            live={!!isStreaming && !(answerStarted && planDone)}
            stoppedHere={noResult}
            startedAt={startedAt}
            endedAt={endedAt}
            files={stageCounts(allWork).files}
            choice={processChoice.get(blockKey)}
            onChoose={(open) => chooseProcess(blockKey, open)}
            mustOpen={allWork.some((u) => u.kind === 'interrupt' && isUnanswered(u.block))}
            clockStopped={answerStarted}
          >
            {lineEls}
          </PipelineBlock>
        </div>
      ),
    });
    if (attentionPending && lines.some((l) => l.stage === 'deliver' && l.rounds.length)) {
      attentionPending = false;
      placed.push({ key: 'attention-deliver', lane: false, el: <div key="attention-deliver" className="mt-2">{attentionCard}</div> });
    }
    sections.forEach((sec, k) => { if (sec.kind === 'unit' && k > lastRow) buffer.push(sec.unit); });
  }
  flush(true);

  // The lane is a class on each item, never a wrapping parent: an item that changes
  // lane (a first-level text that becomes a lane item, or the reverse) keeps its key
  // and its parent; only its class changes. The answer-so-far stays first level, so
  // the final answer never moves when the turn ends; a rare note between tool calls
  // moves into the (still open) block when the next call arrives. `flow-root` keeps the items' own margins
  // inside the rule, so consecutive lane items read as one line.
  const contentBlocks: React.ReactNode[] = placed.map((x) => (
    <div
      key={x.key}
      data-lane={x.lane ? 'true' : undefined}
      className={x.lane ? LANE_CLASS : undefined}
    >
      {x.el}
    </div>
  ));
  if (attentionPending) {
    contentBlocks.push(<div key="attention" className="mt-3">{attentionCard}</div>);
  }
  // A finished run's summary: its verdict and four documents, after the outcome.
  // A document it lists is not listed again in the Produced card.
  const summaryDocs = view.summary && !isStreaming ? view.summary.docs : [];
  const deliverables = summaryDocs.length
    ? view.deliverables.filter((d) => !summaryDocs.some((x) => x.absolutePath === d.absolutePath))
    : view.deliverables;
  if (view.summary && !isStreaming) {
    contentBlocks.push(<div key="pipeline-summary" className="mt-4"><PipelineSummaryCard block={view.summary} /></div>);
  }
  if (changes) {
    contentBlocks.push(<div key="turn-changes" className="mt-4"><ChangesCard block={changes} /></div>);
  }
  if (deliverables.length) {
    contentBlocks.push(<div key="deliverables" className="mt-4"><DeliverablesCard files={deliverables} /></div>);
  }
  if (turnFiles.length) {
    contentBlocks.push(
      <div key="turn-files" data-testid="turn-files" className="mt-2 flex flex-wrap items-center gap-1.5 text-[12.5px] text-[var(--color-text-muted)]">
        <span>{tf(t, 'chat.turn.thisTurnChanged', 'This turn changed')}</span>
        {turnFiles.map((p) => <FileChip key={p} path={p} change="edited" />)}
      </div>,
    );
  }

  const copiedLabel = tf(t, 'chat.message.copied', 'Copied!');
  const saveTitle = tf(t, 'chat.message.saveTitle', 'Save to Memory');
  const savedLabel = tf(t, 'chat.message.saved', 'Saved!');
  const savingLabel = tf(t, 'chat.message.saving', 'Saving...');

  return (
    <ReadOnlyContext.Provider value={!!readOnly}>
    <div className="group/msg min-w-0 flex gap-2.5 items-start">
      {/* Agent avatar — 24px bronze gradient circle matching mockup & nav logo */}
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
      <div className="flex-1 min-w-0">
        <AssistantHeader
        timestamp={message.timestamp}
        isStreaming={isStreaming}
      />

      {message.isError ? (
        <div
          className="border border-amber-500/60 bg-amber-500/10 rounded-lg p-3"
          role="alert"
          aria-label={tf(t, 'chat.message.interrupted', 'Connection interrupted')}
        >
          <div>{contentBlocks}</div>
        </div>
      ) : (
        <div className="text-[var(--color-text-secondary)]">
          {contentBlocks}
          {/* Streaming indicator — the bee at the end of the streaming content
              (flying while content arrives, resting after a quiet spell). */}
          {isStreaming && <StreamingBee resting={!!isResting} />}
          {/* A running sub-agent shows on its Agent strip (WorkGroup ToolStrip,
              fed by subagent_progress) — no separate timer banner. */}
        </div>
      )}

      {/* Next steps — the reply's trailing ```next-steps block as clickable chips.
          Only on the latest answer (older ones are stale) and never in read-only. */}
      {!readOnly && !isStreaming && isLastAssistant && <NextSteps steps={view.nextSteps} />}

      {/* Action buttons — appear on hover below content, hidden while streaming.
          Hidden entirely in read-only (History preview) — Copy/Save/Compact/
          Continue are all side-effecting controls that don't belong there. */}
      {!readOnly && !isStreaming && allText.length > 0 && (
        <div className="opacity-0 group-hover/msg:opacity-100 transition-opacity mt-1 flex items-center gap-2">
          {/* Copy button (unchanged) */}
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1 px-2 py-0.5 text-xs text-[var(--color-text-muted)]
                       hover:text-[var(--color-text)] rounded transition-colors"
            title={copied ? copiedLabel : tf(t, 'chat.message.copyTitle', 'Copy message')}
          >
            <span className="material-symbols-outlined text-sm">
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
                'flex items-center gap-1 px-2 py-0.5 text-xs rounded transition-colors',
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
                'material-symbols-outlined text-sm',
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
                'flex items-center gap-1 px-2 py-0.5 text-xs rounded transition-colors',
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
                'material-symbols-outlined text-sm',
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
              className="flex items-center gap-1 px-2 py-0.5 text-xs text-[var(--color-text-muted)]
                         hover:text-[var(--color-accent,var(--color-primary))] rounded transition-colors"
              title={tf(t, 'chat.message.continueTitle', 'Continue — ask {{name}} to continue its response', { name: ASSISTANT_NAME })}
              aria-label={tf(t, 'chat.message.continueAria', 'Continue response')}
              data-testid="continue-button"
            >
              <span className="material-symbols-outlined text-sm">
                play_arrow
              </span>
              {tf(t, 'chat.message.continue', 'Continue')}
            </button>
          )}
        </div>
      )}

    </div>
    </div>
    </ReadOnlyContext.Provider>
  );
};

export default AssistantMessageView;
